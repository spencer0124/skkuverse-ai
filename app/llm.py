from litellm import Router

from app.config import settings

router = Router(
    model_list=[
        # --- Priority 1: OpenAI gpt-4.1-mini ---
        # max_budget raised from 0.60 → 5.00 (2026-05-06): the prior cap was
        # smaller than realistic backlog drain spikes, and the in-memory budget
        # window drifted (first-call+24h, not calendar midnight) so once
        # exceeded it stuck for ~24h. We pair this raise with a daily 00:00 UTC
        # cache-clear via app/budget_reset.py so paid spend can never get stuck.
        {
            "model_name": "llm",
            "model_info": {"id": "openai-gpt41-mini"},
            "litellm_params": {
                "model": "openai/gpt-4.1-mini",
                "api_key": settings.openai_api_key,
                "weight": 100,
                "rpm": 500,
                "tpm": 200_000,
                "max_budget": 5.00,
                "budget_duration": "1d",
            },
        },
        # --- Priority 2: Cerebras (무료, 카드 미연결) ---
        {
            "model_name": "llm",
            "model_info": {"id": "cerebras-qwen3"},
            "litellm_params": {
                "model": "cerebras/qwen-3-235b-a22b-instruct-2507",
                "api_key": settings.cerebras_api_key,
                "weight": 2,
                "rpm": 30,
                "tpm": 30_000,
            },
        },
        # --- Priority 3: Groq (무료, 카드 미연결) ---
        {
            "model_name": "llm",
            "model_info": {"id": "groq-qwen3"},
            "litellm_params": {
                "model": "groq/qwen/qwen3-32b",
                "api_key": settings.groq_api_key,
                "weight": 1,
                "rpm": 60,
                "tpm": 6_000,
            },
        },
    ],
    enable_pre_call_checks=True,
    allowed_fails=1,
    cooldown_time=300,
    num_retries=2,
    routing_strategy="simple-shuffle",
)
