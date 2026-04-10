const teamProfile = window.__TEAM_PROFILE__ || {};

const byId = (id) => document.getElementById(id);

let routeSpeechEnabled = true;
let lastSpokenNavigationKey = "";
let lastRouteSpeechText = "";
let latestNavigationPoints = [];
let latestNavigationServices = [];
let lastManualStartPointId = "";
let lastManualDestinationPointId = "";
let lastManualServiceId = "";
let latestNavigationState = null;
let latestStateRenderToken = 0;
let navigationMutationInFlight = false;

const navigationDraft = {
  initialized: false,
  startPointId: "",
  destinationPointId: "",
  manualDestinationPointId: "",
  serviceId: "",
  waypointPointIds: [],
};

async function postJson(url, payload, options = {}) {
  const { refreshAfter = true } = options;
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  byId("lastResponse").textContent = JSON.stringify(data, null, 2);
  if (!response.ok) {
    throw new Error(data.detail ? JSON.stringify(data.detail) : "Request failed");
  }
  if (refreshAfter) {
    try {
      await refreshState();
    } catch (error) {
      console.error("Failed to refresh state after POST", error);
    }
  }
  return data;
}

function renderList(targetId, items, formatter) {
  const container = byId(targetId);
  if (!items || !items.length) {
    container.innerHTML = '<div class="stack-item"><span class="small">Пока пусто.</span></div>';
    return;
  }

  container.innerHTML = items
    .map((item) => `<div class="stack-item">${formatter(item)}</div>`)
    .join("");
}

function renderObjectEntries(targetId, payload, formatter) {
  const entries = Object.entries(payload || {});
  renderList(targetId, entries, ([key, value]) => formatter(key, value));
}

function issueStateRenderToken() {
  latestStateRenderToken += 1;
  return latestStateRenderToken;
}

function formatSeconds(value) {
  if (value === null || value === undefined) return "-";
  if (value < 60) return `${Math.round(value)} c`;
  return `${Math.round(value / 60)} мин`;
}

function formatNavigationStatus(status) {
  switch (status) {
    case "awaiting_confirmation":
      return "Ожидаем подтверждение";
    case "blocked":
      return "Маршрут заблокирован";
    case "completed":
      return "Маршрут завершён";
    case "not_configured":
      return "Точки не настроены";
    case "idle":
    default:
      return "Маршрут не запущен";
  }
}

function formatConfirmationMethod(method) {
  switch (method) {
    case "rfid":
      return "RFID-метка";
    case "face":
      return "камера";
    default:
      return method || "-";
  }
}

function formatConfirmationMethods(point) {
  const methods = point?.confirmation_methods || [];
  if (!methods.length) {
    return "Способ подтверждения не настроен.";
  }
  return `Подтверждение: ${methods.map(formatConfirmationMethod).join(" или ")}.`;
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }

  return parsed.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatLastConfirmation(confirmation) {
  if (!confirmation) {
    return "-";
  }

  const source = confirmation.type === "face" ? "камера" : "RFID";
  const pointName = confirmation.point_name || confirmation.point_id || "точка";
  const timeText = formatTimestamp(confirmation.timestamp);
  return timeText
    ? `${pointName}: ${source}, ${timeText}`
    : `${pointName}: ${source}`;
}

function normalizeDeviceIds(value) {
  const values = Array.isArray(value) ? value : [value];
  return values.filter((item) => item !== undefined && item !== null && item !== "");
}

function formatCameraVerificationStatus(navigation) {
  const currentPoint = navigation?.current_point;
  const methods = currentPoint?.confirmation_methods || [];
  const lastConfirmation = navigation?.last_confirmation;

  if (!navigation?.route?.length) {
    return "Камера пока не подтверждала точки.";
  }

  if (navigation.status === "completed") {
    return lastConfirmation?.type === "face"
      ? `Последняя камера подтвердила ${lastConfirmation.point_name || lastConfirmation.point_id}.`
      : "Маршрут завершён. Камера готова к следующему маршруту.";
  }

  if (!navigation.active || !currentPoint) {
    return "Камера будет доступна после построения маршрута.";
  }

  if (!methods.includes("face")) {
    return "Для текущей точки камера не выбрана, используйте RFID.";
  }

  const faceDeviceIds = normalizeDeviceIds(
    currentPoint.confirmation?.face_device_ids || currentPoint.confirmation?.face_device_id,
  );
  const deviceText = !faceDeviceIds.length
    ? "любой ESP32-CAM с распознанным лицом"
    : `ESP32-CAM device_id=${faceDeviceIds.join(" или ")}`;

  if (lastConfirmation?.type === "face") {
    return `Камера работает: последняя TYPE 6 была с device_id=${lastConfirmation.device_id}. Сейчас ждём ${deviceText}.`;
  }

  return `Камера доступна: отправьте кадр на текущую точку через ${deviceText}.`;
}

