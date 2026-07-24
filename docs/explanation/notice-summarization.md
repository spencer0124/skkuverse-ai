---
title: Notice Summarization — 분류·구조화 추출 설계
type: explanation
status: accepted
owner: zoyoong124@gmail.com
last-updated: 2026-07-24
audience: public
---

# Notice Summarization — 분류·구조화 추출 설계

> `POST /api/notices/summarize`가 공지 원문을 **LLM 1콜로 구조화 추출**(분류 + 요약 + 일시/장소/메타)하는 방식과, pydantic 스키마를 "계약이자 자가수리 지시문"으로 써서 **엔드포인트가 절대 500을 내지 않게** 만든 신뢰성 설계를 설명한다. 시스템 전체 흐름은 [공지 파이프라인](https://github.com/spencer0124/skkuverse/blob/main/docs/flows/notice-pipeline.md), LLM 라우팅·budget 운영은 [llm-providers.md](../llm-providers.md).

이 문서의 모든 도메인 로직은 한 파일에 있다: **`app/routes/notices.py`**. 값(라인 번호·정확한 프롬프트 문구)은 코드가 SSOT이며, 여기서는 함수/클래스 이름으로 가리킨다.

## 문제 — 왜 구조화 추출인가

크롤러가 긁어온 공지 원문은 사람이 읽을 마크다운일 뿐, 앱이 "마감 D-3", "오늘 15:00 개최" 같은 UI를 그리려면 **기계가 읽을 구조**가 필요하다. 그래서 AI 서버는 원문을 받아 다음을 한 번에 뽑는다:

- **분류** (`type`) — 이 공지가 요구하는 행동의 성격
- **요약** (`summary`, `oneLiner`) — 2~4문장 본문 + 50자 부제목
- **구조화 칩** (`periods`, `locations`, `details`) — 일시/장소/대상·주최 등

### 단일 콜 트레이드오프

type·요약·칩을 **LLM 1콜**(structured output)에 묶는다. 분리하면 비용·레이턴시가 N배가 되고, 공지는 필드 간 상관이 높아(예: `type=event`면 `periods`에 행사일) 한 콜이 일관성에 유리하다. 대가로 출력 스키마가 커져 검증·후처리 부담이 늘고 — 그래서 [신뢰성 장치](#신뢰성--절대-500을-내지-않는다)가 두껍다.

## 도메인 설계 — 출력 스키마와 3분류

출력 계약은 pydantic 모델 `NoticeSummary` (`app/routes/notices.py`). 필드 구성:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `oneLiner` | `str` (필수) | 50자 부제목 |
| `summary` | `str` (필수) | 2~4문장 요약 본문 |
| `type` | `str` (필수) | 아래 3분류 |
| `periods` | `NoticePeriod[]` | `{label, startDate, startTime, endDate, endTime}` 전부 nullable |
| `locations` | `NoticeLocation[]` | `label` nullable, **`detail`은 `min_length=1`** (빈 원소 생성 불가) |
| `details` | `NoticeDetails` | `{target, action, host, impact}` 전부 nullable |

`SummarizeRequest`는 `{title, category="", cleanText, date?}`, 응답 `SummarizeResponse`는 `NoticeSummary` + `model`(어느 프로바이더가 서빙했나 — 디버깅용).

### 3분류 — "행동 축" 하나

`SYSTEM_PROMPT`(모듈 상수)의 "타입 판별" 섹션이 정의한다:

- **`action_required`** — 학생이 신청·제출·등록 등 **해야 할 행동**이 있음. *행사라도 사전등록이 필요하면 이쪽.*
- **`event`** — 특정 날짜에 열리는 행사 중 **별도 신청이 불필요**한 순수 참석형.
- **`informational`** — 시설/시스템/정책 안내. 행동 불필요.

**왜 3개인가.** taxonomy를 잘게 쪼개면 LLM 일관성이 떨어지고 클라이언트 분기가 폭발한다. 학과·장학 같은 *주제* 분류는 크롤러의 `category` 메타가 담당하고, LLM에는 **사람도 애매한 '행동 의도' 축** 하나만 맡겨 3분할했다. 이 분류의 realized 효과는 downstream에서 [마감 의미 해석](#type의-실제-용도--downstream-마감-의미-해석) 하나로 좁다.

프롬프트 상수(`SYSTEM_PROMPT`)는 **eval 하니스가 그대로 import**해 prod와 어긋나지 않는다 (`tests/test_provider_comparison.py`).

## LLM 처리 — 중앙화된 라우팅

호출은 전부 `model="llm"` 별칭 하나를 거친다 (`app/llm.py`의 `litellm.Router`). 프로바이더·재시도·budget·fallback이 라우터에 중앙화돼, 호출 코드(`summarize_notice`)는 **어느 프로바이더가 서빙하는지 모른다**. 폴백 순서(OpenAI → Cerebras → Groq)·weight·budget 운영·매일 reset의 알려진 no-op은 [llm-providers.md](../llm-providers.md)가 SSOT다 — 여기서 중복하지 않는다.

호출 파라미터의 핵심만: `temperature=0` (재현성 — 크롤러가 content-hash로 재요약을 판단하므로 결정성이 운영 로직과 직결), `response_format=NoticeSummary` (structured output 1차 방어).

> [!NOTE]
> `response_format`은 프로바이더 의존적이다. OpenAI는 structured output을 강제하지만 Qwen 폴백은 프롬프트 + JSON 파싱 + 재시도에 의존한다 — 아래 `_parse_llm_json`의 `<think>` 스트리핑이 존재하는 이유.

## 신뢰성 — "절대 500을 내지 않는다"

structured output도 100%가 아니다(특히 폴백 모델). 그래서 pydantic을 **계약이자 자가수리 지시문**으로 쓰는 4중 방어를 얹었다. 하드 실패로 남는 유일한 상태 코드는 **502**(LLM 전송 예외)뿐 — 모델 출력이 깨져도 502가 아니라 degrade한다.

### self-repair 루프 (최대 2콜)

`summarize_notice`의 루프는 `MAX_FORMAT_RETRIES = 1` → **1콜(정상) + 형식 오류 시 1회 재시도**. pydantic `ValidationError` 발생 시에만 재시도하며, **에러 메시지를 그대로 모델에 되먹여** 수정본을 요청한다:

```text
형식 오류: {pydantic 에러 메시지}
날짜는 YYYY-MM-DD, 시간 HH:mm ... 수정된 JSON만 다시 출력해.
```

타입 시스템의 검증 실패가 곧 재프롬프트 지시문이 된다. 단, 이건 **형식(format) 오류**에만 발동한다 — 연도 같은 *의미* 오류는 아래 결정적 후처리가 맡는다.

### graceful degradation 사다리

1. 파싱 + 검증 성공 → 정상 반환 (200)
2. 검증 실패 → 에러 피드백 재프롬프트 1회
3. 재시도도 실패 → `_safe_summary`로 **문제의 `periods`만 버리고** locations/details는 보존
4. JSON 파싱 자체 실패 + 직전 유효본 존재 → 직전 유효본 복구
5. 전부 실패 → `type="unknown"` 빈 스켈레톤 (**200, 구문상 유효**)

### JSON robustness

`_parse_llm_json`은 `<think>...</think>` 제거(Qwen reasoning) → ` ```json ` fence 해제 → bare `{…}` 순으로 시도하고 실패 시 `None`. 폴백 모델이 1순위만큼 형식을 안 지켜서 필요하다.

### 결정적 후처리 — LLM을 코드로 교정

성공 경로마다 세 함수가 순서대로 LLM 출력을 교정한다:

- **`_guard_year`** — 게시일 ±1년을 벗어난 `periods` 연도를 교정(연도 환각). ±1년은 다음 학기 허용.
- **`_strip_fillers`** — `details`의 "없음/N/A"를 null로, 비특정 장소(집/온라인/remote)를 제거.
- **`_strip_oneliner_prefix`** — "공지:/Notice:" 상투어 제거.

**"어디까지 프롬프트, 어디부터 코드"**의 경계는 비용·확실성으로 가른다 — 연도 교정은 날짜 연산이 재프롬프트보다 싸고 확실하니 코드가 맡는다.

### 502 로깅 규약

`acompletion` 예외로 `HTTPException(502)`를 raise하기 **직전에** `type/status/msg`를 `log.warning`으로 남긴다. uvicorn이 상태 코드만 찍기 때문에, 이 한 줄이 없으면 근본 원인이 응답 바디에만 남아 장애 진단이 늦어진다(실제 장애 사례의 교훈).

## 언어 처리

`_detect_language`가 Hangul 비율 ≥ 10%면 `ko`, 아니면 `en`, 모호하면 **항상 ko 기본**. 결과를 `[LANG: ko|en]` 태그로 user 프롬프트 첫 줄에 주입해 모델이 추측하지 않게 한다. `_enforce_language`는 출력 언어 어긋남을 **경고 로그만** 남긴다(가용성 우선 — 재생성하지 않음).

## type의 실제 용도 — downstream 마감 의미 해석

> [!IMPORTANT]
> `type`의 realized 효과는 **"마감 의미 해석" 하나**로 좁다. 시각적 type 배지·색·아이콘은 (의도적으로) 없다.

같은 `periods[]` 배열을 `type`에 따라 다르게 해석한다:

- **서버**(NestJS): `action_required`면 "놓치면 안 되는 마감일"을 best-pick, 그 외엔 AI가 준 순서를 신뢰 (`selectEffectivePeriod`). 그게 서버의 유일한 type 분기 — 푸시 우선순위·토픽·정렬은 전부 `sourceId`/`oneLiner` 기반. 상세: [server notices-api](https://github.com/spencer0124/skkuverse-server/tree/main/docs).
- **앱**(RN): `type`은 마감 pill 날짜 계산(`formatDeadlineBadge`)에만 쓰이고, 상세 칩은 presence-only 렌더. 색은 type이 아니라 날짜 math가 만든 variant가 결정. 상세: [app notices-feature](https://github.com/spencer0124/skkuverse-app/tree/main/docs).

**프레이밍**: 분류를 *시간 의미론*에 집중시켰다 — 사용자에게 중요한 건 "이게 무슨 분류인가"가 아니라 "언제까지 뭘 해야 하나"라서. type 필터·라벨 UI는 스키마·파라미터가 준비됐고 아직 미연결이라, 제품 결정에 따라 바로 켤 수 있는 상태다.

## 테스트

- **단위 테스트(offline, `app.llm`을 import 시점에 mock)**: `test_detect_language.py`(언어감지 경계), `test_guard_year.py`(연도 환각 QA를 회귀자산화), `test_budget_reset.py`(litellm 내부 cache-key 포맷을 업그레이드 tripwire로 고정).
- **eval 하니스(단위 테스트 아님)**: `test_provider_comparison.py` — `test_` 접두사지만 테스트 함수가 없는 `asyncio.run(main())` 스크립트. prod 동일 프롬프트·QA 케이스를 폴백 모델에 **실제로** 돌려 OK/연도오류/파싱실패를 비교한다(실 API 키 필요).
- **CI**(`.github/workflows/ci.yml`)는 `ruff check app/` + `docker build`만. pytest는 로컬 전용.

## 정직한 한계

- **필수 3키의 함정**: `summary`/`oneLiner`/`type` 중 하나라도 빠지면 크롤러 저장 단계에서 실패로 분류된다(부분 저장 아님). 컨트랙트 드리프트가 "평범한 AI 실패"로 위장될 수 있는 약점.
- **budget cap은 prod에서 실질 미강제**: 매일 reset은 litellm 1.82.6의 알려진 이슈로 no-op이고, 실효 가드는 OpenAI 무료 quota + host cron 재시작이다 — [llm-providers.md](../llm-providers.md) 참조.

## 관련 문서

- [공지 파이프라인 (시스템 전체 흐름)](https://github.com/spencer0124/skkuverse/blob/main/docs/flows/notice-pipeline.md)
- [llm-providers.md](../llm-providers.md) — LLM 라우팅·fallback·budget 운영 (SSOT)
- [cicd-and-branch-protection.md](../cicd-and-branch-protection.md) — CI/CD, 브랜치 보호
- [데이터 소유권 ADR](https://github.com/spencer0124/skkuverse/blob/main/docs/decisions/0001-notice-data-ownership.md) — AI는 요약을 반환만, 저장은 크롤러
