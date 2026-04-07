from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

from digital_guide.core.models import TeamConfig


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "payload"):
            payload["payload"] = record.payload
        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"[{datetime.now().strftime('%H:%M:%S')}] {record.levelname:<7} {record.getMessage()}"
        if hasattr(record, "payload"):
            return f"{base} | {json.dumps(record.payload, ensure_ascii=False)}"
        return base


def configure_logging(team_config: TeamConfig) -> logging.Logger:
    logger = logging.getLogger("digital_guide")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ConsoleFormatter())

    json_handler = logging.StreamHandler(sys.stderr)
    json_handler.setFormatter(JsonFormatter())

    logger.addHandler(console_handler)
    logger.addHandler(json_handler)
    logger.propagate = False
    logger.info(
        "logger configured",
        extra={"payload": {"team_id": team_config.team_id, "mode": team_config.selected_confirmation_mode.value}},
    )
    return logger