function renderNavigationProgress(navigation = {}) {
  const route = navigation.route || [];
  const totalSteps = route.length;
  const confirmedSteps = navigation.status === "completed"
    ? totalSteps
    : route.filter((step) => step.status === "confirmed").length;
  const progressPercent = totalSteps
    ? Math.min(100, Math.round((confirmedSteps / totalSteps) * 100))
    : 0;
  const currentPoint = navigation.current_point;
  const currentName = currentPoint?.name || "-";
  const currentInstruction = navigation.current_step?.instruction || "";

  byId("navigationProgressScore").textContent = `${confirmedSteps}/${totalSteps}`;
  byId("navigationProgressBar").style.width = `${progressPercent}%`;
  byId("navigationLastConfirmation").textContent = formatLastConfirmation(navigation.last_confirmation);
  byId("navigationCameraStatus").textContent = formatCameraVerificationStatus(navigation);

  if (!totalSteps) {
    byId("navigationProgressTitle").textContent = "Маршрут не запущен";
    byId("navigationProgressDetails").textContent = "Постройте маршрут, чтобы видеть прогресс по точкам.";
    byId("navigationProgressCurrentPoint").textContent = "-";
    byId("navigationProgressCurrentMethods").textContent = "Ожидаем построения маршрута.";
    return;
  }

  if (navigation.status === "completed") {
    byId("navigationProgressTitle").textContent = "Маршрут завершён";
    byId("navigationProgressDetails").textContent = `Подтверждены все шаги: ${totalSteps} из ${totalSteps}.`;
    byId("navigationProgressCurrentPoint").textContent = navigation.destination?.name || currentName;
    byId("navigationProgressCurrentMethods").textContent = "Все точки маршрута пройдены.";
    return;
  }

  if (navigation.status === "blocked") {
    byId("navigationProgressTitle").textContent = "Маршрут требует перестроения";
    byId("navigationProgressDetails").textContent = "На пути есть препятствие. После перестроения здесь появится новая текущая цель.";
  } else {
    byId("navigationProgressTitle").textContent = `Сейчас идём к: ${currentName}`;
    byId("navigationProgressDetails").textContent = [
      `Пройдено шагов: ${confirmedSteps} из ${totalSteps}.`,
      currentInstruction,
    ].filter(Boolean).join(" ");
  }

  byId("navigationProgressCurrentPoint").textContent = currentName;
  byId("navigationProgressCurrentMethods").textContent = [
    formatConfirmationMethods(currentPoint),
    currentPoint ? `Ориентир: ${formatPointSignal(currentPoint)}.` : null,
  ].filter(Boolean).join(" ");
}

function getSelectedWaypointPointIds() {
  return Array.from(
    document.querySelectorAll('#navigationWaypoints input[type="checkbox"]:checked'),
  ).map((input) => input.value);
}

function getSelectedService() {
  const serviceId = byId("navigationService").value;
  return latestNavigationServices.find((service) => service.service_id === serviceId) || null;
}

function getServiceById(serviceId) {
  return latestNavigationServices.find((service) => service.service_id === serviceId) || null;
}

function getEffectiveDestinationPointId() {
  return getSelectedService()?.destination_point_id || byId("navigationDestination").value;
}

function syncNavigationDestinationFromService() {
  const destinationSelect = byId("navigationDestination");
  const selectedService = getSelectedService();

  if (selectedService) {
    destinationSelect.value = selectedService.destination_point_id;
    navigationDraft.destinationPointId = selectedService.destination_point_id;
    destinationSelect.disabled = true;
    return;
  }

  destinationSelect.disabled = false;
  if (
    navigationDraft.destinationPointId
    && Array.from(destinationSelect.options).some(
      (option) => option.value === navigationDraft.destinationPointId,
    )
  ) {
    destinationSelect.value = navigationDraft.destinationPointId;
  }
}

