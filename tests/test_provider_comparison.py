"""Cerebras / Groq 모델 비교 — 10건 QA 케이스를 각 provider에 직접 호출."""

import asyncio
import json
import os
import sys
import time
from unittest.mock import MagicMock

# app.llm imports litellm.Router at module level which requires an event loop.
sys.modules["app.llm"] = MagicMock()

import litellm

# ── 시스템 프롬프트 (notices.py에서 가져옴) ──
from app.routes.notices import SYSTEM_PROMPT, NoticeSummary, _guard_year, _strip_fillers

PROVIDERS = {
    "cerebras": {
        "model": "cerebras/qwen-3-235b-a22b-instruct-2507",
        "api_key_env": "CEREBRAS_API_KEY",
        "delay": 3.0,  # RPM 30 → 2초면 충분하지만 여유
    },
    "groq": {
        "model": "groq/qwen/qwen3-32b",
        "api_key_env": "GROQ_API_KEY",
        "delay": 20.0,  # TPM 6000, ~2300 tok/req → need ~23s between calls
    },
}

CASES = [
    {
        "name": "Case 1: 기아 채용",
        "input": {
            "title": "[취업][기아] 2026상반기 기아 신입/전환형 인턴 채용 (신입 ~4/13(월) 11시, 인턴 ~4/20(월) 11시까지)",
            "category": "",
            "cleanText": "[기아] 2026상반기 기아 신입/전환형 인턴 채용■ 신입• 모집기간: 4월 1일(수) 11시 ~ 13일(월) 11시• 지원자격: 학/석사 학위를 보유하신 분 혹은 2026년 8월 졸업 예정이신 분",
            "date": "2026-04-09",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-04-01",
        "expected_endDate": "2026-04-20",
    },
    {
        "name": "Case 2: 삼성기부장학금",
        "input": {
            "title": "[학부]한국장학재단 푸른등대 삼성기부장학금 신청 안내(~4/7(화) 18시까지)",
            "category": "",
            "cleanText": "AI 핵심분야로 진로를 희망하는 학생을 대상으로 학과장 추천서를 받고자 하는 학생은 자기소개서 및 추천서 초안을 작성하여 학부 사무실로 메일 제출. 4월 6일(월)에 학과장 추천서메일 전달.",
            "date": "2026-04-01",
        },
        "expected_year": 2026,
        "expected_startDate": None,
        "expected_endDate": "2026-04-07",
    },
    {
        "name": "Case 3: LG이노텍",
        "input": {
            "title": "[LG이노텍] 2026년 패키지솔루션 신입사원 채용(~4/13(월) 12시까지)",
            "category": "",
            "cleanText": "지원서 접수 방법- LG그룹 채용 홈페이지를 통해서만 지원 가능. 전형절차 서류접수 3/30(월) ~ 4/13(월) 12시",
            "date": "2026-04-01",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-03-30",
        "expected_endDate": "2026-04-13",
    },
    {
        "name": "Case 4: 삼성물산",
        "input": {
            "title": "삼성물산 건설부문 신입사원 채용",
            "category": "취업",
            "cleanText": "모집 일정 : 03.10(화) ~ 03.17(화) 17:00. 모집 직군 : 건축, 기계, 전기, 안전, IT, 경영지원. 삼성커리어스 온라인 지원.",
            "date": "2026-03-12",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-03-10",
        "expected_endDate": "2026-03-17",
    },
    {
        "name": "Case 5: 키움 디지털 아카데미",
        "input": {
            "title": "키움증권 키움 디지털 아카데미 4기생 모집 (~4/17)",
            "category": "취업",
            "cleanText": "이전글[아워홈] 2026 아워홈 식재영업 채용 전환형 인턴 채용다음글[GS칼텍스] 2026년 상반기 GS칼텍스 신입사원 채용 (~3/31)목록",
            "date": "2026-04-01",
        },
        "expected_year": 2026,
        "expected_startDate": None,
        "expected_endDate": "2026-04-17",
    },
    {
        "name": "Case 6: 한국어 실험",
        "input": {
            "title": "선착순 모집 [집에서 간단한 실험하고 2000원 기프티콘 받자(10-15분 소요)! 한국어 실험 참여자 모집]",
            "category": "채용/모집",
            "cleanText": "참여 보상: 2,000원 상당의 기프티콘 (4월 19일 일괄 송부). 한국어가 모국어이신 분.",
            "date": "2026-04-10",
        },
        "expected_year": 2026,
        "expected_startDate": None,
        "expected_endDate": None,  # 마감일 없음, 선착순
    },
    {
        "name": "Case 7: AI FGI",
        "input": {
            "title": "[교수학습혁신센터] AI 활용 학습 방식 소그룹 인터뷰 FGI 참여자 모집",
            "category": "채용/모집",
            "cleanText": "참가 일시: 4월 13일(월), 15일(수), 17일(금) 오전 10:30 / 4월 14일(화), 16일(목) 13:00. 참여 사례: 19,500원.",
            "date": "2026-04-08",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-04-13",
        "expected_endDate": "2026-04-17",
    },
    {
        "name": "Case 8: 기숙사 통금 해제",
        "input": {
            "title": "Suspending Curfew during mid-term exam period",
            "category": "Notice in English",
            "cleanText": "Period of no admission control: Monday, April 13th to Sunday, April 26th. Returning date: from 1am on April 27th.",
            "date": "2026-04-10",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-04-13",
        "expected_endDate": "2026-04-26",
    },
    {
        "name": "Case 9: 외국인 한국어 강좌",
        "input": {
            "title": "[대학원] 외국인 대학원생 한국어 특별강좌 안내",
            "category": "",
            "cleanText": "opens Korean Language Program for SKKU international graduate school students in suwon from Mar. 16. ~ June 4. Tuition fee is free.",
            "date": "2026-03-10",
        },
        "expected_year": 2026,
        "expected_startDate": "2026-03-16",
        "expected_endDate": "2026-06-04",
    },
    {
        "name": "Case 10: 창업지원단",
        "input": {
            "title": "[창업지원단] 성균관대학교 창업지원단 창업중심대학사업(성장 파트) 직원 채용 공고(~2026년 4월 16일(목) 접수)",
            "category": "채용/모집",
            "cleanText": "지원서 접수 : ~ 2026년 4월 16일(목) 24시. 사업기간: 5년(2023년 ~ 2027년).",
            "date": "2026-04-09",
        },
        "expected_year": 2026,
        "expected_startDate": None,
        "expected_endDate": "2026-04-16",
    },
]


def _build_user_prompt(case_input: dict) -> str:
    date_line = f"게시일: {case_input['date']}\n" if case_input.get("date") else ""
    return f"{date_line}제목: {case_input['title']}\n카테고리: {case_input['category']}\n본문:\n{case_input['cleanText']}"


def _parse_response(raw: str) -> dict | None:
    """LLM raw output → JSON dict. <think> 태그, ```json``` wrapper 처리."""
    import re
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


def _check_year(date_str: str | None, expected_year: int) -> str:
    if date_str is None:
        return "null"
    year = int(date_str[:4])
    return "OK" if year == expected_year else f"WRONG({year})"


async def run_provider(provider_name: str):
    cfg = PROVIDERS[provider_name]
    api_key = os.environ.get(cfg["api_key_env"], "")
    if not api_key:
        print(f"\n⚠ {provider_name}: {cfg['api_key_env']} not set, skipping\n")
        return

    print(f"\n{'='*60}")
    print(f" {provider_name.upper()} — {cfg['model']}")
    print(f"{'='*60}\n")

    results = []

    for i, case in enumerate(CASES):
        print(f"  [{i+1}/10] {case['name']}...", end=" ", flush=True)
        user_prompt = _build_user_prompt(case["input"])
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = await litellm.acompletion(
                model=cfg["model"],
                messages=messages,
                temperature=0,
                max_tokens=1024,
                api_key=api_key,
            )
            raw = response.choices[0].message.content.strip()
            parsed = _parse_response(raw)

            if parsed is None:
                print("PARSE_FAIL")
                results.append({
                    "case": case["name"],
                    "status": "PARSE_FAIL",
                    "raw": raw[:200],
                })
            else:
                periods_raw = parsed.get("periods") or []
                first_period = periods_raw[0] if periods_raw else {}
                sd = first_period.get("startDate") if isinstance(first_period, dict) else None
                ed = first_period.get("endDate") if isinstance(first_period, dict) else None
                sd_check = _check_year(sd, case["expected_year"])
                ed_check = _check_year(ed, case["expected_year"])

                # Apply _guard_year
                try:
                    summary = NoticeSummary.model_validate(parsed)
                    summary = _guard_year(summary, case["input"].get("date"))
                    summary = _strip_fillers(summary)
                    sd_after = summary.periods[0].startDate if summary.periods else None
                    ed_after = summary.periods[0].endDate if summary.periods else None
                    sd_after_check = _check_year(sd_after, case["expected_year"])
                    ed_after_check = _check_year(ed_after, case["expected_year"])
                except Exception:
                    sd_after = sd
                    ed_after = ed
                    sd_after_check = sd_check
                    ed_after_check = ed_check

                status = "OK" if (sd_check in ("OK", "null") and ed_check in ("OK", "null")) else "YEAR_ERR"
                print(f"{status} | start={sd}({sd_check}) end={ed}({ed_check})" +
                      (f" → guard: start={sd_after}({sd_after_check}) end={ed_after}({ed_after_check})" if status == "YEAR_ERR" else ""))

                results.append({
                    "case": case["name"],
                    "status": status,
                    "type": parsed.get("type"),
                    "oneLiner": parsed.get("oneLiner"),
                    "startDate": sd,
                    "endDate": ed,
                    "startDate_check": sd_check,
                    "endDate_check": ed_check,
                    "startDate_after_guard": sd_after,
                    "endDate_after_guard": ed_after,
                    "periods": periods_raw,
                    "locations": parsed.get("locations"),
                    "details": parsed.get("details"),
                })

        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "case": case["name"],
                "status": "ERROR",
                "error": str(e),
            })

        # Rate limit delay
        if i < len(CASES) - 1:
            time.sleep(cfg["delay"])

    # Summary
    print(f"\n{'─'*60}")
    print(f" {provider_name.upper()} 결과 요약")
    print(f"{'─'*60}")
    ok = sum(1 for r in results if r["status"] == "OK")
    year_err = sum(1 for r in results if r["status"] == "YEAR_ERR")
    parse_fail = sum(1 for r in results if r["status"] == "PARSE_FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    print(f"  OK: {ok}/10 | YEAR_ERR: {year_err} | PARSE_FAIL: {parse_fail} | ERROR: {errors}")

    if year_err > 0:
        print(f"\n  연도 오류 케이스 (_guard_year 교정 후):")
        for r in results:
            if r["status"] == "YEAR_ERR":
                print(f"    {r['case']}: {r['startDate']}→{r.get('startDate_after_guard')} / {r['endDate']}→{r.get('endDate_after_guard')}")

    # Full output
    print(f"\n{'─'*60}")
    print(f" {provider_name.upper()} 전체 응답")
    print(f"{'─'*60}")
    for r in results:
        print(f"\n  {r['case']} [{r['status']}]")
        if r["status"] in ("OK", "YEAR_ERR"):
            print(f"    type: {r.get('type')}")
            print(f"    oneLiner: {r.get('oneLiner')}")
            print(f"    startDate: {r.get('startDate')} ({r.get('startDate_check')})")
            print(f"    endDate: {r.get('endDate')} ({r.get('endDate_check')})")
            if r["status"] == "YEAR_ERR":
                print(f"    → guard후 startDate: {r.get('startDate_after_guard')}")
                print(f"    → guard후 endDate: {r.get('endDate_after_guard')}")
            d = r.get("details", {})
            if d:
                print(f"    details: target={d.get('target')} | action={d.get('action')} | host={d.get('host')} | impact={d.get('impact')}")
            print(f"    periods: {r.get('periods')}")
            print(f"    locations: {r.get('locations')}")
        elif r["status"] == "PARSE_FAIL":
            print(f"    raw: {r.get('raw')}")
        else:
            print(f"    error: {r.get('error')}")

    return results


async def main():
    from dotenv import load_dotenv
    load_dotenv()

    all_results = {}
    for provider in ["groq"]:  # Cerebras already done, Groq only
        all_results[provider] = await run_provider(provider)

    # Cross-provider comparison
    print(f"\n{'='*60}")
    print(f" CROSS-PROVIDER COMPARISON")
    print(f"{'='*60}")
    print(f"  {'Case':<30} {'OpenAI':>8} {'Cerebras':>10} {'Groq':>8}")
    print(f"  {'─'*58}")
    for i, case in enumerate(CASES):
        openai_status = "OK"  # 이전 테스트에서 10/10 통과 확인됨
        cerebras_status = all_results.get("cerebras", [{}])[i].get("status", "N/A") if all_results.get("cerebras") else "SKIP"
        groq_status = all_results.get("groq", [{}])[i].get("status", "N/A") if all_results.get("groq") else "SKIP"
        print(f"  {case['name']:<30} {openai_status:>8} {cerebras_status:>10} {groq_status:>8}")


if __name__ == "__main__":
    asyncio.run(main())
