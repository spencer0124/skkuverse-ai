from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm import router as llm_router

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
        raise HTTPException(status_code=502, detail=str(e))
