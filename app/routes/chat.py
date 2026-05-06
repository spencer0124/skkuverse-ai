import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm import router as llm_router

log = logging.getLogger(__name__)

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "llm"
    messages: list[ChatMessage]
    max_tokens: int | None = None
    temperature: float | None = None


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    kwargs: dict = {
        "model": req.model,
        "messages": [m.model_dump() for m in req.messages],
    }
    if req.max_tokens is not None:
        kwargs["max_tokens"] = req.max_tokens
    if req.temperature is not None:
        kwargs["temperature"] = req.temperature

    try:
        response = await llm_router.acompletion(**kwargs)
        return response.model_dump()
    except Exception as e:
        # Diagnostic logging before silent 502 — pre-2026-05-06 incident lesson.
        # Without this, the actual exception detail is only in the 502 response
        # body and never visible in container logs (uvicorn logs status only).
        log.warning(
            "llm_router.acompletion failed type=%s status=%s msg=%s",
            type(e).__name__,
            getattr(e, "status_code", None),
            str(e)[:1500],
        )
        raise HTTPException(status_code=502, detail=str(e))