function refreshWaypointPicker(preferredWaypointIds = null) {
  const container = byId("navigationWaypoints");
  const hint = byId("navigationWaypointsHint");
  const startPointId = byId("navigationStartPoint").value;
  const destinationPointId = getEffectiveDestinationPointId();
  const selectedIds = new Set(preferredWaypointIds ?? getSelectedWaypointPointIds());
  const availablePoints = latestNavigationPoints.filter((point) => (
    point.point_id !== startPointId && point.point_id !== destinationPointId
  ));

  if (!availablePoints.length) {
    container.innerHTML = '<div class="waypoint-option waypoint-option--empty">Дополнительные точки сейчас недоступны.</div>';
    hint.textContent = "Стартовая и конечная точки скрыты автоматически.";
    return;
  }

  container.innerHTML = availablePoints.map((point) => `
    <label class="waypoint-option">
      <input
        type="checkbox"
        value="${point.point_id}"
        ${selectedIds.has(point.point_id) ? "checked" : ""}
      />
      <span>
        <strong>${point.name}</strong>
        <span class="small">${point.point_id}</span>
      </span>
    </label>
  `).join("");

  const activeCount = Array.from(selectedIds).filter((pointId) => (
    availablePoints.some((point) => point.point_id === pointId)
  )).length;
  hint.textContent = activeCount
    ? `Выбрано промежуточных точек: ${activeCount}.`
    : "Можно выбрать одну или несколько промежуточных точек.";
}

function setServiceOptions(selectId, services, preferredValue) {
  const select = byId(selectId);
  const serviceList = services || [];
  const currentValue = select.value;
  latestNavigationServices = serviceList;

  if (!serviceList.length) {
    select.innerHTML = '<option value="">Услуги не настроены</option>';
    select.disabled = true;
    return;
  }

  select.innerHTML = [
    '<option value="">Без услуги МФЦ</option>',
    ...serviceList.map((service) => (
      `<option value="${service.service_id}">${service.service_name}</option>`
    )),
  ].join("");
  select.value = serviceList.some((service) => service.service_id === preferredValue)
    ? preferredValue
    : serviceList.some((service) => service.service_id === currentValue)
      ? currentValue
      : "";
  select.disabled = false;
}

function formatPointSignal(point) {
  if (!point) {
    return "-";
  }

  if (point.device_type === "type2" && point.color) {
    const color = point.color;
    return `device_id=${point.device_id}, RGB=(${color.r}, ${color.g}, ${color.b})`;
  }

  return `device_id=${point.device_id}, frequency=${point.frequency_hz} Hz, duration=${point.duration_ms} ms`;
}

function setPointOptions(selectId, points, preferredValue, excludedPointId = null) {
  const select = byId(selectId);
  const currentValue = select.value;
  const availablePoints = excludedPointId
    ? points.filter((point) => point.point_id !== excludedPointId)
    : points.slice();

  if (!availablePoints.length) {
    select.innerHTML = '<option value="">Нет доступных точек</option>';
    select.disabled = true;
    return;
  }

  select.innerHTML = availablePoints
    .map((point) => `<option value="${point.point_id}">${point.name}</option>`)
    .join("");

  const resolvedValue = availablePoints.some((point) => point.point_id === preferredValue)
    ? preferredValue
    : availablePoints.some((point) => point.point_id === currentValue)
      ? currentValue
      : availablePoints[0].point_id;

  select.value = resolvedValue;
  select.disabled = false;
}

function hasPoint(points, pointId) {
  return Boolean(pointId) && points.some((point) => point.point_id === pointId);
}

function hasService(services, serviceId) {
  return Boolean(serviceId) && services.some((service) => service.service_id === serviceId);
}

