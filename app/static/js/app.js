const teamProfile = window.__TEAM_PROFILE__ || {};

const byId = (id) => document.getElementById(id);

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

refreshState().catch((error) => {
  byId("lastResponse").textContent = String(error);
});
setInterval(() => refreshState().catch(() => null), 2500);
