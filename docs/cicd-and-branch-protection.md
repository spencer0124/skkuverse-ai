# CI/CD 및 브랜치 보호 전략

## 워크플로우 구성

| 워크플로우 | 트리거 | 용도 |
|---|---|---|
| `ci.yml` | PR → main | Docker build 검증 |
| `deploy.yml` | main push | OCI 자동 배포 + auto rollback |
| `claude-code-review.yml` | PR 생성/업데이트 | 자동 코드 리뷰 |
| `claude.yml` | `@claude` 멘션 | 대화형 응답 |

---

## CI (`ci.yml`) — PR 단계

PR 생성 시 Docker build 성공 여부를 검증.

```
PR → main
  └→ build job
       ├→ checkout
       └→ docker build
```

## CD (`deploy.yml`) — 머지 후 배포

```
main push
  └→ deploy job (SSH → OCI)
       ├→ PREV_COMMIT 저장
       ├→ git pull origin main
       ├→ .env 존재 확인
       ├→ docker compose up -d --build
       ├→ health check retry (5초 × 6회)
       ├→ 성공 → 완료
       └→ 실패 → git checkout $PREV_COMMIT → 재빌드 (자동 rollback)
```

### 배포 대상

| 항목 | 값 |
|---|---|
| 서버 | OCI ARM VM (168.107.62.58) |
| 유저 | ubuntu |
| 경로 | `/home/ubuntu/skkuverse-ai` |
| 포트 | 4000 (127.0.0.1만 바인딩, 외부 접근 불가) |
| Docker 네트워크 | `skkuverse` (external) |

### 환경변수

`.env` 파일은 OCI 서버에만 존재 (git에 미포함). 워크플로우에서 `.env` 존재를 확인하여 누락 시 배포 실패 처리.

| 변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API |
| `CEREBRAS_API_KEY` | Cerebras API |
| `GROQ_API_KEY` | Groq API |

### GitHub Secrets

| Secret | 용도 |
|---|---|
| `ORACLE_VM_HOST` | OCI 서버 IP |
| `ORACLE_VM_USER` | SSH 유저 |
| `SSH_PRIVATE_KEY` | SSH 개인키 |
| `AI_DEPLOY_PATH` | 배포 경로 |

---

## 브랜치 보호 (GitHub Rulesets)

### 규칙

| 규칙 | 설명 |
|---|---|
| PR 필수 | main 직접 push 차단. PR → CI 통과 → 머지 |
| required_status_checks | `build` job 통과 필수 |
| non_fast_forward | force push 방지 |
| deletion | main 브랜치 삭제 방지 |

### Bypass

- **Repository Admin**: 긴급 시 직접 push 가능 (bypass_mode: always)

### PR 머지 흐름

```
feature branch 생성
  └→ 작업 & 커밋
       └→ PR 생성 (main ← feature)
            ├→ build check (Docker build 검증)
            ├→ Claude Code Review (자동 코드 리뷰)
            └→ 둘 다 통과 → 머지 가능 → deploy 자동 실행
```

### 자동 Rollback

배포 스크립트에 자동 rollback 내장. health check 실패 시:

1. 배포 전 커밋 해시 저장 (`PREV_COMMIT`)
2. health check 30초간 retry (5초 × 6회)
3. 실패 시 `git checkout $PREV_COMMIT` → 이전 이미지로 재빌드
4. CI는 실패로 표시 (GitHub에서 확인 가능)

### 긴급 hotfix

Admin은 bypass 권한이 있으므로 main에 직접 push 가능. 단, 의도적으로만 사용할 것.
