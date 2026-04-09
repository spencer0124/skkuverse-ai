# LLM Provider 전략

## 라우팅 순서

OpenAI gpt-4.1-mini (weight 100) → Cerebras (weight 2) → Groq (weight 1)

OpenAI를 1순위로 사용하되 일일 budget cap ($0.60)으로 과금 제한. 초과 시 무료 provider로 fallback.

## Provider 비교

| | OpenAI (1순위) | Cerebras (2순위) | Groq (3순위) |
|---|---|---|---|
| **모델** | gpt-4.1-mini | Qwen3-235B-A22B (MoE) | Qwen3-32B (Dense) |
| **파라미터** | 비공개 | 235B 총 / 22B 활성 | 32B |
| **RPM** | 500 (Tier 1) | 30 | 60 |
| **TPM** | 200K | 30K | 6K |
| **TPD** | 2.5M (Tier 1, 데이터 공유) | 1M | 500K |
| **Context** | 128K | 65,536 | — |
| **과금 위험** | **있음** (카드 연결, budget cap $0.60/일) | 없음 (카드 미연결) | 없음 (카드 미연결) |
| **비고** | 데이터 공유 opt-in 필수 | 프리뷰 — 안정성 변동 가능 | — |

## 비용 구조

### Cerebras / Groq
- 완전 무료. 카드 연결 없음.
- 한도 초과 시 429 → 다음 provider로 fallback.

### OpenAI (gpt-4.1-mini, Tier 1)
- 데이터 공유 시 하루 2.5M 토큰 무료 (00:00 UTC 리셋).
- 초과분은 유료 과금 (input $0.15/1M, output $0.60/1M).
- **hard limit이 존재하지 않음** — OpenAI가 알림만 보내고 차단 안 함.
- litellm `max_budget: 0.60` + `budget_duration: "1d"`로 코드단에서 차단.

## Fallback 동작

```
요청 → OpenAI gpt-4.1-mini (weight: 100, max_budget $0.60/일)
         ├─ 성공 → 응답 반환
         └─ 실패(429/budget초과) → cooldown 5분
              → Cerebras (weight: 2)
                  ├─ 성공 → 응답 반환
                  └─ 실패 → cooldown 5분
                       → Groq (weight: 1)
                            ├─ 성공 → 응답 반환
                            └─ 실패 → 502 에러
```

- `allowed_fails=1`: 1회 실패 후 해당 provider 5분 쿨다운
- `num_retries=2`: 최대 2회 다른 provider로 fallback 시도
- `routing_strategy="simple-shuffle"`: weight 비율로 우선순위 제어
- fallback 시 429는 즉시 반환되므로 체감 지연은 100~200ms 수준

## 용량 추정 (공지 요약 기준)

공지 요약 1건 ≈ 800 토큰 (input ~650 + output ~150)

| Provider | 일일 토큰 한도 | 공지 요약 가능 건수 |
|----------|-------------|-----------------|
| Cerebras | 1M | ~1,250건 |
| Groq | 500K | ~625건 |
| OpenAI | 2.5M (Tier 1) | ~3,000건 |
| **합계** | **4M** | **~4,875건** |

## Tier 업그레이드 시

OpenAI Tier 3-5로 올라가면:
- 무료 토큰: 10M/일 → `max_budget`을 `2.50`으로 변경
- 1순위로 올려도 됨 (과금 여유 확보 후)

## 설정 파일

`app/llm.py`에서 `weight`, `max_budget` 값 수정.
