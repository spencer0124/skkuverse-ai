"""Daily budget cache reset at 00:00 UTC for litellm Router.

Background — why this exists
============================
litellm 1.82.6's Router tracks per-deployment paid spend in a DualCache
(in-memory + optional Redis). The cache keys, built per call at
`router_strategy/budget_limiter.py:453-454`, are:

    deployment_spend:{model_id}:{budget_duration}     (cumulative spend counter)
    deployment_budget_start_time:{model_id}           (window-open timestamp)

When the spend counter exceeds `max_budget`, the deployment is removed from the
routing pool until the window expires. The window is TTL-driven from the
*first call timestamp*, NOT calendar-aligned — so it drifts off OpenAI's
own free-quota reset (00:00 UTC).

We saw this drift cause a 12+ hour outage on 2026-05-05: the cap activated at
~12:20 UTC and persisted past the 00:00 UTC reset, recovering only at ~03:20
UTC the next day (= 24h after the prior day's first call).

To prevent recurrence, this module exposes `reset_deployment_budgets()` which
deletes the two cache keys per deployment. APScheduler in `app/main.py` calls
it daily at 00:00 UTC. After deletion, litellm auto-reinitializes on the next
call — fresh budget window aligned to midnight UTC.

Implementation notes
====================
- `model_info.id` on each deployment in `app/llm.py` gives stable, predictable
  keys. Without it, litellm assigns random UUIDs at init that change per
  process — making the keys unreachable from outside `acompletion`.
- Defense-in-depth: a host-level cron `docker restart skkuverse-ai-ai-1` at
  00:05 UTC catches the case where this in-process reset fails for any reason.
- After reset, we log whether the keys actually existed before deletion. If
  they're always absent at 00:00 UTC, that itself is a signal worth seeing
  (could mean cache layout changed across litellm versions).

Risk
====
This relies on litellm internal cache keys (no public reset API in 1.82.6).
The format is verified line-by-line against budget_limiter.py:453-454 of the
pinned 1.82.6 release. If we upgrade litellm, this module's keys must be
re-verified — see `tests/test_budget_reset.py` for the format invariants.
"""

from __future__ import annotations

import logging

from app.llm import router as llm_router

log = logging.getLogger(__name__)


def _build_keys(model_id: str, duration: str) -> tuple[str, str]:
    """Build the two cache keys for a deployment (deterministic, mirrors litellm)."""
    return (
        f"deployment_spend:{model_id}:{duration}",
        f"deployment_budget_start_time:{model_id}",
    )


async def reset_deployment_budgets() -> dict[str, int]:
    """Clear all deployment budget cache keys and return a summary.

    Returns: {"deployments": N, "keys_present": K, "keys_deleted": K}
    The summary is logged and returned so unit tests can assert behavior.
    Never raises — APScheduler must not die from a single failure.
    """
    summary = {"deployments": 0, "keys_present": 0, "keys_deleted": 0}
    try:
        budget_logger = getattr(llm_router, "router_budget_logger", None)
        if not budget_logger:
            log.warning("budget_reset_skipped reason=no_router_budget_logger")
            return summary

        cache = getattr(budget_logger, "dual_cache", None)
        if cache is None:
            log.warning("budget_reset_skipped reason=no_dual_cache")
            return summary

        for entry in llm_router.model_list:
            model_id = (entry.get("model_info") or {}).get("id")
            duration = (entry.get("litellm_params") or {}).get("budget_duration")
            if not model_id or not duration:
                continue
            summary["deployments"] += 1

            spend_key, start_time_key = _build_keys(model_id, duration)
            for key in (spend_key, start_time_key):
                # Probe presence first so we get observability into whether the
                # cache layer changed shape (silent no-op detection).
                existed = await cache.async_get_cache(key)
                if existed is not None:
                    summary["keys_present"] += 1
                await cache.async_delete_cache(key)
                summary["keys_deleted"] += 1

        log.info(
            "budget_reset_complete deployments=%d keys_present=%d keys_deleted=%d",
            summary["deployments"],
            summary["keys_present"],
            summary["keys_deleted"],
        )
    except Exception as exc:  # noqa: BLE001 - never break scheduler
        log.exception("budget_reset_failed exc=%s", exc)
    return summary
