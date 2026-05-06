from contextlib import asynccontextmanager
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.budget_reset import reset_deployment_budgets
from app.routes import chat, health, notices

_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Wire APScheduler so litellm Router's budget cache is cleared at exact
    00:00 UTC daily — keeping it aligned with OpenAI's free-quota reset.

    See app/budget_reset.py for full background. A host-level
    `docker restart skkuverse-ai-ai-1` cron at 00:05 UTC is the
    defense-in-depth fallback if this in-process scheduler ever misses.
    """
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=timezone.utc)
    _scheduler.add_job(
        reset_deployment_budgets,
        CronTrigger(hour=0, minute=0, second=0, timezone=timezone.utc),
        id="daily_budget_reset",
        name="Daily litellm budget cache reset (00:00 UTC)",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    _scheduler.start()
    yield
    _scheduler.shutdown(wait=False)


app = FastAPI(title="skkuverse-ai", version="1.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(notices.router)
