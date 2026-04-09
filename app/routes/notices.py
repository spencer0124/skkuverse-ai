import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError, model_validator

from app.llm import router as llm_router

router = APIRouter()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

MAX_FORMAT_RETRIES = 1

SYSTEM_PROMPT = """\
대학교 공지사항 요약 AI.

## 타입 판별

- action_required: 학생이 신청·제출·등록 등 해야 하는 공지. 행사라도 사전등록·신청이 필요하면 action_required.
- event: 특정 날짜에 열리는 행사·세미나·설명회 (별도 신청 불필요한 경우)
- informational: 시설 변경, 시스템 점검, 정책 안내 등

## 날짜/시간 규칙

- startDate: 시작일. endDate: 마감일 또는 종료일 (신청 마감, 납부 기한, 행사 종료 등 모두 포함).
- 날짜는 YYYY-MM-DD, 시간은 HH:mm (24시간). 해당 정보가 없으면 null.

## 타입별 details

action_required:
  - target: 대상. "전체 학생", "재학생" 등 뻔하면 null
  - action: 구체적으로 해야 할 일

event:
  - location: 장소 (캠퍼스명 포함)
  - host: 주최

informational:
  - what: 무엇이 바뀌는지
  - impact: 학생에게 미치는 영향 또는 대안

해당 타입이 아닌 details 필드는 null.

## oneLiner / summary

- oneLiner: 50자 이내. "안내", "공고" 빼고, 날짜 포함. 제목을 그대로 쓰지 말고 날짜+핵심 압축.
- summary: 2~4문장. 해요체. 예: "~할 수 있어요.", "~진행돼요." 학생에게 중요한 정보 위주."""


class NoticeDetails(BaseModel):
    """타입별 고유 메타. 해당 타입이 아닌 필드는 null."""
    target: str | None = None
    action: str | None = None
    location: str | None = None
    host: str | None = None
    what: str | None = None
    impact: str | None = None


class NoticeSummary(BaseModel):
    """LLM이 반환할 JSON 스키마 (response_format용)."""
    oneLiner: str
    summary: str
    type: str
    startDate: str | None = None
    startTime: str | None = None
    endDate: str | None = None
    endTime: str | None = None
    details: NoticeDetails

    @model_validator(mode="after")
    def check_date_time_formats(self):
        for field_name, pattern in [
            ("startDate", DATE_RE),
            ("endDate", DATE_RE),
            ("startTime", TIME_RE),
            ("endTime", TIME_RE),
        ]:
            value = getattr(self, field_name)
            if value is not None and not pattern.match(value):
                fmt = "YYYY-MM-DD" if "Date" in field_name else "HH:mm"
                raise ValueError(f"{field_name}은 {fmt} 형식이어야 합니다. 받은 값: '{value}'")
        return self


class SummarizeRequest(BaseModel):
    title: str
    category: str = ""
    cleanText: str


class SummarizeResponse(NoticeSummary):
    model: str | None = None


def _parse_llm_json(raw: str) -> dict | None:
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


@router.post("/api/notices/summarize", response_model=SummarizeResponse)
async def summarize_notice(req: SummarizeRequest):
    user_prompt = f"제목: {req.title}\n카테고리: {req.category}\n본문:\n{req.cleanText}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    model_name = None
    last_valid_parsed: dict | None = None

    for attempt in range(1 + MAX_FORMAT_RETRIES):
        try:
            response = await llm_router.acompletion(
                model="llm",
                messages=messages,
                temperature=0,
                max_tokens=1024,
                response_format=NoticeSummary,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

        raw = response.choices[0].message.content.strip()
        model_name = getattr(response, "model", None)

        parsed = _parse_llm_json(raw)
        if parsed is None:
            if last_valid_parsed is not None:
                return SummarizeResponse(
                    **_safe_summary(last_valid_parsed), model=model_name
                )
            return SummarizeResponse(
                oneLiner="", summary="", type="unknown",
                details=NoticeDetails(), model=model_name,
            )

        last_valid_parsed = parsed

        try:
            summary = NoticeSummary.model_validate(parsed)
            return SummarizeResponse(**summary.model_dump(), model=model_name)
        except ValidationError as ve:
            if attempt < MAX_FORMAT_RETRIES:
                messages.append({"role": "assistant", "content": raw})
                errors = "; ".join(e["msg"] for e in ve.errors())
                messages.append({
                    "role": "user",
                    "content": (
                        f"형식 오류: {errors}\n"
                        "날짜는 반드시 YYYY-MM-DD, 시간은 HH:mm 형식이어야 해. "
                        "수정된 JSON만 다시 출력해."
                    ),
                })

    # 재시도 소진 — 날짜 빼고 반환
    return SummarizeResponse(
        **_safe_summary(last_valid_parsed), model=model_name
    )


def _safe_summary(parsed: dict) -> dict:
    """날짜/시간 필드를 제외하고 NoticeSummary에 안전하게 넣을 수 있는 dict 반환."""
    details_raw = parsed.get("details") or {}
    if isinstance(details_raw, dict):
        details = NoticeDetails(**{
            k: details_raw.get(k)
            for k in NoticeDetails.model_fields
        })
    else:
        details = NoticeDetails()

    return {
        "oneLiner": parsed.get("oneLiner", ""),
        "summary": parsed.get("summary", ""),
        "type": parsed.get("type", "unknown"),
        "details": details,
    }