function sanitizeNavigationDraft(navigation = {}) {
  const points = navigation.points || [];
  const services = navigation.available_services || [];
  const serverStartPointId = navigation.start_point?.point_id || points[0]?.point_id || "";
  const serverDestinationPointId = navigation.destination?.point_id || "";
  const serverWaypointPointIds = (navigation.waypoints || []).map((point) => point.point_id);

  if (!navigationDraft.initialized) {
    navigationDraft.startPointId = serverStartPointId;
    navigationDraft.destinationPointId = serverDestinationPointId;
    navigationDraft.serviceId = navigation.service?.service_id || "";
    navigationDraft.waypointPointIds = serverWaypointPointIds;
    navigationDraft.initialized = true;
  } else if (navigation.active) {
    navigationDraft.startPointId = serverStartPointId || navigationDraft.startPointId;
    navigationDraft.destinationPointId = serverDestinationPointId || navigationDraft.destinationPointId;
    navigationDraft.serviceId = navigation.service?.service_id || "";
    navigationDraft.waypointPointIds = serverWaypointPointIds;
  }

  if (!hasService(services, navigationDraft.serviceId)) {
    navigationDraft.serviceId = "";
  }

  if (!hasPoint(points, navigationDraft.startPointId)) {
    navigationDraft.startPointId = serverStartPointId || points[0]?.point_id || "";
  }

  const destinationPoints = points.filter(
    (point) => point.point_id !== navigationDraft.startPointId,
  );
  const selectedService = services.find(
    (service) => service.service_id === navigationDraft.serviceId,
  ) || null;
  const preferredDestinationPointId = selectedService?.destination_point_id
    || navigationDraft.destinationPointId
    || serverDestinationPointId;

  if (destinationPoints.some((point) => point.point_id === preferredDestinationPointId)) {
    navigationDraft.destinationPointId = preferredDestinationPointId;
  } else {
    navigationDraft.destinationPointId = destinationPoints[0]?.point_id || "";
  }

  const excludedPointIds = new Set([
    navigationDraft.startPointId,
    navigationDraft.destinationPointId,
  ]);
  const preferredWaypointPointIds = navigation.active
    ? serverWaypointPointIds
    : navigationDraft.waypointPointIds;

  navigationDraft.waypointPointIds = preferredWaypointPointIds.filter((pointId, index, pointIds) => (
    pointIds.indexOf(pointId) === index
    && hasPoint(points, pointId)
    && !excludedPointIds.has(pointId)
  ));
}

function renderNavigationForm(navigation = {}) {
  latestNavigationPoints = navigation.points || [];
  latestNavigationServices = navigation.available_services || [];

  sanitizeNavigationDraft(navigation);

  setPointOptions("navigationStartPoint", latestNavigationPoints, navigationDraft.startPointId);
  setPointOptions(
    "navigationDestination",
    latestNavigationPoints,
    navigationDraft.destinationPointId,
    byId("navigationStartPoint").value,
  );
  setServiceOptions(
    "navigationService",
    latestNavigationServices,
    navigationDraft.serviceId,
  );

  syncNavigationDestinationFromService();
  refreshWaypointPicker(navigationDraft.waypointPointIds);

  navigationDraft.startPointId = byId("navigationStartPoint").value;
  navigationDraft.destinationPointId = byId("navigationDestination").value;
  navigationDraft.serviceId = byId("navigationService").value;
  navigationDraft.waypointPointIds = getSelectedWaypointPointIds();
}

function rememberNavigationDraftFromForm() {
  navigationDraft.initialized = true;
  navigationDraft.startPointId = byId("navigationStartPoint").value;
  navigationDraft.serviceId = byId("navigationService").value;
  if (navigationDraft.serviceId) {
    navigationDraft.destinationPointId = getServiceById(navigationDraft.serviceId)?.destination_point_id
      || byId("navigationDestination").value;
  } else {
    navigationDraft.destinationPointId = byId("navigationDestination").value;
    navigationDraft.manualDestinationPointId = navigationDraft.destinationPointId;
  }
  navigationDraft.waypointPointIds = getSelectedWaypointPointIds();
}

function getNavigationDestinationOptions(startPointId) {
  return latestNavigationPoints.filter((point) => point.point_id !== startPointId);
}

function getNavigationWaypointOptions(startPointId, destinationPointId) {
  return latestNavigationPoints.filter((point) => (
    point.point_id !== startPointId && point.point_id !== destinationPointId
  ));
}

