from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class RGBColor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class VoiceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[1]
    text: str = Field(min_length=1, max_length=200)
    timestamp: int | None = None


class SoundEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[2]
    device_id: int
    duration_ms: int = Field(gt=0)
    frequency_hz: int = Field(gt=0)


class LightEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[3]
    device_id: int
    color: RGBColor


class RfidEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[4]
    device_id: int
    rfid_code: str = Field(min_length=1, max_length=128)


class ObstacleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[5]
    location_id: int | str
    obstacle_type: str = Field(min_length=1, max_length=100)
    reroute_required: bool
    message: str | None = Field(default=None, max_length=200)


class FaceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[6]
    device_id: int
    user_id: str = Field(min_length=1, max_length=128)
    confidence: float = Field(ge=0.0, le=1.0)


CityEvent = Annotated[
    Union[VoiceEvent, SoundEvent, LightEvent, RfidEvent, ObstacleEvent, FaceEvent],
    Field(discriminator="type"),
]

city_event_adapter = TypeAdapter(CityEvent)


class EnvironmentReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature_c: float
    humidity_percent: float = Field(ge=0.0, le=100.0)
    pressure_hpa: float = Field(gt=0.0)
    timestamp: int | None = None


class DistanceReading(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: int
    distance_cm: float = Field(ge=0.0)
    threshold_cm: float = Field(default=40.0, gt=0.0)
    bus_detected: bool | None = None
    timestamp: int | None = None


class AnnounceRecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["clothing", "traffic", "obstacle"]
