from litellm import Router

from app.config import settings

router = Router(
    model_list=[
        # --- Priority 1: OpenAI gpt-4.1-mini ---
        {
            "model_name": "llm",
            "litellm_params": {
                "model": "openai/gpt-4.1-mini",
                "api_key": settings.openai_api_key,
                "weight": 100,
                "rpm": 500,
                "tpm": 200_000,
                "max_budget": 0.60,
                "budget_duration": "1d",
            },
        },
        # --- Priority 2: Cerebras (무료, 카드 미연결) ---
        {
            "model_name": "llm",
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