function initializeNavigationDraftState(navigation = {}) {
  const points = navigation.points || latestNavigationPoints;
  const services = navigation.available_services || latestNavigationServices;
  const fallbackStartPointId = navigation.start_point?.point_id || points[0]?.point_id || "";
  const fallbackDestinationPointId = points.find(
    (point) => point.point_id !== fallbackStartPointId,
  )?.point_id || "";

  if (!navigationDraft.initialized) {
    navigationDraft.initialized = true;
    navigationDraft.startPointId = fallbackStartPointId;
    navigationDraft.destinationPointId = navigation.destination?.point_id || fallbackDestinationPointId;
    navigationDraft.manualDestinationPointId = navigationDraft.destinationPointId;
    navigationDraft.serviceId = navigation.service?.service_id || "";
    navigationDraft.waypointPointIds = (navigation.waypoints || []).map((point) => point.point_id);
  }

  if (!hasPoint(points, navigationDraft.startPointId)) {
    navigationDraft.startPointId = fallbackStartPointId;
  }

  if (!hasService(services, navigationDraft.serviceId)) {
    navigationDraft.serviceId = "";
  }

  const destinationOptions = getNavigationDestinationOptions(navigationDraft.startPointId);
  if (!destinationOptions.some((point) => point.point_id === navigationDraft.manualDestinationPointId)) {
    navigationDraft.manualDestinationPointId = destinationOptions[0]?.point_id || "";
  }

  if (navigationDraft.serviceId) {
    const serviceDestinationPointId = getServiceById(navigationDraft.serviceId)?.destination_point_id || "";
    navigationDraft.destinationPointId = destinationOptions.some(
      (point) => point.point_id === serviceDestinationPointId,
    )
      ? serviceDestinationPointId
      : destinationOptions[0]?.point_id || "";
  } else {
    navigationDraft.destinationPointId = destinationOptions.some(
      (point) => point.point_id === navigationDraft.manualDestinationPointId,
    )
      ? navigationDraft.manualDestinationPointId
      : destinationOptions[0]?.point_id || "";
    navigationDraft.manualDestinationPointId = navigationDraft.destinationPointId;
  }

  const allowedWaypointIds = new Set(
    getNavigationWaypointOptions(
      navigationDraft.startPointId,
      navigationDraft.destinationPointId,
    ).map((point) => point.point_id),
  );
  navigationDraft.waypointPointIds = navigationDraft.waypointPointIds.filter((pointId, index, pointIds) => (
    pointIds.indexOf(pointId) === index && allowedWaypointIds.has(pointId)
  ));
}

function renderNavigationDraftSummary() {
  const summaryNode = byId("navigationDraftSummary");
  if (!summaryNode) {
    return;
  }

  if (!latestNavigationPoints.length) {
    summaryNode.textContent = "Навигационные точки загружаются.";
    return;
  }

  const startName = latestNavigationPoints.find(
    (point) => point.point_id === navigationDraft.startPointId,
  )?.name || "-";
  const destinationName = latestNavigationPoints.find(
    (point) => point.point_id === navigationDraft.destinationPointId,
  )?.name || "-";
  const serviceName = getServiceById(navigationDraft.serviceId)?.service_name;

  summaryNode.textContent = [
    `Старт: ${startName}`,
    `Финиш: ${destinationName}`,
    serviceName ? `Услуга: ${serviceName}` : null,
    navigationDraft.waypointPointIds.length
      ? `Промежуточных точек: ${navigationDraft.waypointPointIds.length}`
      : "Без промежуточных точек",
  ].filter(Boolean).join(" • ");
}

function renderNavigationBuilder(navigation = {}) {
  latestNavigationPoints = navigation.points || latestNavigationPoints;
  latestNavigationServices = navigation.available_services || latestNavigationServices;

  initializeNavigationDraftState(navigation);

  setPointOptions("navigationStartPoint", latestNavigationPoints, navigationDraft.startPointId);
  navigationDraft.startPointId = byId("navigationStartPoint").value;

  const destinationOptions = getNavigationDestinationOptions(navigationDraft.startPointId);
  setPointOptions("navigationDestination", destinationOptions, navigationDraft.destinationPointId);
  navigationDraft.destinationPointId = byId("navigationDestination").value;

  setServiceOptions("navigationService", latestNavigationServices, navigationDraft.serviceId);
  navigationDraft.serviceId = byId("navigationService").value;

  if (navigationDraft.serviceId) {
    const serviceDestinationPointId = getServiceById(navigationDraft.serviceId)?.destination_point_id
      || navigationDraft.destinationPointId;
    setPointOptions("navigationDestination", destinationOptions, serviceDestinationPointId);
    byId("navigationDestination").disabled = true;
    navigationDraft.destinationPointId = byId("navigationDestination").value;
  } else {
    byId("navigationDestination").disabled = false;
    navigationDraft.destinationPointId = byId("navigationDestination").value;
    navigationDraft.manualDestinationPointId = navigationDraft.destinationPointId;
  }

  refreshWaypointPicker(navigationDraft.waypointPointIds);
  navigationDraft.waypointPointIds = getSelectedWaypointPointIds();
  renderNavigationDraftSummary();
}

