import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.llm import router as llm_router

router = APIRouter()

SYSTEM_PROMPT = """\
대학교 공지사항 요약 AI. 아래 공지를 읽고 JSON으로만 응답해.

## 1단계: 타입 판별

- action_required: 학생이 신청·제출·등록 등 해야 하는 공지. 행사라도 사전등록·신청이 필요하면 action_required.
- event: 특정 날짜에 열리는 행사·세미나·설명회 (별도 신청 불필요한 경우)
- informational: 시설 변경, 시스템 점검, 정책 안내 등

## 2단계: 타입별 details 추출

action_required:
  - target: 대상. "전체 학생", "재학생", "모든 학생"처럼 뻔하면 null
  - action: 구체적으로 해야 할 일
  - deadline: YYYY-MM-DD. 없으면 null

event:
  - eventDate: YYYY-MM-DD HH:mm. 시간 불명확하면 YYYY-MM-DD
  - location: 장소 (캠퍼스명 포함)
  - host: 주최

informational:
  - what: 무엇이 바뀌는지
  - period: YYYY-MM-DD ~ YYYY-MM-DD. 시간이 중요하면 HH:mm 포함 가능. 없으면 null
  - impact: 학생에게 미치는 영향 또는 대안

## 3단계: 공통 필드

- oneLiner: 50자 이내. "안내", "공고" 같은 단어 빼고, 반드시 날짜 포함. 제목을 그대로 쓰지 말고 날짜+핵심을 압축.
- summary: 2~4문장. 해요체로. 예시: "~할 수 있어요.", "~진행돼요.", "~필요해요." 본문에서 학생에게 중요한 정보 위주.

## 응답

JSON만 출력. 마크다운 코드블록 없이.

{"oneLiner":"","summary":"","type":"","details":{}}"""


def parse_llm_json(raw: str) -> dict | None:
    """LLM 응답에서 JSON 추출. <think>, ```json```, 기타 wrapper 처리."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(1)
    else:
        m2 = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if m2:
            cleaned = m2.group(1)

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None


class SummarizeRequest(BaseModel):
    title: str
    category: str = ""
    cleanText: str


class SummarizeResponse(BaseModel):
    oneLiner: str | None = None
    summary: str | None = None
    type: str | None = None
    details: dict | None = None
    model: str | None = None


@router.post("/api/notices/summarize", response_model=SummarizeResponse)
async def summarize_notice(req: SummarizeRequest):
    user_prompt = f"제목: {req.title}\n카테고리: {req.category}\n본문:\n{req.cleanText}"

    try:
        response = await llm_router.acompletion(
            model="llm",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
            max_tokens=1024,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    raw = response.choices[0].message.content.strip()
    model_name = getattr(response, "model", None)

    parsed = parse_llm_json(raw)
    if parsed is None:
        return SummarizeResponse(model=model_name)

    return SummarizeResponse(
        oneLiner=parsed.get("oneLiner"),
        summary=parsed.get("summary"),
        type=parsed.get("type"),
        details=parsed.get("details"),
        model=model_name,
    )
