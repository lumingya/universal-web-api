from app.models.schemas import get_default_send_confirmation_config
from app.services.config.engine import ConfigEngine


def _engine():
    return ConfigEngine.__new__(ConfigEngine)


def test_validate_send_confirmation_accepts_keypress_retry_action():
    engine = _engine()

    result = engine._validate_send_confirmation_config(
        {
            "retry_action": "key_press",
            "retry_key_combo": "Ctrl+Enter",
            "max_retry_count": 1,
        }
    )

    assert result["retry_action"] == "key_press"
    assert result["retry_key_combo"] == "Ctrl+Enter"
    assert result["max_retry_count"] == 1


def test_validate_send_confirmation_rejects_invalid_retry_action_and_blank_key():
    engine = _engine()
    defaults = get_default_send_confirmation_config()

    result = engine._validate_send_confirmation_config(
        {
            "retry_action": "double_click",
            "retry_key_combo": "   ",
        }
    )

    assert result["retry_action"] == defaults["retry_action"]
    assert result["retry_key_combo"] == defaults["retry_key_combo"]