function resetNavigationDraftState() {
  navigationDraft.initialized = false;
  navigationDraft.startPointId = "";
  navigationDraft.destinationPointId = "";
  navigationDraft.manualDestinationPointId = "";
  navigationDraft.serviceId = "";
  navigationDraft.waypointPointIds = [];
  renderNavigationBuilder(latestNavigationState || {});
}

function setNavigationSubmitState(isBusy) {
  const submitButton = document.querySelector('#navigationForm button[type="submit"]');
  if (!submitButton) {
    return;
  }

  submitButton.disabled = isBusy;
  submitButton.textContent = isBusy ? "Строим..." : "Построить маршрут";
}

function updateSpeechButtons() {
  const toggleButton = byId("toggleRouteSpeech");
  toggleButton.textContent = `Озвучка маршрута: ${routeSpeechEnabled ? "вкл" : "выкл"}`;
  byId("repeatRouteSpeech").disabled = !lastRouteSpeechText;
}

function speakText(text) {
  if (!routeSpeechEnabled || !text || !("speechSynthesis" in window)) {
    return;
  }

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ru-RU";
  utterance.rate = 0.95;
  utterance.pitch = 1;
  window.speechSynthesis.speak(utterance);
}

function maybeSpeakNavigation(navigation) {
  if (!routeSpeechEnabled || !navigation?.message) {
    return;
  }

  if (navigation.status === "idle" || navigation.status === "not_configured") {
    return;
  }

  const speechKey = [
    navigation.status || "",
    navigation.started_at || "",
    navigation.completed_at || "",
    navigation.current_point?.point_id || "",
    navigation.message,
  ].join("|");

  if (speechKey === lastSpokenNavigationKey) {
    return;
  }

  lastSpokenNavigationKey = speechKey;
  lastRouteSpeechText = navigation.message;
  updateSpeechButtons();
  speakText(navigation.message);
}

function renderNavigationPanel(navigation) {
  latestNavigationState = navigation;

  try {
    renderNavigationBuilder(navigation);
  } catch (error) {
    console.error("Navigation builder render failed", error);
    if (byId("navigationDraftSummary")) {
      byId("navigationDraftSummary").textContent = "Форма маршрута временно недоступна, но маршрут ниже отображается.";
    }
  }

  const blockedPoints = navigation.blocked_points || [];
  byId("navigationStatus").textContent = formatNavigationStatus(navigation.status);
  byId("navigationCurrentPoint").textContent = navigation.current_step
    ? `${navigation.current_point?.name || "-"} (${navigation.current_step.connection_type_label})`
    : navigation.current_point?.name || "-";
  byId("navigationDestinationLabel").textContent = navigation.destination?.name || "-";
  byId("navigationMessage").textContent = navigation.message || "Маршрут не запущен.";
  byId("navigationServiceInfo").textContent = navigation.service
    ? `Услуга: ${navigation.service.service_name}. Талон: ${navigation.service.ticket_number}.`
    : "Услуга МФЦ не выбрана.";
  byId("navigationBlockedPoints").textContent = blockedPoints.length
    ? `Перекрытые точки: ${blockedPoints.map((point) => point.name).join(", ")}.`
    : "Перекрытых точек нет.";
  byId("navigationRouteText").textContent = navigation.route_text
    ? `Последовательность маршрута: ${navigation.route_text}.`
    : "Маршрут пока не построен.";
  renderNavigationProgress(navigation);

  const routeContainer = byId("navigationRoute");
  if (!navigation.route?.length) {
    routeContainer.innerHTML = '<div class="stack-item"><span class="small">Маршрут пока не построен.</span></div>';
    return;
  }

  routeContainer.innerHTML = navigation.route
    .map((step, index) => `
      <div class="route-step route-step--${step.status}">
        <div>
          <strong>${index + 1}. ${step.from_point?.name || "-"} -> ${step.to_point?.name || "-"}</strong>
          <span class="small">${step.connection_type_label || "-"}</span>
          <span class="small">${step.instruction || "-"}</span>
          ${step.connection_type === "bus" && step.via_point_ids?.length ? `<span class="small">Промежуточных остановок на кольце: ${step.via_point_ids.length}</span>` : ""}
          <span class="small">${formatPointSignal(step.to_point)}</span>
        </div>
        <span class="route-badge route-badge--${step.status}">
          ${step.status === "confirmed" ? "Пройден" : step.status === "active" ? "Активный шаг" : "Ожидает"}
        </span>
      </div>
    `)
    .join("");
}

