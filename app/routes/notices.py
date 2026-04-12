import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.llm import router as llm_router

log = logging.getLogger(__name__)

router = APIRouter()

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")

MAX_FORMAT_RETRIES = 1

SYSTEM_PROMPT = """\
대학교 공지사항 요약 AI.

## 출력 언어

user message 맨 위 `[LANG: ko]` 또는 `[LANG: en]` 태그를 반드시 따르세요.

- `[LANG: ko]`: oneLiner / summary / details.target·action·host·impact / periods[].label / locations[].label 을 한국어로. summary는 해요체.
- `[LANG: en]`: 위 필드를 자연스러운 영어로. summary는 친근한 평서문 (예: "Residents can ...", "The event runs from ...").
- 다음은 언어와 무관하게 원본 형태 유지:
  · periods[].startDate / startTime / endDate / endTime (YYYY-MM-DD / HH:mm)
  · locations[].detail (건물명·호실은 번역하지 않음. 한국어 본문이면 한국어, 영어 본문이면 영어 원문 그대로)
  · type enum (action_required / event / informational)
- 본문의 고유명사(인명·회사명·제품명·건물명)는 어느 언어에서도 번역하지 마세요.
- 영어 출력에서 "Notice:", "Announcement:", "FYI:" 같은 공지 상투어는 쓰지 마세요.

## 타입 판별

- action_required: 학생이 신청·제출·등록 등 해야 하는 공지. 행사라도 사전등록·신청이 필요하면 action_required.
- event: 특정 날짜에 열리는 행사·세미나·설명회 (별도 신청 불필요한 경우)
- informational: 시설 변경, 시스템 점검, 정책 안내 등

주의: 채용설명회라도 사전등록·신청이 필요하면 반드시 action_required. event는 참석만 하면 되는 순수 행사에만 사용.

## periods (기간/일시)

공지의 기간·일시 배열. 정보가 없으면 [].

- 각 원소: {label, startDate, startTime, endDate, endTime}
- 원소가 1개면 label은 null. 2개 이상이면 각 원소를 구분하는 짧은 label (예: "1차 신청", "2차 신청", "신입", "인턴")
- startDate: 활동·행사·신청이 시작되는 날. 마감일만 있고 시작일이 불명확하면 null, endDate만 채울 것.
- endDate: 마감·종료·만료일.
- 당일 행사(설명회, 특강 등)는 startDate와 endDate가 같아도 정상.
- 날짜는 YYYY-MM-DD, 시간은 HH:mm (24시간). 없으면 null.
- 본문에 연도가 명시되어 있지 않으면 게시일의 연도를 사용하세요.

## details

해당 정보가 본문에 명시적으로 없으면 반드시 null. 추측하거나 일반론을 쓰지 마세요.

- target: 대상 (누구에게 해당하는지). "전체 학생", "재학생" 등 뻔하면 null
- action: **학생이** 구체적으로 해야 할 일. 학교·기관의 조치는 action이 아님
- host: 주관/주최
- impact: 학생에게 미치는 영향 또는 대안

## locations (장소)

장소 배열. 정보가 없으면 [].

- 각 원소: {label, detail}
- detail: 구체적 건물명·호실·주소 (필수 문자열)
- label: 원소가 1개면 null. 2개 이상이면 각 원소를 구분하는 짧은 label (예: "인사캠", "자과캠", "본선", "예선")
- "집", "온라인", "각자", "비대면" 등 비특정 장소는 원소를 생략. 결과적으로 장소 정보가 하나도 없으면 [].

## oneLiner / summary

- oneLiner: 50자 이내. 부제목 칸에 들어가며, 제목 칸·마감일 칸과 같은 화면에 동시에 표시됩니다. 따라서:
  1) 제목에 이미 있는 단어(주최사명·행사명·공지 유형)는 반복하지 마세요.
  2) 날짜·시간·"까지"·"부터" 등 기간 표현은 쓰지 마세요. 날짜는 periods에 별도로 담깁니다.
  3) 본문에서만 알 수 있는 핵심 정보를 씁니다: 대상(누가), 학생이 해야 할 행동(무엇을), 혜택·영향 중 학생에게 가장 의미 있는 1~2개를 골라 짧게.
  4) 본문에 제목·마감일 외 정보가 거의 없으면 details.target/action/host/impact 중 학생 입장에서 가장 구체적이고 행동 가능한 하나를 골라 한 구절로 압축하세요. 학교 내부 절차·부서명 같은 학생에게 불필요한 정보는 쓰지 마세요.
  5) 한국어: "안내", "공고", "~드림" 같은 공지 상투어는 빼세요. 영어: "Notice", "Announcement", "FYI", "Reminder:" 같은 상투어도 똑같이 빼세요.
- summary: 2~4문장. [LANG: ko]면 해요체 ("~할 수 있어요.", "~진행돼요."), [LANG: en]이면 친근한 평서문. 학생에게 중요한 정보 위주.

## 예시

입력: 제목: 안전교육 이수 안내 / 본문: 전체 재학생은 LMS에서 안전교육을 4월 20일까지 이수해야 합니다.
```json
{"type":"action_required","oneLiner":"전체 재학생 LMS 필수 이수, 미이수 시 수강신청 제한","summary":"전체 재학생은 LMS에서 안전교육을 4월 20일까지 이수해야 해요. 미이수 시 수강신청이 제한될 수 있어요.","periods":[{"label":null,"startDate":null,"startTime":null,"endDate":"2026-04-20","endTime":null}],"locations":[],"details":{"target":"전체 재학생","action":"LMS에서 안전교육 이수","host":null,"impact":null}}
```

입력: 제목: 2026-1학기 교육과정 로드맵 공개 / 본문: 교육과정 로드맵이 학과 홈페이지에 게시되었습니다.
```json
{"type":"informational","oneLiner":"학과 홈페이지 게시, 수강 계획 수립 참고 자료","summary":"2026-1학기 교육과정 로드맵이 학과 홈페이지에 공개됐어요. 수강 계획 수립 시 참고하세요.","periods":[],"locations":[],"details":{"target":null,"action":null,"host":null,"impact":"수강 계획 수립 시 참고"}}
```

입력: 제목: 삼성전자 채용설명회 / 본문: 삼성전자에서 이공계 재학생 대상 채용설명회를 4월 15일 14시에 경영관 33101호에서 진행합니다. 캠퍼스 리크루팅 포털에서 사전 신청 필수.
```json
{"type":"event","oneLiner":"이공계 재학생 대상, 캠퍼스 리크루팅 포털 사전신청 필수","summary":"삼성전자에서 이공계 재학생 대상 채용설명회를 4월 15일 14시에 경영관 33101호에서 진행해요. 캠퍼스 리크루팅 포털에서 사전 신청이 필요해요.","periods":[{"label":null,"startDate":"2026-04-15","startTime":"14:00","endDate":"2026-04-15","endTime":null}],"locations":[{"label":null,"detail":"경영관 33101호"}],"details":{"target":"이공계 재학생","action":"사전 신청 필수 (캠퍼스 리크루팅 포털)","host":"삼성전자","impact":null}}
```

입력: 제목: 2026-1학기 등록금 납부 안내 / 본문: 1차 납부기간 2월 10일~14일, 2차(추가) 납부기간 2월 24일~26일. 납부처: 인사캠 600주년기념관 재무팀, 자과캠 학생회관 재무팀.
```json
{"type":"action_required","oneLiner":"재학생 전체 대상, 고지서 확인 후 납부","summary":"2026-1학기 등록금을 1차(2월 10~14일) 또는 2차 추가기간(2월 24~26일)에 납부해야 해요. 인사캠·자과캠 재무팀에서 처리할 수 있어요.","periods":[{"label":"1차 납부","startDate":"2026-02-10","startTime":null,"endDate":"2026-02-14","endTime":null},{"label":"2차 추가납부","startDate":"2026-02-24","startTime":null,"endDate":"2026-02-26","endTime":null}],"locations":[{"label":"인사캠","detail":"600주년기념관 재무팀"},{"label":"자과캠","detail":"학생회관 재무팀"}],"details":{"target":null,"action":"등록금 납부","host":null,"impact":null}}
```

입력:
[LANG: en]
게시일: 2026-02-15
제목: Spring 2026 Dormitory Check-in Guide
카테고리: 생활관
본문:
Spring 2026 check-in for new residents runs Feb 28 to Mar 2, 09:00-18:00 at the Front Desk (Myeongnyun Hall). Late arrivals after Mar 2 must report to the Resident Manager's office on the 1st floor. Bring your student ID and ARC.
```json
{"type":"action_required","oneLiner":"New residents must bring student ID and ARC to check in","summary":"New residents check in at the Myeongnyun Hall Front Desk from Feb 28 to Mar 2, 09:00-18:00. Arrivals after Mar 2 should report to the Resident Manager's office on the 1st floor. Remember to bring your student ID and ARC.","periods":[{"label":"Check-in window","startDate":"2026-02-28","startTime":"09:00","endDate":"2026-03-02","endTime":"18:00"}],"locations":[{"label":"Check-in","detail":"Myeongnyun Hall Front Desk"},{"label":"Late arrival","detail":"Resident Manager's office, 1F"}],"details":{"target":"New residents","action":"Check in with student ID and ARC","host":null,"impact":"Late arrivals report to Resident Manager's office"}}
```"""


