from __future__ import annotations

import json
from pathlib import Path


class JsonPersistence:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, name: str, payload: dict) -> None:
        path = self.base_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_jsonl(self, stem: str, payload: dict) -> None:
        path = self.base_dir / f"{stem}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

