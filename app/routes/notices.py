import json
import re
from datetime import datetime

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

주의: 채용설명회라도 사전등록·신청이 필요하면 반드시 action_required. event는 참석만 하면 되는 순수 행사에만 사용.

## 날짜/시간 규칙

- startDate: 활동·행사·신청이 시작되는 날.
- endDate: 마감·종료·만료일.
- 마감일만 있고 시작일이 불명확하면 startDate는 null, endDate만 채울 것.
- 당일 행사(설명회, 특강 등)는 startDate와 endDate가 같아도 정상.
- 날짜는 YYYY-MM-DD, 시간은 HH:mm (24시간). 해당 정보가 없으면 null.
- 본문에 연도가 명시되어 있지 않으면 게시일의 연도를 사용하세요.

## details

모든 타입에서 모든 필드를 사용할 수 있음. 해당 정보가 본문에 명시적으로 없으면 반드시 null. 추측하거나 일반론을 쓰지 마세요.

- target: 대상 (누구에게 해당하는지). "전체 학생", "재학생" 등 뻔하면 null
- action: **학생이** 구체적으로 해야 할 일. 학교·기관의 조치는 action이 아님
- location: 구체적 건물명·호실·주소만. "집", "온라인", "각자", "비대면" 등 비특정 장소는 반드시 null
- host: 주관/주최
- impact: 학생에게 미치는 영향 또는 대안

## oneLiner / summary

- oneLiner: 50자 이내. "안내", "공고" 빼고, 날짜 포함. 제목을 그대로 쓰지 말고 날짜+핵심 압축.
- summary: 2~4문장. 해요체. 예: "~할 수 있어요.", "~진행돼요." 학생에게 중요한 정보 위주.

## 예시

입력: 제목: 안전교육 이수 안내 / 본문: 전체 재학생은 LMS에서 안전교육을 4월 20일까지 이수해야 합니다.
```json
{"type":"action_required","oneLiner":"2026-04-20까지 안전교육 이수 필수","summary":"전체 재학생은 LMS에서 안전교육을 4월 20일까지 이수해야 해요. 미이수 시 수강신청이 제한될 수 있어요.","startDate":null,"startTime":null,"endDate":"2026-04-20","endTime":null,"details":{"target":"전체 재학생","action":"LMS에서 안전교육 이수","location":null,"host":null,"impact":null}}
```

입력: 제목: 2026-1학기 교육과정 로드맵 공개 / 본문: 교육과정 로드맵이 학과 홈페이지에 게시되었습니다.
```json
{"type":"informational","oneLiner":"2026-1학기 교육과정 로드맵 공개","summary":"2026-1학기 교육과정 로드맵이 학과 홈페이지에 공개됐어요. 수강 계획 수립 시 참고하세요.","startDate":null,"startTime":null,"endDate":null,"endTime":null,"details":{"target":null,"action":null,"location":null,"host":null,"impact":"수강 계획 수립 시 참고"}}
```

입력: 제목: 삼성전자 채용설명회 / 본문: 삼성전자에서 이공계 재학생 대상 채용설명회를 4월 15일 14시에 경영관 33101호에서 진행합니다. 캠퍼스 리크루팅 포털에서 사전 신청 필수.
```json
{"type":"event","oneLiner":"2026-04-15 14:00 삼성전자 채용설명회","summary":"삼성전자에서 이공계 재학생 대상 채용설명회를 4월 15일 14시에 경영관 33101호에서 진행해요. 캠퍼스 리크루팅 포털에서 사전 신청이 필요해요.","startDate":"2026-04-15","startTime":"14:00","endDate":"2026-04-15","endTime":null,"details":{"target":"이공계 재학생","action":"사전 신청 필수 (캠퍼스 리크루팅 포털)","location":"경영관 33101호","host":"삼성전자","impact":null}}
```"""


class NoticeDetails(BaseModel):
    """공지 메타. 모든 타입에서 사용 가능, 해당 없으면 null."""
    target: str | None = None
    action: str | None = None
    location: str | None = None
    host: str | None = None
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
    date: str | None = None


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


_FILLER_PATTERNS = re.compile(
    r"^(없음|해당\s*없음|특별한\s*(영향|사항)\s*없음|없습니다|N/?A)$",
    re.IGNORECASE,
)


def _guard_year(summary: NoticeSummary, pub_date: str | None) -> NoticeSummary:
    """게시일 ±1년 벗어나는 연도를 교정. 범위 안이면 no-op. pub_date 없으면 no-op."""
    if not pub_date:
        return summary
    pub_year = datetime.strptime(pub_date, "%Y-%m-%d").year

    for field in ("startDate", "endDate"):
        val = getattr(summary, field, None)
        if not val:
            continue
        try:
            dt = datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            continue
        if abs(dt.year - pub_year) <= 1:
            continue
        corrected = dt.replace(year=pub_year)
        setattr(summary, field, corrected.strftime("%Y-%m-%d"))

    return summary


def _strip_fillers(summary: NoticeSummary) -> NoticeSummary:
    """details 필드 중 filler 패턴을 null로 교정."""
    if summary.details:
        for field_name in summary.details.model_fields:
            val = getattr(summary.details, field_name)
            if isinstance(val, str) and _FILLER_PATTERNS.match(val.strip()):
                setattr(summary.details, field_name, None)
    return summary


@router.post("/api/notices/summarize", response_model=SummarizeResponse)
async def summarize_notice(req: SummarizeRequest):
    date_line = f"게시일: {req.date}\n" if req.date else ""
    user_prompt = f"{date_line}제목: {req.title}\n카테고리: {req.category}\n본문:\n{req.cleanText}"

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
                summary = NoticeSummary(**_safe_summary(last_valid_parsed))
                summary = _guard_year(summary, req.date)
                summary = _strip_fillers(summary)
                return SummarizeResponse(**summary.model_dump(), model=model_name)
            return SummarizeResponse(
                oneLiner="", summary="", type="unknown",
                details=NoticeDetails(), model=model_name,
            )

        last_valid_parsed = parsed

        try:
            summary = NoticeSummary.model_validate(parsed)
            summary = _guard_year(summary, req.date)
            summary = _strip_fillers(summary)
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
    summary = NoticeSummary(**_safe_summary(last_valid_parsed))
    summary = _guard_year(summary, req.date)
    summary = _strip_fillers(summary)
    return SummarizeResponse(**summary.model_dump(), model=model_name)


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