function renderNavigation(navigation) {
  renderNavigationPanel(navigation);
}

function renderNavigationMutation(navigation) {
  if (!navigation) {
    return;
  }

  // Invalidate older /api/state responses so they cannot overwrite the fresh route.
  issueStateRenderToken();
  renderNavigationPanel(navigation);
  maybeSpeakNavigation(navigation);
}

async function refreshState(options = {}) {
  const { source = "manual" } = options;
  if (navigationMutationInFlight && source === "interval") {
    return null;
  }

  const requestToken = issueStateRenderToken();
  const response = await fetch("/api/state");
  const state = await response.json();
  if (requestToken !== latestStateRenderToken) {
    return state;
  }

  const collector = state.city.collector || {};

  byId("cityStatus").textContent = state.city.connected ? "Городской сервер доступен" : "Нет связи с городом";
  byId("cityStatus").className = `status-pill ${state.city.connected ? "ok" : "error"}`;
  byId("cityError").textContent = state.city.last_error
    || `Опрос: ${state.city.last_poll_at || "ещё не было"} • snapshot=${collector.snapshots_seen ?? 0}, updates=${collector.updates_seen ?? 0}`;

  const bestEta = state.buses.best_eta_seconds;
  const baselineEta = state.buses.baseline_eta_seconds;
  byId("etaValue").textContent = bestEta === null ? "-" : formatSeconds(bestEta);
  byId("etaBaseline").textContent = baselineEta === null
    ? "Нет базового значения"
    : `Норма: ${formatSeconds(baselineEta)}`;

  byId("queueSize").textContent = state.voice_queue.length;
  byId("vibrationState").textContent = state.vibration.active ? "Активна" : "Неактивна";
  byId("distanceValue").textContent = state.distance
    ? `distance=${state.distance.distance_cm} см, threshold=${state.distance.threshold_cm} см`
    : "Нет данных от датчика расстояния.";

  byId("clothingRecommendation").textContent = state.recommendations.clothing?.text || "Нет данных.";
  byId("trafficRecommendation").textContent = state.recommendations.traffic?.text || "Нет данных.";
  byId("obstacleRecommendation").textContent = state.recommendations.obstacle?.text || "Нет данных.";

  renderNavigationPanel(state.navigation || {});
  maybeSpeakNavigation(state.navigation || {});

  renderObjectEntries("devicesType1", state.devices_type1, (key, value) => `
    <strong>device_id=${key}</strong>
    <span class="small">frequency=${value.frequency_hz ?? "-"} Hz, duration=${value.duration_ms ?? "-"} ms</span>
  `);

  renderObjectEntries("devicesType2", state.devices_type2, (key, value) => `
    <strong>device_id=${key}</strong>
    <span class="small">RGB=${JSON.stringify(value.color || {})}</span>
  `);

  renderList("voiceQueue", state.voice_queue, (item) => `
    <strong>${item.text || item.value || "Сообщение"}</strong>
    <span class="small">${item.timestamp || "-"}</span>
  `);

  renderList("cityUpdates", state.city_updates_preview || [], (item) => `
    <strong>${item.path || "/"}</strong>
    <span class="small">${item.kind || "update"} • ${item.observed_at || "-"}</span>
    <span class="small">${JSON.stringify(item.value, null, 2)}</span>
  `);

  renderList("logs", state.logs.slice(0, 15), (item) => `
    <strong>${item.category}</strong>
    <span class="small">${item.timestamp}</span>
    <span class="small">${JSON.stringify(item.payload || item.vibration || {}, null, 2)}</span>
  `);
}

function formDataToObject(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function numberOrNull(value) {
  return value === "" || value === null || value === undefined ? null : Number(value);
}

byId("resetNavigationForm").textContent = "Сбросить форму";
byId("navigationDraftSummary").textContent = "Навигационные точки загружаются.";

document.getElementById("navigationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  rememberNavigationDraftFromForm();
  navigationMutationInFlight = true;
  setNavigationSubmitState(true);

  try {
    const data = await postJson("/api/navigation/start", {
      start_point_id: navigationDraft.startPointId,
      destination_point_id: navigationDraft.destinationPointId,
      waypoint_point_ids: navigationDraft.waypointPointIds,
      service_id: navigationDraft.serviceId || null,
    }, { refreshAfter: false });

    renderNavigationMutation(data.navigation);
  } catch (error) {
    console.error("Navigation start failed", error);
    byId("navigationMessage").textContent = `Не удалось построить маршрут: ${error.message || error}`;
  } finally {
    navigationMutationInFlight = false;
    setNavigationSubmitState(false);
  }

  refreshState({ source: "post-navigation" }).catch((error) => {
    console.error("Background refresh after navigation start failed", error);
  });
});

