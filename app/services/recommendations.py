from __future__ import annotations

from typing import Any


def build_clothing_recommendation(environment: dict[str, Any] | None) -> dict[str, Any] | None:
    if not environment:
        return None

    temperature = environment["temperature_c"]
    humidity = environment["humidity_percent"]
    pressure = environment["pressure_hpa"]

    if temperature < 10:
        mode = "cold"
        text = "Холодно: тёплая куртка, шапка и закрытая обувь."
    elif temperature > 24:
        mode = "hot"
        text = "Жарко: лёгкая одежда, головной убор и вода."
    else:
        mode = "normal"
        text = "Нормально: лёгкая куртка или свитер по погоде."

    if humidity > 80:
        text += " Влажность высокая, стоит взять непромокаемую верхнюю одежду."
    if pressure < 990:
        text += " Давление понижено, лучше избегать перегрузки и выйти чуть заранее."

    return {
        "mode": mode,
        "text": text,
        "temperature_c": temperature,
        "humidity_percent": humidity,
        "pressure_hpa": pressure,
    }


def build_traffic_recommendation(
    best_eta_seconds: float | None,
    baseline_eta_seconds: float | None,
) -> dict[str, Any] | None:
    if best_eta_seconds is None or baseline_eta_seconds is None or baseline_eta_seconds <= 0:
        return None

    slowdown_factor = best_eta_seconds / baseline_eta_seconds
    delayed = slowdown_factor >= 1.5 and (best_eta_seconds - baseline_eta_seconds) >= 20
    if not delayed:
        return {
            "delayed": False,
            "text": "Существенных задержек автобуса сейчас не наблюдается.",
            "best_eta_seconds": round(best_eta_seconds, 1),
            "baseline_eta_seconds": round(baseline_eta_seconds, 1),
        }

    return {
        "delayed": True,
        "text": (
            "На вашем кольце образовалась пробка, рекомендуем временно "
            "воздержаться от поездок и остаться дома."
        ),
        "best_eta_seconds": round(best_eta_seconds, 1),
        "baseline_eta_seconds": round(baseline_eta_seconds, 1),
    }


def build_obstacle_recommendation(obstacles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not obstacles:
        return None

    latest = obstacles[0]
    message = latest.get("message") or (
        f"На маршруте обнаружено препятствие типа {latest.get('obstacle_type', 'unknown')}."
    )
    return {
        "text": message,
        "location_id": latest.get("location_id"),
        "reroute_required": bool(latest.get("reroute_required")),
    }

