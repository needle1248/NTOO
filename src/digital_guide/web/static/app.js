const pages = {
  dashboard: {
    title: "Панель управления",
    subtitle: "Статус сценариев, маршрут, ETA, погода, пробки и активные предупреждения."
  },
  devices: {
    title: "Устройства",
    subtitle: "Heartbeat, очередь команд и роли устройств для звука, света, погоды и виброплатформы."
  },
  route_builder: {
    title: "Построение маршрута",
    subtitle: "Построение пеших, транспортных, пересадочных и indoor-маршрутов по конфигурируемому графу."
  },
  transport: {
    title: "Транспорт",
    subtitle: "Отслеживание автобусов, ETA, признаков пробки и подсказок на посадку или выход."
  },
  mfc: {
    title: "МФЦ",
    subtitle: "Выбор услуги, привязка к окну и запуск indoor-сценария со световой навигацией."
  },
  logs: {
    title: "Журнал событий",
    subtitle: "Удобная для защиты лента событий с machine JSON и читаемой временной шкалой."
  }
};

let routeVoiceRecognition = null;

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const text = await response.text();
    let parsed = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    throw new Error((parsed && (parsed.detail || parsed.message)) || text || `HTTP ${response.status}`);
  }
  return response.json();
}

function setHero(pageId) {
  const hero = pages[pageId] || pages.dashboard;
  const heroTitle = document.querySelector("[data-hero-title]");
  const heroSubtitle = document.querySelector("[data-hero-subtitle]");
  if (heroTitle) heroTitle.textContent = hero.title;
  if (heroSubtitle) heroSubtitle.textContent = hero.subtitle;
  document.querySelectorAll(".nav a").forEach(link => {
    const match = link.dataset.page === pageId;
    link.classList.toggle("active", Boolean(match));
  });
}

function renderJson(id, payload) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = JSON.stringify(payload, null, 2);
  }
}