document.getElementById("navigationStartPoint").addEventListener("change", () => {
  rememberNavigationDraftFromForm();
  renderNavigationBuilder(latestNavigationState || {});
});

document.getElementById("navigationDestination").addEventListener("change", () => {
  rememberNavigationDraftFromForm();
  renderNavigationBuilder(latestNavigationState || {});
});

document.getElementById("navigationService").addEventListener("change", () => {
  rememberNavigationDraftFromForm();
  renderNavigationBuilder(latestNavigationState || {});
});

document.getElementById("navigationWaypoints").addEventListener("change", () => {
  rememberNavigationDraftFromForm();
  renderNavigationDraftSummary();
});

document.getElementById("resetNavigationForm").addEventListener("click", () => {
  resetNavigationDraftState();
});

document.getElementById("toggleRouteSpeech").addEventListener("click", () => {
  routeSpeechEnabled = !routeSpeechEnabled;
  if (!routeSpeechEnabled && "speechSynthesis" in window) {
    window.speechSynthesis.cancel();
  }
  updateSpeechButtons();
});

document.getElementById("repeatRouteSpeech").addEventListener("click", () => {
  if (!lastRouteSpeechText) {
    return;
  }
  speakText(lastRouteSpeechText);
});

document.getElementById("voiceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 1,
    text: form.text,
    timestamp: Math.floor(Date.now() / 1000),
  });
});

document.getElementById("soundForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 2,
    device_id: Number(form.device_id),
    frequency_hz: Number(form.frequency_hz),
    duration_ms: Number(form.duration_ms),
  });
});

document.getElementById("lightForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 3,
    device_id: Number(form.device_id),
    color: {
      r: Number(form.r),
      g: Number(form.g),
      b: Number(form.b),
    },
  });
});

document.getElementById("rfidForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 4,
    device_id: Number(form.device_id),
    rfid_code: form.rfid_code,
  });
});

document.getElementById("obstacleForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 5,
    location_id: form.location_id,
    obstacle_type: form.obstacle_type,
    reroute_required: event.target.querySelector("[name=reroute_required]").checked,
    message: form.message || null,
  });
});

document.getElementById("faceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/events", {
    type: 6,
    device_id: Number(form.device_id),
    user_id: form.user_id,
    confidence: Number(form.confidence),
  });
});

document.getElementById("environmentForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/sensors/environment", {
    temperature_c: Number(form.temperature_c),
    humidity_percent: Number(form.humidity_percent),
    pressure_hpa: Number(form.pressure_hpa),
    timestamp: Math.floor(Date.now() / 1000),
  });
});

document.getElementById("distanceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/sensors/distance", {
    device_id: Number(form.device_id),
    distance_cm: Number(form.distance_cm),
    threshold_cm: Number(form.threshold_cm),
    bus_detected: event.target.querySelector("[name=bus_detected]").checked,
    timestamp: Math.floor(Date.now() / 1000),
  });
});

document.getElementById("sendDefaultSound").addEventListener("click", async () => {
  const deviceId = numberOrNull(teamProfile.devices?.type1_ids?.[0]) ?? 1;
  await postJson(`/api/actions/default-sound/${deviceId}`, {});
});

document.getElementById("sendDefaultLight").addEventListener("click", async () => {
  const deviceId = numberOrNull(teamProfile.devices?.type2_ids?.[0]);
  if (deviceId === null) {
    byId("lastResponse").textContent = "Укажите хотя бы один TYPE 2 device_id в config/team.json";
    return;
  }
  await postJson(`/api/actions/default-light/${deviceId}`, {});
});

document.querySelectorAll("[data-announce]").forEach((button) => {
  button.addEventListener("click", async () => {
    await postJson("/api/actions/announce-recommendation", { kind: button.dataset.announce });
  });
});

updateSpeechButtons();
refreshState({ source: "initial" }).catch((error) => {
  byId("lastResponse").textContent = String(error);
});
setInterval(() => refreshState({ source: "interval" }).catch(() => null), 1000);
window.addEventListener("focus", () => {
  refreshState({ source: "focus" }).catch(() => null);
});
