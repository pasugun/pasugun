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

## 토스 API 확인 (조회 전용)

```bash
cd services/engine
uv run python -m engine.toss.cli prices 005930          # 현재가
uv run python -m engine.toss.cli candles 005930 --count 5
uv run python -m engine.toss.cli calendar               # 장 운영 시간
uv run python -m engine.toss.cli --help                 # 전체 명령
```

- `TOSS_MODE=mock`(기본): `services/engine/tests/fixtures/toss/` 의 JSON 으로 응답합니다. 네트워크·DB 불필요.
- `TOSS_MODE=live`: `.env` 에 `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET`(계좌 조회는 `TOSS_ACCOUNT_SEQ`도)를 넣고,
  토스증권 WTS 설정 > Open API > 허용 IP 에 현재 공인 IP 를 등록해야 합니다.
  토큰은 DB `api_tokens` 에 저장해 모든 프로세스가 공유하므로 DB 가 떠 있어야 합니다(`docker compose up -d db`).
  `accountSeq` 는 `uv run python -m engine.toss.cli accounts` 로 확인합니다.

## 데이터 수집 배치

`engine-worker` 컨테이너가 평일 15:50 KST(`COLLECT_CRON`)에 장 마감 배치를 돌립니다.
종목 마스터 → 일봉(전 보통주 + 벤치마크 069500) → 대상 종목 선정(20일 평균 거래대금 ≥ 10억) → 수급 순서이며,
휴장일(market-calendar/KR)에는 건너뜁니다. 실행 기록은 `collect_runs` 테이블에 남습니다.

```bash
cd services/engine
uv run collect stocks                          # 종목 마스터
uv run collect daily --since 2023-01-01        # 일봉·수급, 빠진 날짜 보충
uv run collect backfill --years 3              # 백테스트용 과거 일봉 (진행률 표시)
uv run collect backfill --years 1 --symbols 005930,000660 --with-investor
uv run collect run-daily [--date 2026-09-30]   # 스케줄러 작업을 한 번 실행
```

- 모든 작업은 upsert 라서 여러 번 돌려도 결과가 같습니다. 최근 5거래일은 매번 다시 받아 수정주가·잠정치를 반영하고,
  다시 받은 종가가 달라지면(액면분할 등) 그 종목 전체 기간을 다시 받습니다.
- 배치가 하루 실패해도 다음 실행이 종목별 마지막 저장일부터 이어 받아 자동으로 채웁니다.
  워커가 꺼져 있다 켜졌을 때 오늘 실행 시각이 지났고 기록이 없으면 바로 한 번 실행합니다.
- 상장폐지 종목(생존자 편향 방지): `uv sync --extra krx` 후 `.env` 에 `KRX_ID`/`KRX_PW` 를 넣고
  `uv run collect backfill --years 3 --include-delisted`. `source='pykrx'`, `adjusted=false` 로 저장되고 토스 데이터는 덮어쓰지 않습니다.
- 테스트는 개발 DB 옆에 `signal_test` DB 를 만들어 씁니다(DB 가 없으면 DB 테스트는 건너뜀).

마이그레이션 추가: 모델(`services/engine/src/engine/db/models.py`)을 고친 뒤
`uv run alembic revision --autogenerate -m "설명"` → 생성된 파일 검토 → `uv run alembic upgrade head`.