class NoticePeriod(BaseModel):
    """공지의 기간·일시 원소. 원소가 1개면 label은 null."""
    label: str | None = None
    startDate: str | None = None
    startTime: str | None = None
    endDate: str | None = None
    endTime: str | None = None


class NoticeLocation(BaseModel):
    """공지의 장소 원소. detail은 구체적 건물명·호실·주소 (필수)."""
    label: str | None = None
    detail: str = Field(min_length=1)


class NoticeDetails(BaseModel):
    """공지 메타. 모든 타입에서 사용 가능, 해당 없으면 null."""
    target: str | None = None
    action: str | None = None
    host: str | None = None
    impact: str | None = None


class NoticeSummary(BaseModel):
    """LLM이 반환할 JSON 스키마 (response_format용)."""
    oneLiner: str
    summary: str
    type: str
    periods: list[NoticePeriod] = []
    locations: list[NoticeLocation] = []
    details: NoticeDetails

    @model_validator(mode="after")
    def check_date_time_formats(self):
        for i, p in enumerate(self.periods):
            for field_name, pattern in [
                ("startDate", DATE_RE),
                ("endDate", DATE_RE),
                ("startTime", TIME_RE),
                ("endTime", TIME_RE),
            ]:
                value = getattr(p, field_name)
                if value is not None and not pattern.match(value):
                    fmt = "YYYY-MM-DD" if "Date" in field_name else "HH:mm"
                    raise ValueError(
                        f"periods[{i}].{field_name}은 {fmt} 형식이어야 합니다. 받은 값: '{value}'"
                    )
        return self


