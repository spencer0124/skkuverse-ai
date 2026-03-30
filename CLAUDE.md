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
    └── notices.py  ← POST /api/notices/summarize (공지 3줄 요약)
```

## LLM Provider Fallback (order 순)

1. OpenAI gpt-4o-mini (데이터 공유 10M tok/일 무료)
2. Cerebras qwen-3-235b-a22b-instruct-2507 (무료 티어)
3. Groq qwen/qwen3-32b (무료 티어)

429 발생 → 1회 실패 후 5분 쿨다운 → 다음 provider로 fallback.

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
  -d '{"content":"2026학년도 1학기 등록금 납부 안내..."}'
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
