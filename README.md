# pasugun — 시그널 로봇

규칙 기반 "로봇"이 국내 주식을 감시하다가 매수·매도 타점이 오면 텔레그램으로 알리고,
웹 대시보드에서 근거와 성과를 보여주는 개인용 도구입니다. 프로젝트 규칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 구조

| 경로 | 내용 |
|---|---|
| `apps/web` | Next.js(App Router) + TypeScript + Tailwind 대시보드 (pnpm) |
| `services/engine` | Python 3.12 FastAPI, 수집 배치, 신호 엔진, 백테스터 (uv) |
| `services/engine/alembic` | DB 마이그레이션 (스키마 + 로봇 시드) |
| `docs/toss` | 토스증권 Open API 스펙 |

## 준비

- Docker (Compose v2)
- 로컬 개발 시: [uv](https://docs.astral.sh/uv/), Node.js 22+, pnpm 10+

```bash
cp .env.example .env   # 값 채우기. 기본은 TOSS_MODE=mock
```

기본 포트는 DB `5433`, engine API `8000`, 웹 `3001`입니다. 겹치면 `.env`의 `POSTGRES_PORT`, `ENGINE_API_PORT`, `WEB_PORT`를 바꾸세요.

## 전체 실행 (Docker)

```bash
docker compose up -d --build
curl localhost:8000/health   # {"status":"ok"}
open http://localhost:3001
```

`migrate` 서비스가 먼저 `alembic upgrade head`(시드 포함)를 실행하고 끝난 뒤 engine-api가 뜹니다.

## 로컬 개발

DB만 도커로 띄우고 engine과 web은 호스트에서 실행합니다.

```bash
docker compose up -d db

# engine
cd services/engine
uv sync
uv run alembic upgrade head
uv run uvicorn engine.api.main:app --reload --port 8000
uv run pytest
uv run ruff check . && uv run ruff format --check .

# web (다른 터미널)
cd apps/web
pnpm install
ENGINE_API_URL=http://localhost:8000 pnpm dev --port 3001
pnpm test
pnpm lint && pnpm typecheck
```

마이그레이션 추가: 모델(`services/engine/src/engine/db/models.py`)을 고친 뒤
`uv run alembic revision --autogenerate -m "설명"` → 생성된 파일 검토 → `uv run alembic upgrade head`.
