import ssl
from pathlib import Path

from app.services import face_map


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_download_model_payload_falls_back_after_ssl_error(monkeypatch):
    calls: list[ssl.SSLContext | None] = []

    def fake_urlopen(url: str, timeout: int = 30, context=None):
        calls.append(context)
        if context is None:
            raise ssl.SSLError("certificate verify failed")
        return _FakeResponse(b"model-bytes")

    monkeypatch.setattr(face_map, "urlopen", fake_urlopen)
    monkeypatch.setattr(face_map, "_certifi_context", lambda: None)

    payload = face_map._download_model_payload("https://example.com/model.onnx")

    assert payload == b"model-bytes"
    assert calls[0] is None
    assert isinstance(calls[1], ssl.SSLContext)


def test_ensure_model_file_skips_existing_valid_file(tmp_path: Path, monkeypatch):
    model_path = tmp_path / "model.onnx"
    model_path.write_bytes(b"existing-model")

    def unexpected_urlopen(*args, **kwargs):
        raise AssertionError("download should not be attempted")

    monkeypatch.setattr(face_map, "urlopen", unexpected_urlopen)

    face_map._ensure_model_file(model_path, "https://example.com/model.onnx")

    assert model_path.read_bytes() == b"existing-model"
