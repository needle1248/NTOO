const teamProfile = window.__TEAM_PROFILE__ || {};

const byId = (id) => document.getElementById(id);

let routeSpeechEnabled = true;
let lastSpokenNavigationKey = "";
let lastRouteSpeechText = "";
let latestNavigationPoints = [];

async function postJson(url, payload) {
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
  await refreshState();
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

function parseWaypointPointIds(value) {
  return (value || "")
    .split(/[,\n;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function setServiceOptions(selectId, services, preferredValue) {
  const select = byId(selectId);
  const serviceList = services || [];

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

function renderNavigation(navigation) {
  const points = navigation.points || [];
  latestNavigationPoints = points;
  const startPointId = navigation.start_point?.point_id || points[0]?.point_id || "";
  const destinationPointId = navigation.destination?.point_id || "";
  const blockedPoints = navigation.blocked_points || [];
  const waypoints = navigation.waypoints || [];

  setPointOptions("navigationStartPoint", points, startPointId);
  setPointOptions("navigationDestination", points, destinationPointId, byId("navigationStartPoint").value);
  setServiceOptions("navigationService", navigation.available_services || [], navigation.service?.service_id || "");

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
  byId("navigationWaypoints").value = waypoints.map((point) => point.point_id).join(", ");

  const routeContainer = byId("navigationRoute");
  if (!navigation.route?.length) {
    routeContainer.innerHTML = '<div class="stack-item"><span class="small">Маршрут пока не построен.</span></div>';
    return;
  }

  routeContainer.innerHTML = navigation.route
    .map((step, index) => `
      <div class="route-step route-step--${step.status}">
        <div>
          <strong>${index + 1}. ${step.from_point.name} -> ${step.to_point.name}</strong>
          <span class="small">${step.connection_type_label}</span>
          <span class="small">${step.instruction}</span>
          <span class="small">${formatPointSignal(step.to_point)}</span>
        </div>
        <span class="route-badge route-badge--${step.status}">
          ${step.status === "confirmed" ? "Пройден" : step.status === "active" ? "Активный шаг" : "Ожидает"}
        </span>
      </div>
    `)
    .join("");
}

async function refreshState() {
  const response = await fetch("/api/state");
  const state = await response.json();

  byId("cityStatus").textContent = state.city.connected ? "Городской сервер доступен" : "Нет связи с городом";
  byId("cityStatus").className = `status-pill ${state.city.connected ? "ok" : "error"}`;
  byId("cityError").textContent = state.city.last_error || `Последний опрос: ${state.city.last_poll_at || "ещё не было"}`;

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

  renderNavigation(state.navigation || {});
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

document.getElementById("navigationForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = formDataToObject(event.target);
  await postJson("/api/navigation/start", {
    start_point_id: form.start_point_id,
    destination_point_id: form.destination_point_id,
    waypoint_point_ids: parseWaypointPointIds(form.waypoint_point_ids),
    service_id: form.service_id || null,
  });
});

document.getElementById("navigationStartPoint").addEventListener("change", () => {
  setPointOptions(
    "navigationDestination",
    latestNavigationPoints,
    byId("navigationDestination").value,
    byId("navigationStartPoint").value,
  );
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
refreshState().catch((error) => {
  byId("lastResponse").textContent = String(error);
});
setInterval(() => refreshState().catch(() => null), 2500);
