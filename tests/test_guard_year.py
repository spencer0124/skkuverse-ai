"""_guard_year 후처리 검증 — 연도 hallucination 10건 QA 데이터 기반."""

import sys
from unittest.mock import MagicMock

# app.llm imports litellm.Router at module level which requires an event loop.
# Mock it out before importing notices.
sys.modules["app.llm"] = MagicMock()

import pytest

from app.routes.notices import (
    NoticeDetails,
    NoticePeriod,
    NoticeSummary,
    _guard_year,
)


def _make(startDate=None, endDate=None, periods=None):
    if periods is None:
        if startDate or endDate:
            periods = [NoticePeriod(startDate=startDate, endDate=endDate)]
        else:
            periods = []
    return NoticeSummary(
        oneLiner="test",
        summary="test",
        type="action_required",
        periods=periods,
        locations=[],
        details=NoticeDetails(),
    )


def _sd(s: NoticeSummary) -> str | None:
    return s.periods[0].startDate if s.periods else None


def _ed(s: NoticeSummary) -> str | None:
    return s.periods[0].endDate if s.periods else None


# ── Case 1: 기아 채용 ── 2024→2026, date=2026-04-09
def test_case1_kia():
    s = _guard_year(_make("2024-04-01", "2024-04-20"), "2026-04-09")
    assert _sd(s) == "2026-04-01"
    assert _ed(s) == "2026-04-20"


# ── Case 2: 삼성기부장학금 ── 2024→2026, date=2026-04-01
def test_case2_samsung_scholarship():
    s = _guard_year(_make("2024-04-07", "2024-04-07"), "2026-04-01")
    assert _sd(s) == "2026-04-07"
    assert _ed(s) == "2026-04-07"


# ── Case 3: LG이노텍 ── 2023→2026, date=2026-04-01
def test_case3_lg_innotek():
    s = _guard_year(_make("2023-03-30", "2023-04-13"), "2026-04-01")
    assert _sd(s) == "2026-03-30"
    assert _ed(s) == "2026-04-13"


# ── Case 4: 삼성물산 ── 2024→2026, date=2026-03-12
def test_case4_samsung_ct():
    s = _guard_year(_make("2024-03-10", "2024-03-17"), "2026-03-12")
    assert _sd(s) == "2026-03-10"
    assert _ed(s) == "2026-03-17"


# ── Case 5: 키움 디지털 아카데미 ── 2024→2026, startDate=None
def test_case5_kiwoom():
    s = _guard_year(_make(None, "2024-04-17"), "2026-04-01")
    assert _sd(s) is None
    assert _ed(s) == "2026-04-17"


# ── Case 6: 한국어 실험 ── 2024→2026
def test_case6_korean_experiment():
    s = _guard_year(_make(None, "2024-04-19"), "2026-04-01")
    assert _sd(s) is None
    assert _ed(s) == "2026-04-19"


# ── Case 7: AI FGI ── 2024→2026
def test_case7_ai_fgi():
    s = _guard_year(_make("2024-04-13", "2024-04-17"), "2026-04-01")
    assert _sd(s) == "2026-04-13"
    assert _ed(s) == "2026-04-17"


# ── Case 8: 기숙사 통금 해제 (영문) ── 2020→2026
def test_case8_curfew():
    s = _guard_year(_make("2020-04-13", "2020-04-26"), "2026-04-01")
    assert _sd(s) == "2026-04-13"
    assert _ed(s) == "2026-04-26"


# ── Case 9: 외국인 한국어 강좌 ── 2024→2026
def test_case9_korean_lecture():
    s = _guard_year(_make("2024-03-16", "2024-06-04"), "2026-03-10")
    assert _sd(s) == "2026-03-16"
    assert _ed(s) == "2026-06-04"


# ── Case 10: 창업지원단 ── startDate 2023(사업기간)→교정, endDate 2026(정확)→유지
def test_case10_startup():
    s = _guard_year(_make("2023-01-01", "2026-04-16"), "2026-04-01")
    assert _sd(s) == "2026-01-01"
    assert _ed(s) == "2026-04-16"


# ── 경계 케이스: ±1년 이내는 no-op ──
def test_within_range_no_op():
    s = _guard_year(_make("2025-12-20"), "2026-01-05")
    assert _sd(s) == "2025-12-20"  # |2025-2026|=1, 범위 안


def test_pub_date_none_no_op():
    s = _guard_year(_make("2024-03-15"), None)
    assert _sd(s) == "2024-03-15"  # no-op


def test_null_dates_no_op():
    s = _guard_year(_make(None, None), "2026-04-01")
    assert s.periods == []


# ── 다중 period 교정 ──
def test_multi_period_year_fix():
    s = _guard_year(
        _make(
            periods=[
                NoticePeriod(label="1차 납부", startDate="2024-02-10", endDate="2024-02-14"),
                NoticePeriod(label="2차 추가납부", startDate="2024-02-24", endDate="2024-02-26"),
            ]
        ),
        "2026-02-01",
    )
    assert s.periods[0].startDate == "2026-02-10"
    assert s.periods[0].endDate == "2026-02-14"
    assert s.periods[0].label == "1차 납부"
    assert s.periods[1].startDate == "2026-02-24"
    assert s.periods[1].endDate == "2026-02-26"
    assert s.periods[1].label == "2차 추가납부"
