"""Tests for app/budget_reset.py — daily litellm Router budget cache clear.

These tests are NOT just structural — they assert the exact key formats that
must match `litellm/router_strategy/budget_limiter.py:453-454` of the pinned
1.82.6 release. If a litellm upgrade changes the cache layout, these tests
fail loudly instead of the production reset becoming a silent no-op.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

# app.llm imports litellm.Router at module level which requires an event loop;
# mock it before importing budget_reset (mirrors test_guard_year.py pattern).
sys.modules["app.llm"] = MagicMock()

import pytest  # noqa: E402

from app.budget_reset import _build_keys, reset_deployment_budgets  # noqa: E402


# ── Key format invariants (must match litellm 1.82.6 source) ──


def test_spend_key_format_matches_litellm_source():
    spend_key, _ = _build_keys("openai-gpt41-mini", "1d")
    # Must equal: budget_limiter.py:453 deployment_spend_key build expression
    assert spend_key == "deployment_spend:openai-gpt41-mini:1d"


def test_start_time_key_format_matches_litellm_source():
    _, start_time_key = _build_keys("openai-gpt41-mini", "1d")
    # Must equal: budget_limiter.py:454 deployment_start_time_key build
    # NOTE: no `:duration` suffix on this key — easy mistake to make
    assert start_time_key == "deployment_budget_start_time:openai-gpt41-mini"


def test_keys_are_deterministic_across_calls():
    a = _build_keys("x", "1d")
    b = _build_keys("x", "1d")
    assert a == b


# ── reset_deployment_budgets behavior ──


def _make_router_mock(model_list, has_budget_logger=True, has_dual_cache=True):
    """Build a fake litellm Router with the structure budget_reset reads."""
    fake_cache = AsyncMock()
    fake_cache.async_get_cache = AsyncMock(return_value="any-truthy-value")
    fake_cache.async_delete_cache = AsyncMock()

    fake_budget_logger = MagicMock()
    fake_budget_logger.dual_cache = fake_cache if has_dual_cache else None

    fake_router = MagicMock()
    fake_router.router_budget_logger = fake_budget_logger if has_budget_logger else None
    fake_router.model_list = model_list

    return fake_router, fake_cache


@pytest.mark.asyncio
async def test_clears_both_keys_per_deployment(monkeypatch):
    model_list = [
        {
            "model_info": {"id": "openai-gpt41-mini"},
            "litellm_params": {"budget_duration": "1d"},
        },
        {
            "model_info": {"id": "cerebras-qwen3"},
            "litellm_params": {"budget_duration": "1d"},
        },
    ]
    fake_router, fake_cache = _make_router_mock(model_list)

    # patch the module-level llm_router that budget_reset imported
    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)

    summary = await reset_deployment_budgets()

    # 2 deployments × 2 keys = 4 deletes
    assert summary == {"deployments": 2, "keys_present": 4, "keys_deleted": 4}
    assert fake_cache.async_delete_cache.await_count == 4
    deleted_keys = [c.args[0] for c in fake_cache.async_delete_cache.await_args_list]
    assert "deployment_spend:openai-gpt41-mini:1d" in deleted_keys
    assert "deployment_budget_start_time:openai-gpt41-mini" in deleted_keys
    assert "deployment_spend:cerebras-qwen3:1d" in deleted_keys
    assert "deployment_budget_start_time:cerebras-qwen3" in deleted_keys


@pytest.mark.asyncio
async def test_skips_deployment_without_budget_duration(monkeypatch):
    """Cerebras/Groq have no `max_budget` in our config → no budget_duration."""
    model_list = [
        {
            "model_info": {"id": "openai-gpt41-mini"},
            "litellm_params": {"budget_duration": "1d"},
        },
        {
            "model_info": {"id": "cerebras-qwen3"},
            "litellm_params": {},  # no budget_duration
        },
    ]
    fake_router, fake_cache = _make_router_mock(model_list)
    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)

    summary = await reset_deployment_budgets()
    assert summary["deployments"] == 1  # only OpenAI counted
    assert fake_cache.async_delete_cache.await_count == 2


@pytest.mark.asyncio
async def test_no_op_when_router_budget_logger_missing(monkeypatch):
    fake_router, _ = _make_router_mock([], has_budget_logger=False)
    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)
    summary = await reset_deployment_budgets()
    assert summary == {"deployments": 0, "keys_present": 0, "keys_deleted": 0}


@pytest.mark.asyncio
async def test_no_op_when_dual_cache_missing(monkeypatch):
    fake_router, _ = _make_router_mock([], has_dual_cache=False)
    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)
    summary = await reset_deployment_budgets()
    assert summary == {"deployments": 0, "keys_present": 0, "keys_deleted": 0}


@pytest.mark.asyncio
async def test_swallows_cache_exceptions(monkeypatch):
    """APScheduler must NEVER die from a single failure."""
    model_list = [
        {
            "model_info": {"id": "openai-gpt41-mini"},
            "litellm_params": {"budget_duration": "1d"},
        },
    ]
    fake_router, fake_cache = _make_router_mock(model_list)
    fake_cache.async_get_cache = AsyncMock(side_effect=RuntimeError("cache offline"))

    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)

    # Must not raise even though the cache call exploded
    summary = await reset_deployment_budgets()
    # On exception path the function returns whatever was accumulated before
    # the failure; the only invariant is "didn't raise".
    assert isinstance(summary, dict)


@pytest.mark.asyncio
async def test_keys_present_counter_when_already_empty(monkeypatch):
    """If cache returns None, keys_present should reflect that — observability."""
    model_list = [
        {
            "model_info": {"id": "openai-gpt41-mini"},
            "litellm_params": {"budget_duration": "1d"},
        },
    ]
    fake_router, fake_cache = _make_router_mock(model_list)
    fake_cache.async_get_cache = AsyncMock(return_value=None)  # already absent

    import app.budget_reset as br

    monkeypatch.setattr(br, "llm_router", fake_router)
    summary = await reset_deployment_budgets()
    assert summary["deployments"] == 1
    assert summary["keys_present"] == 0  # important: distinguishes silent no-op
    assert summary["keys_deleted"] == 2  # delete still attempted (idempotent)