class SummarizeRequest(BaseModel):
    title: str
    category: str = ""
    cleanText: str
    date: str | None = None


class SummarizeResponse(NoticeSummary):
    model: str | None = None


_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_ALPHA_RE = re.compile(r"[A-Za-z\uac00-\ud7a3]")


def _detect_language(*texts: str) -> str:
    """본문/제목을 순서대로 훑어 Hangul 비율 ≥10%면 'ko', 아니면 'en'.
    모든 입력이 공백/빈 문자열이면 기본값 'ko'."""
    combined = " ".join(t for t in texts if t).strip()
    if not combined:
        return "ko"
    alpha = _ALPHA_RE.findall(combined)
    if not alpha:
        return "ko"
    hangul_ratio = len(_HANGUL_RE.findall(combined)) / len(alpha)
    return "ko" if hangul_ratio >= 0.1 else "en"


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
    r"^(없음|해당\s*없음|특별한\s*(영향|사항)\s*없음|없습니다"
    r"|N/?A|none|not\s*applicable|no\s*(impact|effect|host))$",
    re.IGNORECASE,
)


# 비특정 장소 — locations 원소에서 제거 대상.
# 모든 항목은 반드시 lowercase. 비교 시 .strip().lower() 기준.
_NONSPECIFIC_LOC = {
    "집", "온라인", "각자", "비대면", "자택",
    "home", "online", "remote", "tba", "tbd", "n/a",
    "each student", "your room", "anywhere",
}


_ONELINER_PREFIX_RE = re.compile(
    r"^\s*(notice|announcement|fyi|reminder|공지|안내|알림)\s*[:\-–—]\s*",
    re.IGNORECASE,
)


def _guard_year(summary: NoticeSummary, pub_date: str | None) -> NoticeSummary:
    """게시일 ±1년 벗어나는 연도를 각 period마다 교정. ±1년은 허용 (다음 학기 안내 등)."""
    if not pub_date:
        return summary
    pub_year = datetime.strptime(pub_date, "%Y-%m-%d").year

    for period in summary.periods:
        for field in ("startDate", "endDate"):
            val = getattr(period, field, None)
            if not val:
                continue
            try:
                dt = datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                continue
            if abs(dt.year - pub_year) <= 1:
                continue
            corrected = dt.replace(year=pub_year)
            setattr(period, field, corrected.strftime("%Y-%m-%d"))

    return summary


