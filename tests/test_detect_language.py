import sys
from unittest.mock import MagicMock

# app.llm imports litellm.Router at module level which requires an event loop.
sys.modules["app.llm"] = MagicMock()

from app.routes.notices import _detect_language  # noqa: E402


def test_pure_korean():
    assert _detect_language("전체 재학생은 LMS에서 이수하세요.") == "ko"


def test_pure_english():
    assert _detect_language("All residents must check in at the Front Desk.") == "en"


def test_mixed_korean_dominant():
    # "Check-in 안내" 스타일 — 한국어 지배
    assert _detect_language("Check-in 안내: 2월 28일까지 프런트에서 완료") == "ko"


def test_mixed_english_dominant():
    # 한국어 소수 포함 영어 문장 — 영어 지배
    assert _detect_language("Bring your student ID (학생증) to the Front Desk.") == "en"


def test_whitespace_only():
    assert _detect_language("   ", "") == "ko"


def test_empty_uses_title_fallback():
    assert _detect_language("", "Dormitory Check-in") == "en"


def test_numeric_only():
    # 알파벳이 아예 없으면 기본 ko
    assert _detect_language("2026-02-28 09:00") == "ko"


def test_empty_all():
    assert _detect_language() == "ko"
    assert _detect_language("", "") == "ko"
