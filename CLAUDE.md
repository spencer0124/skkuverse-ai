# skkuverse-ai

skkuverse 서비스용 AI API 서버. LLM 라우팅 + 도메인 특화 endpoint 제공.

## 기술 스택

- FastAPI + uvicorn
- litellm==1.82.6 (SDK Router) — 버전 pin 필수 (1.82.7/1.82.8 supply chain 이슈)
- pydantic-settings (환경변수)
- Docker (python:3.12-slim)

## 프로젝트 구조

```
app/
├── main.py         ← FastAPI 엔트리포인트
├── config.py       ← 환경변수 로드
├── llm.py          ← litellm.Router 싱글턴
└── routes/
    ├── health.py   ← GET /health
    ├── chat.py     ← POST /v1/chat/completions (범용 OpenAI 호환)
    └── notices.py  ← POST /api/notices/summarize (공지 요약 + 타입 분류)
```

## LLM Provider Fallback (weight 순)

1. OpenAI gpt-4.1-mini (weight 100, 데이터 공유 인센티브 2.5M tok/일 무료 Tier 1-2, budget cap $0.60/일)
2. Cerebras qwen-3-235b-a22b-instruct-2507 (weight 2, 무료 티어)
3. Groq qwen/qwen3-32b (weight 1, 무료 티어)

429 발생 → 1회 실패 후 5분 쿨다운 → 다음 provider로 fallback.
공지 요약은 `response_format` (structured output)으로 JSON 스키마 강제.

**응답 스키마** (`NoticeSummary`)
- `oneLiner: str` — 50자 이내 부제목. 프런트에서 제목·마감일과 나란히 표시되므로 **제목 키워드와 날짜/시간/"까지"·"부터" 표현은 쓰지 않는다.** 본문에서만 알 수 있는 학생 관점의 핵심(대상·해야 할 행동·영향)을 압축. 학교 내부 부서·절차 같은 학생에게 불필요한 정보는 배제.
- `summary: str` — 2~4문장 해요체 요약.
- `type: str` — `action_required` / `event` / `informational`.
- `periods: list[NoticePeriod]` — 기간/일시 배열. 각 원소 `{label, startDate, startTime, endDate, endTime}`. 정보 없으면 `[]`.
- `locations: list[NoticeLocation]` — 장소 배열. 각 원소 `{label, detail}` (detail은 필수 문자열). 정보 없으면 `[]`.
- `details: {target, action, host, impact}` — 모두 nullable. `location`은 top-level `locations`로 이동.
- `label` 규칙: 원소 1개면 `null`, 2개 이상이면 각 원소 구분용 짧은 label (예: "1차 신청"/"2차 신청", "인사캠"/"자과캠").
- 출력 언어: 본문 Hangul 비율 ≥10%면 한국어, 아니면 영어로 `oneLiner`/`summary`/`details.*`/`*.label` 생성. 날짜·`locations[].detail`·`type`은 언어 무관 원본 유지. 감지 결과는 user prompt 맨 위 `[LANG: ko|en]` 태그로 LLM에 전달.

후처리
- `_guard_year` — 게시일 ±1년 벗어나는 `periods[].startDate/endDate` 교정. ±1년은 허용 (다음 학기 안내 등).
- `_strip_fillers` — `details` filler 패턴을 null로, `locations`에서 빈 문자열·비특정 장소(`집/온라인/각자/비대면/자택`)·filler 원소 제거.

## 개발 명령어

```bash
# 로컬 실행
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 4000 --reload

# Docker 실행
docker network create skkuverse 2>/dev/null
docker compose up -d

# 테스트
curl http://127.0.0.1:4000/health
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"llm","messages":[{"role":"user","content":"Hello"}],"max_tokens":50}'
curl -s http://127.0.0.1:4000/api/notices/summarize \
  -H "Content-Type: application/json" \
  -d '{"title":"등록금 납부 안내","category":"학사","cleanText":"납부기간: 2026.3.2~3.6 18:00. 재학생 대상.","date":"2026-03-01"}'
```

## 통신 방식

- Docker 네트워크 `skkuverse` 공유, 서비스 이름 `ai`로 호출
- 인증 없음 (Docker 네트워크 격리)
- 다른 서비스에서: `http://ai:4000/v1/chat/completions`

## 환경변수

- `OPENAI_API_KEY` — OpenAI API 키
- `CEREBRAS_API_KEY` — Cerebras API 키
- `GROQ_API_KEY` — Groq API 키

## endpoint 추가 시

1. `app/routes/`에 새 파일 생성
2. `app/main.py`에 `app.include_router()` 추가
