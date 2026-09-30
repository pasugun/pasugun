# 프로젝트: 시그널 로봇 (개인용)

## 목적
투자 스타일이 다른 여러 "로봇"(규칙 기반 전략)이 국내 주식을 감시하다가 매수·매도 타점이 오면
텔레그램으로 실시간 알림을 보내고, 웹 대시보드에서 근거와 성과를 보여주는 개인용 도구.
사용자는 나 한 명이다. 멀티 유저, 회원가입, 결제는 만들지 않는다.

## 핵심 원칙 (반드시 지킬 것)
1. 신호는 규칙 엔진이 만든다. LLM은 이미 계산된 지표를 받아 "설명 문장"만 만든다.
   LLM이 가격·수치·매수 여부를 새로 판단하거나 만들어내면 안 된다.
2. 같은 입력이면 같은 신호가 나와야 한다(결정적). 난수, 현재 시각 의존 로직은 주입 가능하게 만든다.
3. 주문 API(POST /api/v1/orders, conditional-orders 등)는 호출하는 코드를 만들지 않는다. 조회 전용.
4. 비밀값(토스 client_id/secret, 토큰, 텔레그램 토큰, Anthropic 키)은 .env에만 두고
   로그·에러 메시지·프론트엔드 번들·git에 절대 노출하지 않는다. .env.example만 커밋한다.
5. 토스 API의 필드 이름, 파라미터, WebSocket 메시지 형식은 docs/toss/ 의 openapi.json, asyncapi.json,
   overview.md 를 읽고 따른다. 문서에 없으면 추측하지 말고 TODO와 함께 나에게 질문한다.
6. 모든 외부 호출(토스, 텔레그램, LLM)은 인터페이스 뒤에 두고, 테스트에서는 mock으로 대체한다.
   테스트가 실제 토스 API를 호출하면 안 된다.
7. 금액·가격 계산은 float 대신 Decimal(파이썬) / 정수 원 단위를 쓴다.
8. 시간은 모두 Asia/Seoul 기준으로 저장·표시하고, DB에는 timezone 포함 타입(timestamptz)을 쓴다.

## 기술 스택
- 모노레포
  - apps/web: Next.js(App Router) + TypeScript + Tailwind, 차트는 lightweight-charts
  - services/engine: Python 3.12, FastAPI(웹용 API), 수집 배치, 실시간 워커, 신호 엔진, 백테스터
  - db: PostgreSQL 16 (docker-compose), 마이그레이션은 Alembic
- 패키지 관리: web은 pnpm, engine은 uv
- 테스트: engine은 pytest, web은 vitest
- 웹은 토스 API를 직접 호출하지 않는다. 항상 engine의 FastAPI를 거친다.
- 운영: 고정 IP VPS 한 대에서 docker compose로 전부 실행

## 토스증권 Open API 요약 (세부는 docs/toss/ 참고)
- REST: https://openapi.tossinvest.com / WebSocket: wss://openapi-ws.tossinvest.com/ws/v1
- 인증: OAuth 2.0 Client Credentials. POST /oauth2/token (grant_type=client_credentials, client_id, client_secret)
  - 모든 요청: Authorization: Bearer {token}
  - 계좌·자산·주문 API: X-Tossinvest-Account: {accountSeq} (GET /api/v1/accounts 로 조회)
  - 토큰은 클라이언트당 1개만 유효. 새로 발급하면 이전 토큰이 무효화된다.
    → 토큰 발급·갱신은 TokenManager 한 곳에서만 하고, 모든 프로세스가 DB에 저장된 토큰을 공유한다.
  - 허용 IP로 등록된 곳에서만 호출 가능(미등록은 403 edge-blocked).
- 초당 호출 한도: 차트(MARKET_DATA_CHART) 20, 시세(MARKET_DATA) 15, 수급(STOCK_TRADING_TREND) 10,
  종목(STOCK) 5, 전체종목(STOCK_ALL) 1, 랭킹 5, 자산(ASSET) 5, 계좌(ACCOUNT) 1, 주문내역 5, 주문정보 6.
  429 응답 시 Retry-After 헤더만큼 기다렸다 재시도. 응답의 X-RateLimit-* 헤더를 존중한다.
- 주요 조회 API
  - GET /api/v1/candles: interval 1m 또는 1d, count 최대 200, before(ISO, + 는 %2B)로 과거 페이지, adjusted(수정주가)
    응답은 최신순. 다음 페이지는 응답의 nextBefore 사용.
  - GET /api/v1/prices, /orderbook, /trades
  - GET /api/v1/stocks?symbols=(최대 200, 콤마), GET /api/v1/stocks/all (전체 종목)
  - GET /api/v1/stocks/{symbol}/warnings (매수 유의사항)
  - GET /api/v1/stocks/{symbol}/investor-trading: 국내만, 일별, 개인·외국인·기관 순매수 "주식 수", count 최대 100, until로 페이지
  - GET /api/v1/rankings: type(MARKET_TRADING_AMOUNT 등), marketCountry, duration(realtime~1y), 최대 100위
  - GET /api/v1/market-calendar/KR (장 운영일·시간)
  - GET /api/v1/holdings (매수 평균가·평가금액·손익), GET /api/v1/buying-power, GET /api/v1/orders?status=CLOSED
- WebSocket
  - 계정당 동시 연결 최대 2개, 연결당 구독 최대 100개, 구독 선언 최대 초당 5회
  - 180초 동안 클라이언트 수신이 없으면 끊김 → 60초마다 PING
  - 구독은 선언형: JSON 배열 하나가 "현재 구독 전체". 보낼 때마다 전체를 교체한다. [] = 전체 해제.
  - 채널: trade:kr(실시간 체결), orderbook:kr(호가), personal:order(내 주문 이벤트)
  - 시세 채널은 LOSSY: 느리면 중간 체결이 빠질 수 있다. → 가격 조건은 "정확히 그 가격"이 아니라 "도달 여부"로 판단.
- 토스가 주지 않는 데이터: 재무(PER·PBR·배당) → DART, 입출금 내역 → 사용자 수동 입력, 과거 자산 추이 → 매일 자체 저장

## 도메인 용어
- 로봇(Robot): 종목 선정 조건 + 매수 타점 + 매도 규칙(목표가·손절가·기간)의 묶음. 코드에서는 Strategy 클래스.
- 신호(Signal): 로봇이 특정 종목에 대해 낸 매수 알림. 상태 OPEN → TAKE_PROFIT / STOP_LOSS / EXPIRED / CANCELLED.
- 감시 목록(Watchlist): 장 마감 배치가 로봇별로 뽑은 "내일 실시간으로 볼 후보 종목". 전체 합계 200개 이하.
- 실행 중 로봇: 사용자가 켠 로봇. 동시에 최대 3개. 서버에서 강제한다(프론트 검증만으로 끝내지 않는다).

## 코드 스타일
- 작은 함수, 명확한 이름, 타입 힌트(파이썬)와 strict TypeScript.
- 새 기능에는 테스트를 같이 작성한다. 특히 신호 규칙과 백테스트 계산은 손으로 계산한 기대값으로 테스트한다.
- 작업이 끝나면 무엇을 만들었는지, 어떻게 실행·검증하는지, 남은 TODO가 무엇인지 요약한다.