def _strip_fillers(summary: NoticeSummary) -> NoticeSummary:
    """details filler를 null로, locations에서 빈/비특정/filler 원소를 제거."""
    if summary.details:
        for field_name in summary.details.model_fields:
            val = getattr(summary.details, field_name)
            if isinstance(val, str) and _FILLER_PATTERNS.match(val.strip()):
                setattr(summary.details, field_name, None)
    summary.locations = [
        loc for loc in summary.locations
        if loc.detail
        and loc.detail.strip()
        and loc.detail.strip().lower() not in _NONSPECIFIC_LOC
        and not _FILLER_PATTERNS.match(loc.detail.strip())
    ]
    return summary


def _strip_oneliner_prefix(summary: NoticeSummary) -> NoticeSummary:
    """oneLiner 앞에 붙은 'Notice:', 'Announcement:', '공지:' 등 상투어 prefix 제거."""
    summary.oneLiner = _ONELINER_PREFIX_RE.sub("", summary.oneLiner).strip()
    return summary


def _enforce_language(
    summary: NoticeSummary, lang: str, model_name: str | None
) -> None:
    """출력 언어 misalignment 감지 — warning 로그만 남기고 반환값 없음."""
    combined = f"{summary.oneLiner} {summary.summary}"
    alpha = _ALPHA_RE.findall(combined)
    hangul_ratio = (
        len(_HANGUL_RE.findall(combined)) / len(alpha) if alpha else 0
    )

    if lang == "en" and hangul_ratio > 0:
        log.warning(
            "lang_mismatch: expected=en but output contains Hangul "
            "(ratio=%.2f, model=%s, oneLiner=%r)",
            hangul_ratio, model_name, summary.oneLiner,
        )
    elif lang == "ko" and alpha and hangul_ratio < 0.1:
        log.warning(
            "lang_mismatch: expected=ko but output is mostly non-Korean "
            "(ratio=%.2f, model=%s, oneLiner=%r)",
            hangul_ratio, model_name, summary.oneLiner,
        )


@router.post("/api/notices/summarize", response_model=SummarizeResponse)
async def summarize_notice(req: SummarizeRequest):
    lang = _detect_language(req.cleanText, req.title)
    date_line = f"게시일: {req.date}\n" if req.date else ""
    user_prompt = (
        f"[LANG: {lang}]\n"
        f"{date_line}제목: {req.title}\n카테고리: {req.category}\n본문:\n{req.cleanText}"
    )

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
                summary = _strip_oneliner_prefix(summary)
                _enforce_language(summary, lang, model_name)
                return SummarizeResponse(**summary.model_dump(), model=model_name)
            return SummarizeResponse(
                oneLiner="", summary="", type="unknown",
                periods=[], locations=[],
                details=NoticeDetails(), model=model_name,
            )

        last_valid_parsed = parsed

        try:
            summary = NoticeSummary.model_validate(parsed)
            summary = _guard_year(summary, req.date)
            summary = _strip_fillers(summary)
            summary = _strip_oneliner_prefix(summary)
            _enforce_language(summary, lang, model_name)
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
    summary = _strip_oneliner_prefix(summary)
    _enforce_language(summary, lang, model_name)
    return SummarizeResponse(**summary.model_dump(), model=model_name)


def _safe_summary(parsed: dict) -> dict:
    """periods(날짜/시간)를 제외하고 NoticeSummary에 안전하게 넣을 수 있는 dict 반환.
    locations는 검증 실패 원인이 아니므로 보존 시도."""
    details_raw = parsed.get("details") or {}
    if isinstance(details_raw, dict):
        details = NoticeDetails(**{
            k: details_raw.get(k)
            for k in NoticeDetails.model_fields
        })
    else:
        details = NoticeDetails()

    safe_locations: list[NoticeLocation] = []
    for raw_loc in parsed.get("locations") or []:
        if not isinstance(raw_loc, dict):
            continue
        detail = raw_loc.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            continue
        safe_locations.append(
            NoticeLocation(label=raw_loc.get("label"), detail=detail)
        )

    return {
        "oneLiner": parsed.get("oneLiner", ""),
        "summary": parsed.get("summary", ""),
        "type": parsed.get("type", "unknown"),
        "periods": [],
        "locations": safe_locations,
        "details": details,
    }