function renderTable(id, rows, columns) {
  const root = document.getElementById(id);
  if (!root) return;
  const thead = `<thead><tr>${columns.map(col => `<th>${col.label}</th>`).join("")}</tr></thead>`;
  const body = rows.length
    ? rows.map(row => `<tr>${columns.map(col => `<td>${col.render(row)}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${columns.length}" class="meta">Данных пока нет.</td></tr>`;
  root.innerHTML = `${thead}<tbody>${body}</tbody>`;
}

function normalizeScenarioForApi(value) {
  const scenario = (value || "").trim().toLowerCase();
  if (scenario === "transport") return "bus";
  if (scenario === "mfc") return "indoor";
  return scenario || "walk";
}

function normalizeScenarioForForm(value) {
  const scenario = normalizeScenarioForApi(value);
  return ["walk", "bus", "indoor", "mixed"].includes(scenario) ? scenario : "";
}

function parseCommaSeparatedPoints(value) {
  return value
    ? value
        .split(",")
        .map(item => Number(item.trim()))
        .filter(item => Number.isInteger(item) && item > 0)
    : [];
}

function getRouteBuilderElements() {
  return {
    form: document.getElementById("route-builder-form"),
    status: document.getElementById("route-builder-status"),
    missing: document.getElementById("route-builder-missing"),
    transcript: document.getElementById("voice-route-transcript"),
    modelJson: document.getElementById("voice-route-model-json"),
    startButton: document.getElementById("voice-route-start"),
    stopButton: document.getElementById("voice-route-stop"),
    buildVoiceButton: document.getElementById("voice-route-build")
  };
}

function setRouteBuilderStatus(message, kind = "info", missingMessage = "") {
  const { status, missing } = getRouteBuilderElements();
  if (status) {
    status.textContent = message;
    status.className = `status-box${kind ? ` ${kind}` : ""}`;
  }
  if (missing) {
    missing.textContent = missingMessage;
  }
}

function collectRouteFormSnapshot(form) {
  return {
    user_id: form.user_id.value.trim(),
    start_point: form.start_node.value ? Number(form.start_node.value) : null,
    target_point: form.goal_node.value ? Number(form.goal_node.value) : null,
    via_points: parseCommaSeparatedPoints(form.via_nodes.value),
    scenario: normalizeScenarioForApi(form.scenario_kind.value)
  };
}

function applyVoiceParamsToForm(form, params) {
  if (params.user_id) {
    form.user_id.value = params.user_id;
  }
  if (params.start_point !== null && params.start_point !== undefined) {
    form.start_node.value = params.start_point;
  }
  if (params.target_point !== null && params.target_point !== undefined) {
    form.goal_node.value = params.target_point;
  }
  if (Array.isArray(params.via_points) && params.via_points.length) {
    form.via_nodes.value = params.via_points.join(", ");
  }
  if (params.scenario) {
    const normalized = normalizeScenarioForForm(params.scenario);
    if (normalized) {
      form.scenario_kind.value = normalized;
    }
  }
}

function routeFormHasRequiredFields(form) {
  return Boolean(form.user_id.value.trim() && form.start_node.value.trim() && form.goal_node.value.trim());
}

async function refreshState() {
  const compact = window.innerWidth <= 640;
  const state = await fetchJson("/api/state");
  renderJson("state-json", state);
  renderTable(
    "device-table",
    Object.values(state.devices || {}),
    compact
      ? [
          { label: "Устройство", render: row => row.device_id || "-" },
          { label: "Статус", render: row => row.status || "ok" }
        ]
      : [
          { label: "Устройство", render: row => row.device_id || "-" },
          { label: "Статус", render: row => row.status || "ok" },
          { label: "Тип", render: row => row.device_kind || "-" },
          { label: "Последняя активность", render: row => row.last_seen || "-" }
        ]
  );
  renderTable(
    "bus-table",
    state.buses || [],
    compact
      ? [
          { label: "Автобус", render: row => row.bus_id },
          { label: "Ост.", render: row => row.current_stop },
          { label: "Пробка", render: row => (row.congestion ? "ДА" : "нет") }
        ]
      : [
          { label: "Автобус", render: row => row.bus_id },
          { label: "Кольцо", render: row => row.ring_id },
          { label: "Текущая остановка", render: row => row.current_stop },
          {
            label: "Средний круг",
            render: row => (row.rolling_lap_average_seconds ? `${row.rolling_lap_average_seconds.toFixed(1)} с` : "-")
          },
          { label: "Пробка", render: row => (row.congestion ? "ДА" : "нет") }
        ]
  );
  renderTable(
    "log-table",
    (await fetchJson("/api/logs?limit=20")) || [],
    compact
      ? [
          { label: "Событие", render: row => row.event },
          { label: "Сообщение", render: row => row.message }
        ]
      : [
          { label: "Время", render: row => row.timestamp },
          { label: "Событие", render: row => row.event },
          { label: "Сообщение", render: row => row.message }
        ]
  );

  const routeMeta = document.getElementById("route-meta");
  if (routeMeta) {
    routeMeta.textContent = state.active_route ? `${state.active_route.route_id}: ${state.active_route.voice_text}` : "Активного маршрута пока нет.";
  }
  const recommendation = document.getElementById("recommendation-box");
  if (recommendation) {
    recommendation.textContent = state.recommendation || "Рекомендаций пока нет.";
  }
  const warning = document.getElementById("warning-box");
  if (warning) {
    warning.textContent = state.congestion_warning || "Предупреждений о пробках нет.";
    warning.className = state.congestion_warning ? "badge warning" : "badge";
  }
}

async function buildRoute(form, options = {}) {
  const payload = {
    user_id: form.user_id.value.trim(),
    start_node: form.start_node.value ? Number(form.start_node.value) : null,
    goal_node: form.goal_node.value ? Number(form.goal_node.value) : null,
    via_nodes: parseCommaSeparatedPoints(form.via_nodes.value),
    scenario_kind: normalizeScenarioForApi(form.scenario_kind.value)
  };

  if (!payload.user_id || payload.start_node === null || payload.goal_node === null) {
    if (options.reportStatus !== false) {
      setRouteBuilderStatus("Не удалось построить маршрут: заполните ID пользователя, стартовую и целевую точки.", "error");
    }
    throw new Error("Не заполнены обязательные поля маршрута.");
  }

  const route = await fetchJson("/api/routes/build", { method: "POST", body: JSON.stringify(payload) });
  renderJson("route-result", route);
  if (options.reportStatus !== false) {
    setRouteBuilderStatus(options.successMessage || "Маршрут построен.", "success", options.missingMessage || "");
  }
  await refreshState();
  return route;
}

async function startScenario(form) {
  const payload = {
    scenario_kind: form.scenario_kind.value,
    user_id: form.user_id.value,
    start_node: Number(form.start_node.value),
    goal_node: form.goal_node.value ? Number(form.goal_node.value) : null,
    via_nodes: form.via_nodes.value ? form.via_nodes.value.split(",").map(x => Number(x.trim())).filter(Boolean) : [],
    bus_id: form.bus_id?.value || null,
    destination_label: form.destination_label?.value || null,
    mfc_service_id: form.mfc_service_id?.value || null
  };
  const scenario = await fetchJson("/api/scenario/start", { method: "POST", body: JSON.stringify(payload) });
  renderJson("scenario-result", scenario);
  await refreshState();
}

async function simulateFace(form) {
  const url = `/api/simulate/point-confirmation/${encodeURIComponent(form.device_id.value)}?user_id=${encodeURIComponent(form.user_id.value)}`;
  const response = await fetchJson(url, { method: "POST" });
  renderJson("simulation-result", response);
  await refreshState();
}

async function simulateObstacle(form) {
  const url = `/api/simulate/obstacle/${encodeURIComponent(form.location_id.value)}?message=${encodeURIComponent(form.message.value)}`;
  const response = await fetchJson(url, { method: "POST" });
  renderJson("simulation-result", response);
  await refreshState();
}

async function simulateDistance(form) {
  const url = `/api/simulate/distance/${encodeURIComponent(form.device_id.value)}?distance_cm=${encodeURIComponent(form.distance_cm.value)}`;
  const response = await fetchJson(url, { method: "POST" });
  renderJson("simulation-result", response);
  await refreshState();
}

function stopRouteVoiceRecognition() {
  if (routeVoiceRecognition) {
    routeVoiceRecognition.stop();
  }
}

function createRouteVoiceRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    return null;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = "ru-RU";
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.continuous = false;
  return recognition;
}

