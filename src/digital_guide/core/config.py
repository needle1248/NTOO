from __future__ import annotations

import json
from pathlib import Path

import yaml

from digital_guide.core.models import DeviceDescriptor, MFCServiceMap, RouteGraph, RuntimeConfig, SignalProfile, TeamConfig


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_runtime(config_dir: Path) -> RuntimeConfig:
    team_config = TeamConfig.model_validate(_read_yaml(config_dir / "team_config.yaml"))
    route_graph = RouteGraph.model_validate(_read_json(config_dir / "route_graph.json"))
    devices = [DeviceDescriptor.model_validate(item) for item in _read_json(config_dir / "devices.json")["devices"]]
    signal_profiles = [SignalProfile.model_validate(item) for item in _read_json(config_dir / "signals.json")["profiles"]]
    mfc_service_map = MFCServiceMap.model_validate(_read_json(config_dir / "mfc_services.json"))
    route_graph.devices = devices
    route_graph.signal_profiles = signal_profiles
    route_graph.mfc_service_map = mfc_service_map
    return RuntimeConfig(team_config=team_config, route_graph=route_graph)

