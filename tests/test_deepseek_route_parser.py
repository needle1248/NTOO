from digital_guide.core.models import VoiceRouteParameters
from digital_guide.services.deepseek_route_parser import DeepSeekRouteParser


def test_normalize_model_payload_backfills_user_and_aliases_scenario() -> None:
    params = DeepSeekRouteParser.normalize_model_payload(
        {
            "user_id": "",
            "start_point": 1,
            "target_point": 16,
            "via_points": [5, "8", "bad", 11],
            "scenario": "transport",
        },
        current_user_id="user_demo",
    )
    assert params == VoiceRouteParameters(
        user_id="user_demo",
        start_point=1,
        target_point=16,
        via_points=[5, 8, 11],
        scenario="bus",
    )


def test_missing_fields_marks_only_absent_values() -> None:
    params = VoiceRouteParameters(user_id="user_demo", start_point=None, target_point=16, via_points=[], scenario="")
    assert DeepSeekRouteParser.missing_fields(params) == ["start_point", "scenario"]