function setupRouteBuilderVoice() {
  const { form, status, transcript, modelJson, startButton, stopButton, buildVoiceButton } = getRouteBuilderElements();
  if (!form || !status || !transcript || !modelJson || !startButton || !stopButton || !buildVoiceButton) {
    return;
  }

  const recognition = createRouteVoiceRecognition();
  if (!recognition) {
    startButton.disabled = true;
    stopButton.disabled = true;
    buildVoiceButton.disabled = true;
    setRouteBuilderStatus("Браузер не поддерживает распознавание речи. Используйте Chrome или Edge.", "error");
    return;
  }

  startButton.addEventListener("click", () => {
    transcript.value = "";
    modelJson.textContent = "";
    buildVoiceButton.disabled = true;
    stopButton.disabled = false;
    startButton.disabled = true;
    routeVoiceRecognition = recognition;
    setRouteBuilderStatus("Слушаю...", "warning");
    recognition.start();
  });

  stopButton.addEventListener("click", () => {
    setRouteBuilderStatus("Останавливаю запись...", "warning");
    stopRouteVoiceRecognition();
  });

  recognition.onresult = event => {
    const spokenText = Array.from(event.results)
      .map(result => result[0].transcript)
      .join(" ")
      .trim();
    transcript.value = spokenText;
    if (spokenText) {
      buildVoiceButton.disabled = false;
      setRouteBuilderStatus("Речь распознана. Нажмите «Построить голосом».", "success");
    }
  };

  recognition.onerror = event => {
    startButton.disabled = false;
    stopButton.disabled = true;
    buildVoiceButton.disabled = !transcript.value.trim();
    setRouteBuilderStatus(`Ошибка распознавания речи: ${event.error}`, "error");
  };

  recognition.onend = () => {
    routeVoiceRecognition = null;
    startButton.disabled = false;
    stopButton.disabled = true;
    buildVoiceButton.disabled = !transcript.value.trim();
    if (!transcript.value.trim()) {
      setRouteBuilderStatus("Речь не распознана. Попробуйте ещё раз.", "warning");
    }
  };

  buildVoiceButton.addEventListener("click", async () => {
    const spokenText = transcript.value.trim();
    if (!spokenText) {
      setRouteBuilderStatus("Сначала запишите или введите распознанную речь.", "warning");
      return;
    }

    const currentState = collectRouteFormSnapshot(form);
    setRouteBuilderStatus("Обрабатываю через DeepSeek-R1...", "warning");

    try {
      const response = await fetchJson("/api/voice/route-parse", {
        method: "POST",
        body: JSON.stringify({
          transcript: spokenText,
          current_user_id: currentState.user_id,
          current_start_point: currentState.start_point,
          current_target_point: currentState.target_point,
          current_via_points: currentState.via_points,
          current_scenario: currentState.scenario
        })
      });

      renderJson("voice-route-model-json", response.raw_model_json || {});
      applyVoiceParamsToForm(form, response.params || {});

      const missingMessage = response.missing_fields?.length
        ? `Не распознаны параметры: ${response.missing_fields.join(", ")}.`
        : "";

      if (!routeFormHasRequiredFields(form)) {
        setRouteBuilderStatus(
          response.message || "Не удалось распознать параметры маршрута.",
          response.missing_fields?.length ? "warning" : "error",
          missingMessage
        );
        return;
      }

      await buildRoute(form, {
        successMessage: "Маршрут построен.",
        missingMessage
      });
    } catch (error) {
      setRouteBuilderStatus(`Не удалось распознать параметры: ${error.message}`, "error");
    }
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  const pageId = document.body.dataset.page || "dashboard";
  setHero(pageId);

  document.querySelectorAll("form[data-action='build-route']").forEach(form =>
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        await buildRoute(form);
      } catch {
        // Status is already shown in the UI.
      }
    })
  );

  document.querySelectorAll("form[data-action='start-scenario']").forEach(form =>
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await startScenario(form);
    })
  );

  document.querySelectorAll("form[data-action='simulate-face']").forEach(form =>
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await simulateFace(form);
    })
  );

  document.querySelectorAll("form[data-action='simulate-obstacle']").forEach(form =>
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await simulateObstacle(form);
    })
  );

  document.querySelectorAll("form[data-action='simulate-distance']").forEach(form =>
    form.addEventListener("submit", async event => {
      event.preventDefault();
      await simulateDistance(form);
    })
  );

  setupRouteBuilderVoice();
  await refreshState();
  setInterval(refreshState, 4000);
});
