"""
웹 서버 (Web Server)

역할:
- API 엔드포인트 제공
- 대시보드 제공
- DB 읽기 전용 (분석/저장은 analyzer_server에서 담당)

포트: 8888
"""

import os
import sys
import asyncio
import logging
import math
import json
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional

# 프로젝트 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)  # tradeBot 폴더
_pgdb_dir = os.path.dirname(_tradebot_dir)  # PGdb 폴더
sys.path.insert(0, _tradebot_dir)
sys.path.insert(0, _pgdb_dir)

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Depends, Request, Request as FastAPIRequest, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi.responses import RedirectResponse

# 인증 모듈
from auth import AuthManager, get_current_user, require_auth
from auth.security import SecurityMiddleware, get_rate_limiter, get_ip_whitelist

import pandas as pd
from sqlalchemy import create_engine

from core.config_loader import get_config
from strategies import create_strategy_manager
from strategies.base import TradeSignal
from database.watched_symbols_repo import WatchedSymbolsRepo
from database.signals_repo import SignalsRepo
from database.positions_repo import PositionsRepo
from database.strategy_settings_repo import StrategySettingsRepo
from database.events_repo import EventsRepo
from database.user_actions_repo import UserActionsRepo
from database.positions_repo import PositionsRepo
from services.order_client import OrderClient
from services.signal_action_matcher import SignalActionMatcher
from services.timing_learner import TimingLearner
from services.policy_recommender import PolicyRecommender
from services.paper_trading_engine import PaperTradingEngine
from brokers.paper_broker import PaperBroker

# 로그 큐 (WebSocket으로 전송)
log_queue = queue.Queue(maxsize=1000)


class QueueHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        try:
            log_queue.put_nowait({
                'timestamp': datetime.now().isoformat(),
                'level': record.levelname,
                'message': log_entry
            })
        except queue.Full:
            try:
                log_queue.get_nowait()
                log_queue.put_nowait({
                    'timestamp': datetime.now().isoformat(),
                    'level': record.levelname,
                    'message': log_entry
                })
            except:
                pass


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("WebServer")

queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
logging.getLogger().addHandler(queue_handler)

# 전역 변수
config = None
strategy_manager = None
exchange = None
exchange_ccxt = None  # ccxt 인스턴스 캐싱
db_engine = None
watched_symbols_repo = None
signals_repo = None
positions_repo = None
strategy_settings_repo = None
events_repo = None
user_actions_repo = None
order_client = None
auth_manager = None
active_connections: List[WebSocket] = []
paper_trading_connections: List[WebSocket] = []  # Paper Trading 전용 WebSocket 연결


class AppState:
    """앱 상태"""
    def __init__(self):
        self.signals = []
        self.positions = []
        self.last_update = None
        self.watched_symbols = []
        self.timeframe = '15m'
        self.analysis_interval = 300
        # 학습 상태 추적: {symbol: {'timing': {...}, 'policy': {...}}}
        self.training_status = {}
        # Paper Trading 엔진
        self.paper_trading_engine: Optional[PaperTradingEngine] = None


app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기 관리"""
    import time
    global config, strategy_manager, exchange, exchange_ccxt, db_engine
    global watched_symbols_repo, signals_repo, positions_repo, strategy_settings_repo
    global events_repo, user_actions_repo, order_client, auth_manager

    total_start = time.time()
    logger.info("🚀 웹 서버 시작 중...")

    # Config 로드
    t0 = time.time()
    config = get_config()
    logger.info(f"✅ Config 로드 ({(time.time()-t0)*1000:.0f}ms)")

    # DB 연결 (읽기 전용)
    t0 = time.time()
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    db_engine = create_engine(db_url)
    logger.info(f"✅ DB 연결 ({(time.time()-t0)*1000:.0f}ms)")

    # Repository 초기화
    t0 = time.time()
    watched_symbols_repo = WatchedSymbolsRepo(db_engine)
    signals_repo = SignalsRepo(db_engine)
    positions_repo = PositionsRepo(db_engine)
    strategy_settings_repo = StrategySettingsRepo(db_engine)
    events_repo = EventsRepo(db_engine)
    user_actions_repo = UserActionsRepo(db_engine)
    logger.info(f"✅ Repository 초기화 ({(time.time()-t0)*1000:.0f}ms)")

    # 거래소 초기화 (잔고 조회용)
    t0 = time.time()
    try:
        from exchanges.binance_live import BinanceLive
        import ccxt

        api_key = os.getenv('BINANCE_LIVE_API_KEY')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET')

        if api_key and api_secret:
            t1 = time.time()
            exchange = BinanceLive(api_key=api_key, api_secret=api_secret)
            logger.info(f"  - BinanceLive 초기화 ({(time.time()-t1)*1000:.0f}ms)")

            # ccxt 인스턴스 캐싱 (load_markets 한 번만 호출)
            t1 = time.time()
            exchange_ccxt = ccxt.binance({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',
                    'adjustForTimeDifference': True  # 서버 시간 동기화
                }
            })
            logger.info(f"  - ccxt 인스턴스 생성 ({(time.time()-t1)*1000:.0f}ms)")

            t1 = time.time()
            exchange_ccxt.load_markets()
            logger.info(f"  - load_markets() ({(time.time()-t1)*1000:.0f}ms)")

            logger.info(f"✅ 바이낸스 거래소 연결 (총 {(time.time()-t0)*1000:.0f}ms)")
        else:
            logger.warning("⚠️ 바이낸스 API 키 없음")
    except Exception as e:
        logger.error(f"❌ 거래소 초기화 실패: {e}")

    # 감시 심볼 로드
    t0 = time.time()
    symbols = watched_symbols_repo.get_symbols_list(enabled_only=True)
    if symbols:
        app_state.watched_symbols = symbols
        logger.info(f"✅ 감시 심볼 로드: {len(symbols)}개 ({(time.time()-t0)*1000:.0f}ms)")
    else:
        app_state.watched_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
        logger.info(f"⚠️ 기본값 사용: {app_state.watched_symbols}")

    # 전략 매니저 초기화 (DB 기반, config.yaml strategies 사용 안 함)
    t0 = time.time()
    strategy_manager = create_strategy_manager(strategy_settings_repo=strategy_settings_repo, config=config.config if hasattr(config, 'config') else {})
    logger.info(f"✅ 전략 초기화: {strategy_manager.list_strategies()} ({(time.time()-t0)*1000:.0f}ms)")

    # 주문 클라이언트
    t0 = time.time()
    order_client = OrderClient(order_server_url="http://localhost:8889")
    order_server_healthy = await order_client.health_check()
    if order_server_healthy:
        logger.info(f"✅ 주문 서버 연결 ({(time.time()-t0)*1000:.0f}ms)")
    else:
        logger.warning(f"⚠️ 주문 서버 연결 실패 ({(time.time()-t0)*1000:.0f}ms)")

    # 인증 매니저 초기화
    t0 = time.time()
    auth_manager = AuthManager()
    # 기본 관리자 계정 생성 (없으면)
    if not auth_manager.users:
        default_password = os.getenv('ADMIN_PASSWORD', 'admin1234')
        auth_manager.create_user('admin', default_password, role='admin')
        logger.info(f"✅ 기본 관리자 계정 생성 (admin) ({(time.time()-t0)*1000:.0f}ms)")
    else:
        logger.info(f"✅ 인증 매니저 초기화 ({len(auth_manager.users)}명) ({(time.time()-t0)*1000:.0f}ms)")

    logger.info(f"🎉 웹 서버 준비 완료! (포트 8888) - 총 초기화 시간: {(time.time()-total_start)*1000:.0f}ms")

    yield

    # 종료
    if order_client:
        await order_client.close()
    if db_engine:
        db_engine.dispose()
    logger.info("👋 웹 서버 종료")


# FastAPI 앱
app = FastAPI(
    title="Auto Trading Dashboard",
    description="자동매매 대시보드 (읽기 전용)",
    version="2.0.0",
    lifespan=lifespan
)

# CORS 설정 (환경변수로 제한 가능)
allowed_origins = os.getenv('CORS_ORIGINS', '*').split(',')
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if allowed_origins != ['*'] else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# 보안 미들웨어 (Rate Limiting, IP 화이트리스트)
app.add_middleware(
    SecurityMiddleware,
    rate_limiter=get_rate_limiter(),
    ip_whitelist=get_ip_whitelist(),
    exclude_paths={'/api/health', '/favicon.ico', '/static'}
)

# Static files
static_dir = Path(_tradebot_dir) / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ============================================================
# 페이지 라우트
# ============================================================

@app.get("/favicon.ico")
async def favicon():
    """파비콘 (204 No Content 반환으로 404 에러 방지)"""
    from fastapi.responses import Response
    # 실제 favicon 파일이 없으면 빈 응답 반환
    favicon_file = static_dir / "favicon.ico"
    if favicon_file.exists():
        return FileResponse(favicon_file)
    return Response(status_code=204)


@app.get("/")
async def root(request: Request, user: dict = Depends(get_current_user)):
    """대시보드 페이지 (인증 필요)"""
    # 인증되지 않으면 로그인 페이지로 리다이렉트
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>Auto Trading Dashboard</h1><p>static/index.html을 생성하세요.</p>")


@app.get("/signals")
async def signals_page(request: Request, user: dict = Depends(get_current_user)):
    """신호 리스트 페이지 (인증 필요)"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    signals_file = static_dir / "signals.html"
    if signals_file.exists():
        return FileResponse(signals_file)
    return HTMLResponse("<h1>signals.html을 찾을 수 없습니다</h1>")


@app.get("/positions")
async def positions_page(request: Request, user: dict = Depends(get_current_user)):
    """포지션 페이지 (인증 필요)"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    positions_file = static_dir / "positions.html"
    if positions_file.exists():
        return FileResponse(positions_file)
    return HTMLResponse("<h1>positions.html을 찾을 수 없습니다</h1>")


@app.get("/matching")
async def matching_page(request: Request, user: dict = Depends(get_current_user)):
    """신호-액션 매칭 페이지 (인증 필요)"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    matching_file = static_dir / "matching.html"
    if matching_file.exists():
        return FileResponse(matching_file)
    return HTMLResponse("<h1>matching.html을 찾을 수 없습니다</h1>")


@app.get("/paper-trading")
async def paper_trading_page(request: Request, user: dict = Depends(get_current_user)):
    """Paper Trading 페이지 (인증 필요)"""
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    paper_trading_file = static_dir / "paper_trading.html"
    if paper_trading_file.exists():
        return FileResponse(paper_trading_file)
    return HTMLResponse("<h1>paper_trading.html을 찾을 수 없습니다</h1>")


# ============================================================
# 인증 API
# ============================================================

class LoginRequest(BaseModel):
    """로그인 요청"""
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    """비밀번호 변경 요청"""
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    """사용자 생성 요청"""
    username: str
    password: str
    role: str = "user"


@app.get("/login")
async def login_page():
    """로그인 페이지"""
    login_file = static_dir / "login.html"
    if login_file.exists():
        return FileResponse(login_file)
    return HTMLResponse("<h1>login.html을 찾을 수 없습니다</h1>")


@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """로그인"""
    if not auth_manager:
        raise HTTPException(status_code=500, detail="인증 시스템 초기화되지 않음")

    token = auth_manager.authenticate(request.username, request.password)

    if not token:
        raise HTTPException(
            status_code=401,
            detail="아이디 또는 비밀번호가 올바르지 않습니다"
        )

    # 응답에 토큰 포함
    response = {
        "success": True,
        "token": token,
        "username": request.username,
        "message": "로그인 성공"
    }

    return response


@app.post("/api/auth/logout")
async def logout(request: Request):
    """로그아웃"""
    token = request.cookies.get('auth_token')
    if not token:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:]

    if token and auth_manager:
        auth_manager.logout(token)

    return {"success": True, "message": "로그아웃 완료"}


@app.get("/api/auth/me")
async def get_current_user_info(user: dict = Depends(require_auth)):
    """현재 사용자 정보"""
    return {
        "username": user.get('username'),
        "role": user.get('role'),
        "authenticated": True
    }


@app.get("/api/auth/check")
async def check_auth(user: dict = Depends(get_current_user)):
    """인증 상태 확인"""
    if user:
        return {
            "authenticated": True,
            "username": user.get('username'),
            "role": user.get('role')
        }
    return {"authenticated": False}


@app.post("/api/auth/change-password")
async def change_password(request: ChangePasswordRequest, user: dict = Depends(require_auth)):
    """비밀번호 변경"""
    username = user.get('username')

    # 현재 비밀번호 확인
    if not auth_manager.authenticate(username, request.current_password):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다")

    # 새 비밀번호 설정
    if auth_manager.change_password(username, request.new_password):
        return {"success": True, "message": "비밀번호가 변경되었습니다"}

    raise HTTPException(status_code=500, detail="비밀번호 변경 실패")


@app.get("/api/auth/users")
async def list_users(user: dict = Depends(require_auth)):
    """사용자 목록 (관리자만)"""
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")

    return {"users": auth_manager.list_users()}


@app.post("/api/auth/users")
async def create_user(request: CreateUserRequest, user: dict = Depends(require_auth)):
    """사용자 생성 (관리자만)"""
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")

    if auth_manager.create_user(request.username, request.password, request.role):
        return {"success": True, "message": f"사용자 '{request.username}' 생성 완료"}

    raise HTTPException(status_code=400, detail="사용자 생성 실패 (이미 존재할 수 있음)")


@app.delete("/api/auth/users/{username}")
async def delete_user(username: str, user: dict = Depends(require_auth)):
    """사용자 삭제 (관리자만)"""
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")

    if username == user.get('username'):
        raise HTTPException(status_code=400, detail="자신의 계정은 삭제할 수 없습니다")

    if auth_manager.delete_user(username):
        return {"success": True, "message": f"사용자 '{username}' 삭제 완료"}

    raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")


# ============================================================
# REST API
# ============================================================

@app.get("/api/health")
async def health_check():
    """헬스 체크"""
    return {"status": "ok", "timestamp": datetime.now().isoformat(), "server": "web"}


@app.get("/api/strategies")
async def get_strategies():
    """전략 목록"""
    strategies = []
    for name in strategy_manager.list_strategies():
        strategy = strategy_manager.get_strategy(name)
        strategies.append({
            "name": name,
            "enabled": strategy.is_enabled(),
            "config": strategy.get_config()
        })
    return {"strategies": strategies, "count": len(strategies)}


class StrategyUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[Dict] = None


async def _trigger_analyzer_reload():
    """analyzer_server에 전략 리로드 요청(실패해도 web은 계속 동작)"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{ANALYZER_SERVER_URL}/api/strategies/reload")
    except Exception as e:
        logger.warning(f"⚠️ analyzer 리로드 요청 실패: {e}")


@app.post("/api/strategies/{strategy_name}/enable")
async def enable_strategy(strategy_name: str, user: dict = Depends(require_auth)):
    """전략 활성화 (DB 저장 + analyzer 반영)"""
    if not strategy_settings_repo:
        raise HTTPException(status_code=500, detail="strategy_settings_repo가 초기화되지 않았습니다")
    ok = strategy_settings_repo.patch(strategy_name, enabled=True, config_patch=None)
    if not ok:
        raise HTTPException(status_code=500, detail="전략 활성화(DB 저장) 실패")

    # web_server 메모리에도 반영
    st = strategy_manager.get_strategy(strategy_name) if strategy_manager else None
    if st:
        st.set_enabled(True)

    await _trigger_analyzer_reload()
    return {"success": True, "strategy": strategy_name, "enabled": True}


@app.post("/api/strategies/{strategy_name}/disable")
async def disable_strategy(strategy_name: str, user: dict = Depends(require_auth)):
    """전략 비활성화 (DB 저장 + analyzer 반영)"""
    if not strategy_settings_repo:
        raise HTTPException(status_code=500, detail="strategy_settings_repo가 초기화되지 않았습니다")
    ok = strategy_settings_repo.patch(strategy_name, enabled=False, config_patch=None)
    if not ok:
        raise HTTPException(status_code=500, detail="전략 비활성화(DB 저장) 실패")

    st = strategy_manager.get_strategy(strategy_name) if strategy_manager else None
    if st:
        st.set_enabled(False)

    await _trigger_analyzer_reload()
    return {"success": True, "strategy": strategy_name, "enabled": False}


@app.put("/api/strategies/{strategy_name}")
async def update_strategy(strategy_name: str, req: StrategyUpdateRequest, user: dict = Depends(require_auth)):
    """전략 설정 업데이트 (DB 저장 + analyzer 반영)"""
    if not strategy_settings_repo:
        raise HTTPException(status_code=500, detail="strategy_settings_repo가 초기화되지 않았습니다")

    config_patch = req.config or {}
    # enabled는 별도 필드로 관리 (config에 들어와도 무시)
    if isinstance(config_patch, dict) and 'enabled' in config_patch:
        config_patch = dict(config_patch)
        config_patch.pop('enabled', None)

    ok = strategy_settings_repo.patch(strategy_name, enabled=req.enabled, config_patch=config_patch)
    if not ok:
        raise HTTPException(status_code=500, detail="전략 설정 업데이트(DB 저장) 실패")

    # web_server 메모리에도 반영 (update_config가 런타임 필드를 동기화하도록 개선됨)
    st = strategy_manager.get_strategy(strategy_name) if strategy_manager else None
    if st:
        if req.enabled is not None:
            st.set_enabled(bool(req.enabled))
        if config_patch:
            st.update_config(config_patch)

    await _trigger_analyzer_reload()
    saved = strategy_settings_repo.get(strategy_name)
    return {"success": True, "strategy": strategy_name, "saved": saved}


@app.get("/api/symbols")
async def get_symbols():
    """저장된 심볼 목록"""
    try:
        query = "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
        df = pd.read_sql(query, db_engine)
        symbols = df['symbol'].tolist()
        return {"symbols": symbols, "count": len(symbols)}
    except Exception as e:
        logger.error(f"심볼 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Binance USD-M Futures API (USDT-margined perpetual)
BINANCE_USDM_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"


@app.get("/api/binance-symbols")
async def get_binance_symbols(query: str = "", limit: int = 500):
    """바이낸스 USD-M 선물(USDT 마진 영구선물) 전체 심볼 목록 (검색 필터 지원)"""
    import httpx

    try:
        # 캐시된 심볼 목록 사용 (없으면 API 호출)
        if not hasattr(app_state, 'binance_symbols') or not app_state.binance_symbols:
            async with httpx.AsyncClient() as client:
                res = await client.get(BINANCE_USDM_EXCHANGE_INFO, timeout=10.0)
                data = res.json()
                app_state.binance_symbols = [
                    s['symbol'] for s in data.get('symbols', [])
                    if s.get('status') == 'TRADING'
                    and s.get('contractType') == 'PERPETUAL'
                    and s.get('symbol', '').endswith('USDT')
                ]
                app_state.binance_symbols.sort()

        symbols = app_state.binance_symbols

        # 검색 필터 적용 (query가 있으면 필터링, 없으면 전체 반환)
        if query:
            query = query.upper().strip()
            symbols = [s for s in symbols if query in s]

        # limit 적용 (기본 500, 검색 시에도 충분한 결과)
        symbols = symbols[:min(limit, 500)]

        return {"symbols": symbols, "count": len(symbols), "total": len(app_state.binance_symbols)}

    except Exception as e:
        logger.error(f"바이낸스 심볼 조회 실패: {e}")
        # 기본 심볼 목록 반환 (확장)
        default_symbols = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT',
            'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT',
            'ETCUSDT', 'XLMUSDT', 'NEARUSDT', 'APTUSDT', 'ARBUSDT', 'OPUSDT', 'INJUSDT',
            'SUIUSDT', 'SEIUSDT', 'TIAUSDT', 'FILUSDT', 'IMXUSDT', 'RENDERUSDT', 'PEPEUSDT'
        ]
        if query:
            query = query.upper()
            default_symbols = [s for s in default_symbols if query in s]
        return {"symbols": default_symbols, "count": len(default_symbols), "total": len(default_symbols)}


@app.get("/api/watched-symbols")
async def get_watched_symbols():
    """감시 심볼 목록"""
    try:
        watched_list = watched_symbols_repo.get_all(enabled_only=False)
        return {
            "symbols": app_state.watched_symbols,
            "watched_list": watched_list,
            "timeframe": app_state.timeframe,
            "interval": app_state.analysis_interval,
            "count": len(app_state.watched_symbols)
        }
    except Exception as e:
        logger.error(f"감시 심볼 조회 실패: {e}")
        return {
            "symbols": app_state.watched_symbols,
            "timeframe": app_state.timeframe,
            "interval": app_state.analysis_interval,
            "count": len(app_state.watched_symbols)
        }


@app.get("/api/watched-symbols/with-strategies")
async def get_watched_symbols_with_strategies():
    """전략 정보 포함 감시 심볼"""
    import time
    t0 = time.time()
    try:
        watched_list = watched_symbols_repo.get_all_with_strategies(enabled_only=False)
        logger.info(f"[API /watched-symbols/with-strategies] {(time.time()-t0)*1000:.0f}ms ({len(watched_list)}개)")
        return {
            "symbols": app_state.watched_symbols,
            "watched_list": watched_list,
            "count": len(watched_list)
        }
    except Exception as e:
        logger.error(f"조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class AddSymbolRequest(BaseModel):
    """심볼 추가 요청"""
    symbol: str
    timeframe: str = "15m"
    strategies: str = ""
    notes: str = ""


class UpdateStrategiesRequest(BaseModel):
    """전략 업데이트 요청"""
    symbol: str
    strategies: str
    timeframe: str = None


class UpdateSortOrderRequest(BaseModel):
    """심볼 순서 업데이트 요청"""
    symbols: List[str]


@app.post("/api/watched-symbols/add")
async def add_watched_symbol(request: AddSymbolRequest):
    """감시 심볼 추가"""
    try:
        symbol = request.symbol.upper().strip()
        if not symbol:
            raise HTTPException(status_code=400, detail="심볼을 입력하세요")

        # DB에 저장
        if request.strategies:
            success = watched_symbols_repo.add_with_strategy(
                symbol=symbol,
                strategies=request.strategies,
                timeframe=request.timeframe,
                enabled=True,
                notes=request.notes
            )
        else:
            success = watched_symbols_repo.add(
                symbol=symbol,
                timeframe=request.timeframe,
                enabled=True
            )

        if success:
            # 메모리 상태 업데이트
            if symbol not in app_state.watched_symbols:
                app_state.watched_symbols.append(symbol)

            logger.info(f"✅ 감시 심볼 추가: {symbol}")

            return {
                "success": True,
                "symbol": symbol,
                "strategies": request.strategies,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            raise HTTPException(status_code=500, detail="심볼 추가 실패")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"심볼 추가 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/remove")
async def remove_watched_symbol(symbol: str):
    """감시 심볼 제거"""
    try:
        symbol = symbol.upper().strip()

        # DB에서 삭제
        success = watched_symbols_repo.remove(symbol)

        if success:
            # 메모리에서도 제거
            if symbol in app_state.watched_symbols:
                app_state.watched_symbols.remove(symbol)

            logger.info(f"🗑️ 감시 심볼 제거: {symbol}")

            return {
                "success": True,
                "symbol": symbol,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            raise HTTPException(status_code=404, detail="심볼을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"심볼 제거 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/toggle")
async def toggle_watched_symbol(symbol: str):
    """감시 심볼 활성화/비활성화 토글"""
    try:
        symbol = symbol.upper().strip()

        new_state = watched_symbols_repo.toggle(symbol)

        if new_state is not None:
            # 메모리 상태 업데이트
            if new_state and symbol not in app_state.watched_symbols:
                app_state.watched_symbols.append(symbol)
            elif not new_state and symbol in app_state.watched_symbols:
                app_state.watched_symbols.remove(symbol)

            logger.info(f"🔄 감시 심볼 토글: {symbol} → {'활성화' if new_state else '비활성화'}")

            return {
                "success": True,
                "symbol": symbol,
                "enabled": new_state,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            raise HTTPException(status_code=404, detail="심볼을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"심볼 토글 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/update-strategies")
async def update_symbol_strategies(request: UpdateStrategiesRequest):
    """심볼의 전략 및 타임프레임 정보 업데이트"""
    try:
        symbol = request.symbol.upper().strip()

        success = watched_symbols_repo.update_strategies(
            symbol,
            request.strategies,
            request.timeframe
        )

        if success:
            tf_info = f" (TF: {request.timeframe})" if request.timeframe else ""
            logger.info(f"✅ 전략 업데이트: {symbol} → {request.strategies}{tf_info}")

            return {
                "success": True,
                "symbol": symbol,
                "strategies": request.strategies,
                "timeframe": request.timeframe
            }
        else:
            raise HTTPException(status_code=404, detail="심볼을 찾을 수 없습니다")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전략 업데이트 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/update-order")
async def update_symbols_order(request: UpdateSortOrderRequest):
    """심볼 순서 업데이트"""
    try:
        success = watched_symbols_repo.update_sort_order(request.symbols)

        if success:
            logger.info(f"✅ 심볼 순서 업데이트: {len(request.symbols)}개")
            return {
                "success": True,
                "count": len(request.symbols)
            }
        else:
            raise HTTPException(status_code=500, detail="순서 업데이트 실패")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"순서 업데이트 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/sort-alphabetically")
async def sort_symbols_alphabetically():
    """심볼 알파벳순 정렬"""
    try:
        success = watched_symbols_repo.sort_alphabetically()

        if success:
            logger.info("✅ 심볼 알파벳순 정렬 완료")
            return {
                "success": True,
                "message": "알파벳순 정렬 완료"
            }
        else:
            raise HTTPException(status_code=500, detail="정렬 실패")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"정렬 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 신호 API
# ============================================================

@app.get("/api/signals/current")
async def get_current_signals(include_rejected: bool = True):
    """현재 신호 (메모리 + DB에서 최근 24시간)

    Args:
        include_rejected: AI 거부 신호 포함 여부 (기본: True)
    """
    import time
    api_start = time.time()
    signals_data = []

    # 1. 메모리에 있는 신호
    t0 = time.time()
    for s in app_state.signals:
        # AI 거부 신호 필터링
        if not include_rejected:
            ai_analysis = s.metadata.get('ai_analysis', {}) if s.metadata else {}
            if ai_analysis.get('decision') == 'reject':
                continue
        signal_dict = {
            "strategy_name": s.strategy_name,
            "symbol": s.symbol,
            "timeframe": s.timeframe,
            "signal_type": s.signal_type,
            "entry_price": s.entry_price,
            "stop_loss": s.stop_loss,
            "take_profit_1": s.take_profit_1,
            "confidence": s.confidence,
            "risk_reward": s.risk_reward,
            "reasons": s.reasons,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "source": "memory"
        }
        if 'ai_analysis' in s.metadata:
            ai = s.metadata['ai_analysis']
            signal_dict['ai_analysis'] = {
                'decision': ai.get('decision'),
                'confidence': ai.get('confidence'),
                'reasoning': ai.get('reasoning'),
                'rejected': ai.get('rejected', False)
            }
        signals_data.append(signal_dict)
    logger.debug(f"[API /signals/current] 메모리 신호 처리: {(time.time()-t0)*1000:.0f}ms ({len(signals_data)}개)")

    # 2. DB에서 최근 24시간 신호 조회
    t0 = time.time()
    try:
        from datetime import timedelta
        start_date = datetime.now() - timedelta(hours=24)
        db_signals = signals_repo.get_signals(
            limit=50,
            start_date=start_date,
            result_status='pending'  # 아직 미처리된 신호만
        )

        # 중복 제거 (같은 symbol + signal_type + entry_price)
        existing_keys = set()
        for s in signals_data:
            key = f"{s['symbol']}_{s['signal_type']}_{s.get('entry_price', 0):.2f}"
            existing_keys.add(key)

        for s in db_signals:
            # AI 거부 신호 필터링
            if not include_rejected and s.get('ai_decision') == 'reject':
                continue

            key = f"{s.get('symbol', '')}_{s.get('signal_type', '')}_{s.get('entry_price', 0):.2f}"
            if key not in existing_keys:
                signal_dict = {
                    "strategy_name": s.get('strategy_name', 'unknown'),
                    "symbol": s.get('symbol', ''),
                    "timeframe": s.get('timeframe', '15m'),
                    "signal_type": s.get('signal_type', ''),
                    "entry_price": s.get('entry_price', 0),
                    "stop_loss": s.get('stop_loss', 0),
                    "take_profit_1": s.get('take_profit_1', 0),
                    "confidence": s.get('confidence', 0.5),
                    "risk_reward": s.get('risk_reward', 0),
                    "reasons": s.get('reasons', []),
                    "created_at": s.get('created_at').isoformat() if s.get('created_at') else None,
                    "source": "db"
                }
                if s.get('ai_decision'):
                    signal_dict['ai_analysis'] = {
                        'decision': s.get('ai_decision'),
                        'confidence': s.get('ai_confidence'),
                        'reasoning': s.get('ai_reasoning'),
                        'rejected': s.get('ai_decision') == 'reject'
                    }
                signals_data.append(signal_dict)
                existing_keys.add(key)

    except Exception as e:
        logger.warning(f"DB 신호 조회 실패 (무시): {e}")
    logger.debug(f"[API /signals/current] DB 신호 조회: {(time.time()-t0)*1000:.0f}ms")

    # 시간순 정렬 (최신 순)
    t0 = time.time()
    signals_data.sort(key=lambda x: x.get('created_at') or '', reverse=True)
    logger.debug(f"[API /signals/current] 정렬: {(time.time()-t0)*1000:.0f}ms")

    logger.info(f"[API /signals/current] 총 {(time.time()-api_start)*1000:.0f}ms ({len(signals_data)}개)")
    return {
        "signals": signals_data,
        "count": len(signals_data),
        "last_update": app_state.last_update.isoformat() if app_state.last_update else None
    }


@app.get("/api/signals/list")
async def get_signals_list(
    limit: int = 100,
    offset: int = 0,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    signal_type: Optional[str] = None,
    ai_decision: Optional[str] = None,
    result_status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """신호 리스트 (DB)"""
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        signals = signals_repo.get_signals(
            limit=limit,
            offset=offset,
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            ai_decision=ai_decision,
            result_status=result_status,
            start_date=start_dt,
            end_date=end_dt
        )

        total_count = signals_repo.get_signals_count(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            ai_decision=ai_decision,
            result_status=result_status,
            start_date=start_dt,
            end_date=end_dt
        )

        # NaN 처리 (JSON 직렬화 오류 방지)
        for signal in signals:
            for key in ['pnl', 'pnl_percent', 'r_multiple', 'max_favorable_excursion', 'max_adverse_excursion',
                        'confidence', 'ai_confidence', 'risk_reward']:
                if key in signal and signal[key] is not None:
                    try:
                        if math.isnan(signal[key]) or math.isinf(signal[key]):
                            signal[key] = None
                    except (TypeError, ValueError):
                        pass

        return {
            "signals": signals,
            "count": total_count,
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"신호 리스트 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/stats")
async def get_signals_statistics(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    days: int = 30
):
    """신호 통계"""
    try:
        stats = signals_repo.get_statistics(symbol=symbol, timeframe=timeframe, days=days)
        return stats
    except Exception as e:
        logger.error(f"통계 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/time-analysis")
async def get_signals_time_analysis(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    days: int = 30
):
    """요일별/시간별 신호 분석"""
    try:
        cutoff = datetime.now() - timedelta(days=days)

        # 기본 WHERE 조건
        where_clause = "WHERE s.created_at >= %(cutoff)s"
        params = {'cutoff': cutoff}

        if symbol:
            where_clause += " AND s.symbol = %(symbol)s"
            params['symbol'] = symbol

        if timeframe:
            where_clause += " AND s.timeframe = %(timeframe)s"
            params['timeframe'] = timeframe

        # 요일별 분석 (PostgreSQL: EXTRACT(DOW FROM ...) returns 0=Sunday)
        day_query = f"""
            SELECT
                EXTRACT(DOW FROM s.created_at)::int as day_of_week,
                COUNT(*)::int as count,
                COUNT(CASE WHEN sr.status IN ('tp1_hit', 'tp2_hit') THEN 1 END)::int as wins,
                COUNT(CASE WHEN sr.status = 'sl_hit' THEN 1 END)::int as losses,
                COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent
            FROM tradebot_signals s
            LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
            {where_clause}
            GROUP BY EXTRACT(DOW FROM s.created_at)
            ORDER BY day_of_week
        """

        # 시간별 분석 (KST = UTC + 9)
        hour_query = f"""
            SELECT
                EXTRACT(HOUR FROM s.created_at + INTERVAL '9 hours')::int as hour,
                COUNT(*)::int as count,
                COUNT(CASE WHEN sr.status IN ('tp1_hit', 'tp2_hit') THEN 1 END)::int as wins,
                COUNT(CASE WHEN sr.status = 'sl_hit' THEN 1 END)::int as losses,
                COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent
            FROM tradebot_signals s
            LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
            {where_clause}
            GROUP BY EXTRACT(HOUR FROM s.created_at + INTERVAL '9 hours')
            ORDER BY hour
        """

        logger.info(f"[time-analysis] 쿼리 실행: days={days}, symbol={symbol}, timeframe={timeframe}")

        day_df = pd.read_sql(day_query, db_engine, params=params)
        hour_df = pd.read_sql(hour_query, db_engine, params=params)

        logger.info(f"[time-analysis] 결과: day={len(day_df)}행, hour={len(hour_df)}행")

        # NaN 처리
        day_df = day_df.fillna(0)
        hour_df = hour_df.fillna(0)

        return {
            "by_day": day_df.to_dict('records'),
            "by_hour": hour_df.to_dict('records')
        }

    except Exception as e:
        logger.error(f"시간 분석 조회 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/strategy-analysis")
async def get_signals_strategy_analysis(
    days: int = 30,
    top_k: int = 10,
    source: Optional[str] = None,  # 'signal' or 'real' or None (all)
    result: Optional[str] = None,  # 'win' or 'loss' or None (all)
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    month: Optional[str] = None,  # 'YYYY-MM' format
    day_of_week: Optional[int] = None,  # 0=Sun, 1=Mon, ...
    hour: Optional[int] = None  # 0-23 (KST)
):
    """
    전략 분석 - Symbol Top10, 전략별, 월별/요일별/시간별 통계
    인터랙티브 필터링 지원

    Args:
        days: 분석 기간 (일)
        top_k: 상위 N개
        source: 'signal' (신호만), 'real' (실제 거래만), None (전체)
        result: 'win' (승리만), 'loss' (패배만), None (전체)
        symbol: 심볼 필터
        strategy: 전략 필터
        month: 월 필터 (YYYY-MM)
        day_of_week: 요일 필터 (0=Sun)
        hour: 시간 필터 (KST)

    Returns:
        - by_symbol: 심볼별 Top10 통계
        - by_strategy: 전략별 통계
        - by_month: 월별 통계
        - by_day: 요일별 통계
        - by_hour: 시간별 통계
        - active_filters: 현재 활성 필터
    """
    try:
        cutoff = datetime.now() - timedelta(days=days)
        params = {'cutoff': cutoff, 'top_k': top_k}

        # ============================================================
        # 실제 거래 (tradebot_positions) - 바이낸스 실거래 데이터
        # ============================================================
        if source == 'real':
            # positions 테이블용 WHERE 조건
            pos_conditions = ["p.open_time >= %(cutoff)s", "p.status = 'closed'"]

            # Win/Loss 필터
            if result == 'win':
                pos_conditions.append("p.pnl > 0")
            elif result == 'loss':
                pos_conditions.append("p.pnl <= 0")

            if symbol:
                pos_conditions.append("p.symbol = %(symbol)s")
                params['symbol'] = symbol

            if month:
                pos_conditions.append("TO_CHAR(p.open_time + INTERVAL '9 hours', 'YYYY-MM') = %(month)s")
                params['month'] = month

            if day_of_week is not None:
                pos_conditions.append("EXTRACT(DOW FROM p.open_time + INTERVAL '9 hours') = %(day_of_week)s")
                params['day_of_week'] = day_of_week

            if hour is not None:
                pos_conditions.append("EXTRACT(HOUR FROM p.open_time + INTERVAL '9 hours') = %(hour)s")
                params['hour'] = hour

            pos_where = " AND ".join(pos_conditions)

            # result 필터에 따른 wins/losses 계산 방식 결정 (positions)
            # pnl이 NULL인 경우는 wins도 losses도 아님 (미결 또는 데이터 없음)
            if result == 'win':
                pos_wins_expr = "COUNT(*)::int"
                pos_losses_expr = "0::int"
                pos_win_rate_expr = "100.0::float"
            elif result == 'loss':
                pos_wins_expr = "0::int"
                pos_losses_expr = "COUNT(*)::int"
                pos_win_rate_expr = "0.0::float"
            else:
                # pnl IS NOT NULL인 경우만 wins/losses 카운트
                pos_wins_expr = "COUNT(CASE WHEN p.pnl IS NOT NULL AND p.pnl > 0 THEN 1 END)::int"
                pos_losses_expr = "COUNT(CASE WHEN p.pnl IS NOT NULL AND p.pnl < 0 THEN 1 END)::int"
                pos_win_rate_expr = """CASE WHEN COUNT(CASE WHEN p.pnl IS NOT NULL THEN 1 END) > 0 THEN
                    ROUND(COUNT(CASE WHEN p.pnl > 0 THEN 1 END)::numeric / COUNT(CASE WHEN p.pnl IS NOT NULL THEN 1 END)::numeric * 100, 1)
                ELSE 0 END::float"""

            # 심볼별 Top10
            symbol_query = f"""
                SELECT
                    p.symbol,
                    COUNT(*)::int as signal_count,
                    {pos_wins_expr} as wins,
                    {pos_losses_expr} as losses,
                    COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent,
                    {pos_win_rate_expr} as win_rate
                FROM tradebot_positions p
                WHERE {pos_where}
                GROUP BY p.symbol
                ORDER BY signal_count DESC
                LIMIT %(top_k)s
            """

            # 전략별 통계 - positions에는 strategy가 없으므로 'binance_real'로 표시
            strategy_query = f"""
                SELECT
                    'binance_real' as strategy_name,
                    COUNT(*)::int as signal_count,
                    {pos_wins_expr} as wins,
                    {pos_losses_expr} as losses,
                    COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent,
                    {pos_win_rate_expr} as win_rate
                FROM tradebot_positions p
                WHERE {pos_where}
            """

            # 월별 통계 (KST = UTC + 9)
            month_query = f"""
                SELECT
                    TO_CHAR(p.open_time + INTERVAL '9 hours', 'YYYY-MM') as month,
                    COUNT(*)::int as signal_count,
                    {pos_wins_expr} as wins,
                    {pos_losses_expr} as losses,
                    COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent,
                    {pos_win_rate_expr} as win_rate
                FROM tradebot_positions p
                WHERE {pos_where}
                GROUP BY TO_CHAR(p.open_time + INTERVAL '9 hours', 'YYYY-MM')
                ORDER BY month DESC
            """

            # 요일별 통계 (KST = UTC + 9)
            day_query = f"""
                SELECT
                    EXTRACT(DOW FROM p.open_time + INTERVAL '9 hours')::int as day_of_week,
                    COUNT(*)::int as signal_count,
                    {pos_wins_expr} as wins,
                    {pos_losses_expr} as losses,
                    COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent,
                    {pos_win_rate_expr} as win_rate
                FROM tradebot_positions p
                WHERE {pos_where}
                GROUP BY EXTRACT(DOW FROM p.open_time + INTERVAL '9 hours')
                ORDER BY day_of_week
            """

            # 시간별 통계 (KST)
            hour_query = f"""
                SELECT
                    EXTRACT(HOUR FROM p.open_time + INTERVAL '9 hours')::int as hour,
                    COUNT(*)::int as signal_count,
                    {pos_wins_expr} as wins,
                    {pos_losses_expr} as losses,
                    COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent,
                    {pos_win_rate_expr} as win_rate
                FROM tradebot_positions p
                WHERE {pos_where}
                GROUP BY EXTRACT(HOUR FROM p.open_time + INTERVAL '9 hours')
                ORDER BY hour
            """

        # ============================================================
        # 신호 (tradebot_signals) - 분석 서버 생성 신호 + 시뮬레이션
        # ============================================================
        else:
            # signals 테이블용 WHERE 조건
            base_conditions = ["s.created_at >= %(cutoff)s"]

            # Win/Loss 필터 (signal_results 테이블 기준)
            if result == 'win':
                base_conditions.append("sr.status IN ('tp1_hit', 'tp2_hit')")
            elif result == 'loss':
                base_conditions.append("sr.status = 'sl_hit'")

            if symbol:
                base_conditions.append("s.symbol = %(symbol)s")
                params['symbol'] = symbol

            if strategy:
                base_conditions.append("s.strategy_name = %(strategy)s")
                params['strategy'] = strategy

            if month:
                base_conditions.append("TO_CHAR(s.created_at + INTERVAL '9 hours', 'YYYY-MM') = %(month)s")
                params['month'] = month

            if day_of_week is not None:
                base_conditions.append("EXTRACT(DOW FROM s.created_at + INTERVAL '9 hours') = %(day_of_week)s")
                params['day_of_week'] = day_of_week

            if hour is not None:
                base_conditions.append("EXTRACT(HOUR FROM s.created_at + INTERVAL '9 hours') = %(hour)s")
                params['hour'] = hour

            where_clause = " AND ".join(base_conditions)

            # result 필터에 따른 wins/losses 계산 방식 결정
            if result == 'win':
                # Win 필터: 모든 레코드가 Win
                wins_expr = "COUNT(*)::int"
                losses_expr = "0::int"
                win_rate_expr = "100.0::float"
            elif result == 'loss':
                # Loss 필터: 모든 레코드가 Loss
                wins_expr = "0::int"
                losses_expr = "COUNT(*)::int"
                win_rate_expr = "0.0::float"
            else:
                # 전체: 정상 계산
                wins_expr = "COUNT(CASE WHEN sr.status IN ('tp1_hit', 'tp2_hit') THEN 1 END)::int"
                losses_expr = "COUNT(CASE WHEN sr.status = 'sl_hit' THEN 1 END)::int"
                win_rate_expr = """CASE WHEN COUNT(*) > 0 THEN
                    ROUND(COUNT(CASE WHEN sr.status IN ('tp1_hit', 'tp2_hit') THEN 1 END)::numeric / COUNT(*)::numeric * 100, 1)
                ELSE 0 END::float"""

            # 심볼별 Top10 (신호 수 기준)
            symbol_query = f"""
                SELECT
                    s.symbol,
                    COUNT(*)::int as signal_count,
                    {wins_expr} as wins,
                    {losses_expr} as losses,
                    COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent,
                    {win_rate_expr} as win_rate
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE {where_clause}
                GROUP BY s.symbol
                ORDER BY signal_count DESC
                LIMIT %(top_k)s
            """

            # 전략별 통계
            strategy_query = f"""
                SELECT
                    s.strategy_name,
                    COUNT(*)::int as signal_count,
                    {wins_expr} as wins,
                    {losses_expr} as losses,
                    COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent,
                    {win_rate_expr} as win_rate
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE {where_clause}
                GROUP BY s.strategy_name
                ORDER BY signal_count DESC
            """

            # 월별 통계
            # 월별 통계 (KST = UTC + 9)
            month_query = f"""
                SELECT
                    TO_CHAR(s.created_at + INTERVAL '9 hours', 'YYYY-MM') as month,
                    COUNT(*)::int as signal_count,
                    {wins_expr} as wins,
                    {losses_expr} as losses,
                    COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent,
                    {win_rate_expr} as win_rate
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE {where_clause}
                GROUP BY TO_CHAR(s.created_at + INTERVAL '9 hours', 'YYYY-MM')
                ORDER BY month DESC
            """

            # 요일별 통계 (KST = UTC + 9, 0=Sunday)
            day_query = f"""
                SELECT
                    EXTRACT(DOW FROM s.created_at + INTERVAL '9 hours')::int as day_of_week,
                    COUNT(*)::int as signal_count,
                    {wins_expr} as wins,
                    {losses_expr} as losses,
                    COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent,
                    {win_rate_expr} as win_rate
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE {where_clause}
                GROUP BY EXTRACT(DOW FROM s.created_at + INTERVAL '9 hours')
                ORDER BY day_of_week
            """

            # 시간별 통계 (KST = UTC + 9)
            hour_query = f"""
                SELECT
                    EXTRACT(HOUR FROM s.created_at + INTERVAL '9 hours')::int as hour,
                    COUNT(*)::int as signal_count,
                    {wins_expr} as wins,
                    {losses_expr} as losses,
                    COALESCE(SUM(sr.pnl), 0)::float as total_pnl,
                    COALESCE(AVG(sr.pnl_percent), 0)::float as avg_pnl_percent,
                    {win_rate_expr} as win_rate
                FROM tradebot_signals s
                LEFT JOIN tradebot_signal_results sr ON s.signal_id = sr.signal_id
                WHERE {where_clause}
                GROUP BY EXTRACT(HOUR FROM s.created_at + INTERVAL '9 hours')
                ORDER BY hour
            """

        logger.info(f"[strategy-analysis] 쿼리 실행: days={days}, top_k={top_k}, source={source}, symbol={symbol}, strategy={strategy}, month={month}, day_of_week={day_of_week}, hour={hour}")

        symbol_df = pd.read_sql(symbol_query, db_engine, params=params)
        strategy_df = pd.read_sql(strategy_query, db_engine, params=params)
        month_df = pd.read_sql(month_query, db_engine, params=params)
        day_df = pd.read_sql(day_query, db_engine, params=params)
        hour_df = pd.read_sql(hour_query, db_engine, params=params)

        # NaN 처리
        symbol_df = symbol_df.fillna(0)
        strategy_df = strategy_df.fillna(0)
        month_df = month_df.fillna(0)
        day_df = day_df.fillna(0)
        hour_df = hour_df.fillna(0)

        logger.info(f"[strategy-analysis] 결과: symbol={len(symbol_df)}, strategy={len(strategy_df)}, month={len(month_df)}, day={len(day_df)}, hour={len(hour_df)}")

        # 전체 요약 계산 (strategy_df에서 합계)
        summary = {
            "total_count": int(strategy_df['signal_count'].sum()) if len(strategy_df) > 0 else 0,
            "total_wins": int(strategy_df['wins'].sum()) if len(strategy_df) > 0 else 0,
            "total_losses": int(strategy_df['losses'].sum()) if len(strategy_df) > 0 else 0,
            "total_pnl": float(strategy_df['total_pnl'].sum()) if len(strategy_df) > 0 else 0.0,
            "avg_pnl_percent": float(strategy_df['avg_pnl_percent'].mean()) if len(strategy_df) > 0 else 0.0,
            "win_rate": 0.0
        }
        if summary["total_count"] > 0:
            summary["win_rate"] = round(summary["total_wins"] / summary["total_count"] * 100, 1)

        # 활성 필터 정보
        active_filters = {}
        if source:
            active_filters['source'] = source
        if result:
            active_filters['result'] = result
        if symbol:
            active_filters['symbol'] = symbol
        if strategy:
            active_filters['strategy'] = strategy
        if month:
            active_filters['month'] = month
        if day_of_week is not None:
            active_filters['day_of_week'] = day_of_week
        if hour is not None:
            active_filters['hour'] = hour

        return {
            "by_symbol": symbol_df.to_dict('records'),
            "by_strategy": strategy_df.to_dict('records'),
            "by_month": month_df.to_dict('records'),
            "by_day": day_df.to_dict('records'),
            "by_hour": hour_df.to_dict('records'),
            "summary": summary,
            "active_filters": active_filters
        }

    except Exception as e:
        logger.error(f"전략 분석 조회 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/{signal_id}")
async def get_signal_detail(signal_id: str):
    """신호 상세"""
    try:
        signal = signals_repo.get_signal_by_id(signal_id)
        if not signal:
            raise HTTPException(status_code=404, detail="신호를 찾을 수 없습니다")
        return signal
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"신호 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/signals/simulate")
async def simulate_signals(
    capital: float = 10000,
    risk_percent: float = 2.0,
    update_all: bool = False,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    signal_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    hours_limit: int = 72
):
    """
    신호 시뮬레이션 - 실제 가격 데이터로 SL/TP 도달 여부 확인 및 DB 업데이트

    Args:
        capital: 총 자본금 (USD)
        risk_percent: 리스크 비율 (%)
        update_all: True면 이미 시뮬레이션된 신호도 재계산
        symbol, timeframe 등: 필터 조건
        hours_limit: 신호 유효 시간 (기본 72시간)

    Returns:
        시뮬레이션 결과 (업데이트된 신호 수, 총 수익 등)
    """
    import ccxt
    from datetime import timezone

    try:
        # 필터 조건으로 pending 상태인 신호만 조회
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        signals = signals_repo.get_signals(
            limit=1000,
            offset=0,
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            start_date=start_dt,
            end_date=end_dt
        )

        if not signals:
            return {
                "success": True,
                "message": "No signals found for simulation",
                "total": 0,
                "updated": 0,
                "skipped": 0
            }

        # update_all=True면 모든 신호, False면 pending 상태인 신호만 필터링
        if update_all:
            pending_signals = signals
            logger.info(f"[simulate] update_all=True: 모든 신호 {len(pending_signals)}개 재시뮬레이션")
        else:
            pending_signals = [s for s in signals if s.get('result_status') in (None, 'pending')]
            logger.info(f"[simulate] update_all=False: pending 신호 {len(pending_signals)}개만 시뮬레이션")

        if not pending_signals:
            return {
                "success": True,
                "message": "No pending signals to simulate",
                "total": len(signals),
                "updated": 0,
                "skipped": len(signals)
            }

        # ccxt 초기화
        ex = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        ex.load_markets()

        total_signals = len(pending_signals)
        updated_count = 0
        skipped_count = 0
        total_pnl = 0.0
        risk_amount = capital * (risk_percent / 100)

        simulation_results = []

        for signal in pending_signals:
            signal_id = signal.get('signal_id')
            sym = signal.get('symbol')
            tf = signal.get('timeframe', '15m')
            entry_price = signal.get('entry_price', 0)
            stop_loss = signal.get('stop_loss', 0)
            take_profit_1 = signal.get('take_profit_1', 0)
            sig_type = signal.get('signal_type')  # buy or sell
            created_at = signal.get('created_at')

            if not entry_price or not stop_loss or not created_at:
                skipped_count += 1
                continue

            # ccxt 심볼 변환
            ccxt_symbol = sym
            if '/' not in sym and sym.endswith('USDT'):
                base = sym[:-4]
                ccxt_symbol = f"{base}/USDT:USDT"

            try:
                # 신호 생성 시점부터 현재까지의 가격 데이터 조회
                if isinstance(created_at, str):
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))

                # DB는 KST(UTC+9)로 저장됨 - timezone-naive인 경우 KST로 간주
                if created_at.tzinfo is None:
                    # KST를 UTC로 변환 (9시간 빼기)
                    from zoneinfo import ZoneInfo
                    kst = ZoneInfo('Asia/Seoul')
                    created_at = created_at.replace(tzinfo=kst)

                since_ms = int(created_at.timestamp() * 1000)
                ohlcv = ex.fetch_ohlcv(ccxt_symbol, timeframe=tf, since=since_ms, limit=500)

                if not ohlcv or len(ohlcv) < 2:
                    skipped_count += 1
                    continue

                # SL/TP 도달 여부 확인
                result_status = None
                exit_price = None
                exit_time = None

                for candle in ohlcv[1:]:  # 첫 캔들은 신호 생성 시점이므로 제외
                    candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
                    high = candle[2]
                    low = candle[3]

                    if sig_type == 'buy':
                        # 롱: SL은 low가 stop_loss 이하, TP는 high가 take_profit_1 이상
                        if low <= stop_loss:
                            result_status = 'sl_hit'
                            exit_price = stop_loss
                            exit_time = candle_time
                            break
                        if take_profit_1 and high >= take_profit_1:
                            result_status = 'tp1_hit'
                            exit_price = take_profit_1
                            exit_time = candle_time
                            break
                    else:  # sell
                        # 숏: SL은 high가 stop_loss 이상, TP는 low가 take_profit_1 이하
                        if high >= stop_loss:
                            result_status = 'sl_hit'
                            exit_price = stop_loss
                            exit_time = candle_time
                            break
                        if take_profit_1 and low <= take_profit_1:
                            result_status = 'tp1_hit'
                            exit_price = take_profit_1
                            exit_time = candle_time
                            break

                # 유효 시간 초과 체크
                if not result_status:
                    last_candle_time = datetime.fromtimestamp(ohlcv[-1][0] / 1000, tz=timezone.utc)
                    hours_passed = (last_candle_time - created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600

                    if hours_passed >= hours_limit:
                        result_status = 'expired'
                        exit_price = ohlcv[-1][4]  # 마지막 close
                        exit_time = last_candle_time

                # MFE/MAE 계산 (모든 경우에 대해)
                mfe = 0.0  # Maximum Favorable Excursion
                mae = 0.0  # Maximum Adverse Excursion

                for candle in ohlcv[1:]:
                    high = candle[2]
                    low = candle[3]

                    if sig_type == 'buy':
                        # 롱: MFE는 최고가 대비 수익률, MAE는 최저가 대비 손실률
                        favorable = ((high - entry_price) / entry_price) * 100
                        adverse = ((entry_price - low) / entry_price) * 100
                    else:
                        # 숏: MFE는 최저가 대비 수익률, MAE는 최고가 대비 손실률
                        favorable = ((entry_price - low) / entry_price) * 100
                        adverse = ((high - entry_price) / entry_price) * 100

                    mfe = max(mfe, favorable)
                    mae = max(mae, adverse)

                # 결과가 있으면 DB 업데이트
                if result_status:
                    # PnL 계산
                    stop_distance = abs(entry_price - stop_loss)
                    position_size = risk_amount / stop_distance if stop_distance > 0 else 0

                    if sig_type == 'buy':
                        pnl = (exit_price - entry_price) * position_size
                        pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                    else:
                        pnl = (entry_price - exit_price) * position_size
                        pnl_percent = ((entry_price - exit_price) / entry_price) * 100

                    r_multiple = pnl / risk_amount if risk_amount > 0 else 0

                    # 소요 시간 계산
                    duration_minutes = 0
                    if exit_time and created_at:
                        created_utc = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
                        duration_minutes = int((exit_time - created_utc).total_seconds() / 60)

                    # DB 저장
                    result_data = {
                        'signal_id': signal_id,
                        'status': result_status,
                        'exit_price': float(exit_price),
                        'exit_time': exit_time,
                        'pnl': round(pnl, 2),
                        'pnl_percent': round(pnl_percent, 4),
                        'r_multiple': round(r_multiple, 2),
                        'max_favorable_excursion': round(mfe, 4),
                        'max_adverse_excursion': round(mae, 4),
                        'duration_minutes': duration_minutes,
                        'is_simulation': True,
                        'notes': f'Simulated with ${capital} capital, {risk_percent}% risk'
                    }

                    if signals_repo.save_result(result_data):
                        updated_count += 1
                        total_pnl += pnl

                        simulation_results.append({
                            "signal_id": signal_id,
                            "symbol": sym,
                            "signal_type": sig_type,
                            "result": result_status,
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "pnl": round(pnl, 2),
                            "pnl_percent": round(pnl_percent, 2),
                            "r_multiple": round(r_multiple, 2),
                            "duration_minutes": duration_minutes
                        })
                    else:
                        skipped_count += 1
                else:
                    # 아직 SL/TP 도달 안함 - pending 상태로 저장하여 필터에서 제외
                    import math
                    if math.isnan(mfe) or math.isinf(mfe):
                        mfe = 0.0
                    if math.isnan(mae) or math.isinf(mae):
                        mae = 0.0

                    result_data = {
                        'signal_id': signal_id,
                        'status': 'pending',
                        'exit_price': None,
                        'exit_time': None,
                        'pnl': None,
                        'pnl_percent': None,
                        'r_multiple': None,
                        'max_favorable_excursion': round(mfe, 4),
                        'max_adverse_excursion': round(mae, 4),
                        'duration_minutes': None,
                        'is_simulation': True,
                        'notes': f'Pending - No TP/SL hit yet (simulation: ${capital} @ {risk_percent}%)'
                    }

                    if signals_repo.save_result(result_data):
                        updated_count += 1
                        logger.info(f"⏳ {signal_id}: still pending (MFE: {mfe:.2f}%, MAE: {mae:.2f}%)")
                    else:
                        skipped_count += 1

            except Exception as e:
                logger.warning(f"신호 {signal_id} 시뮬레이션 실패: {e}")
                skipped_count += 1
                continue

        return {
            "success": True,
            "message": f"Simulation completed",
            "total": total_signals,
            "updated": updated_count,
            "skipped": skipped_count,
            "total_pnl": round(total_pnl, 2),
            "capital": capital,
            "risk_percent": risk_percent,
            "risk_amount_per_trade": round(risk_amount, 2),
            "results": simulation_results[:50]
        }

    except Exception as e:
        logger.error(f"시뮬레이션 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": str(e),
            "total": 0,
            "updated": 0,
            "skipped": 0
        }


# ============================================================
# 포지션 API
# ============================================================

@app.get("/api/positions/list")
async def get_positions_list(
    limit: int = 100,
    offset: int = 0,
    symbol: Optional[str] = None,
    side: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """포지션 리스트"""
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        positions = positions_repo.get_positions(
            limit=limit, offset=offset, symbol=symbol,
            side=side, status=status,
            start_date=start_dt, end_date=end_dt
        )

        # NaN/Inf 값 처리 (JSON 직렬화 오류 방지)
        for pos in positions:
            for key in ['exit_price', 'pnl', 'pnl_percent', 'commission', 'margin', 'entry_price', 'quantity']:
                if key in pos and pos[key] is not None:
                    try:
                        if math.isnan(pos[key]) or math.isinf(pos[key]):
                            pos[key] = None
                    except (TypeError, ValueError):
                        pass

        return {"positions": positions, "count": len(positions), "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"포지션 리스트 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/stats")
async def get_positions_statistics(symbol: Optional[str] = None, days: int = 30):
    """포지션 통계"""
    try:
        stats = positions_repo.get_statistics(symbol=symbol, days=days)
        return stats
    except Exception as e:
        logger.error(f"포지션 통계 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/binance/connection-status")
async def get_binance_connection_status():
    """
    바이낸스 API 연결 상태 확인

    Returns:
        연결 상태 정보
    """
    try:
        import ccxt

        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')

        if not api_key or not api_secret:
            return {
                "connected": False,
                "message": "API 키가 설정되지 않았습니다",
                "account_type": None
            }

        # 간단한 연결 테스트 (fetch_balance는 빠르고 안전)
        exchange_inst = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'},
            'timeout': 5000  # 5초 타임아웃
        })

        try:
            # 연결 테스트
            exchange_inst.load_markets()
            balance = exchange_inst.fetch_balance()
            
            return {
                "connected": True,
                "message": "연결 성공",
                "account_type": "Futures",
                "markets_loaded": len(exchange_inst.markets) if hasattr(exchange_inst, 'markets') else 0
            }
        except Exception as e:
            return {
                "connected": False,
                "message": f"연결 실패: {str(e)}",
                "account_type": None
            }

    except Exception as e:
        logger.error(f"바이낸스 연결 상태 확인 실패: {e}")
        return {
            "connected": False,
            "message": f"오류: {str(e)}",
            "account_type": None
        }


@app.post("/api/positions/sync-binance")
async def sync_binance_positions(
    date: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    바이낸스에서 포지션 히스토리를 가져와 DB에 저장

    Args:
        date: 동기화할 날짜 (YYYY-MM-DD) - 우선순위 1
        days: 조회할 기간 (일) - date가 없을 때 사용
        start_date: 시작 날짜 (YYYY-MM-DD) - date가 없을 때 사용
        end_date: 종료 날짜 (YYYY-MM-DD) - date가 없을 때 사용

    Returns:
        동기화 결과 (total, saved, skipped, deleted)
    """
    try:
        import ccxt

        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')

        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="API 키가 설정되지 않았습니다")

        exchange_inst = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange_inst.load_markets()

        # 날짜 지정 모드: 해당 날짜의 데이터 삭제 후 동기화
        deleted_count = 0
        if date:
            target_date = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
            start_dt = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = target_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            since_ms = int(start_dt.timestamp() * 1000)
            until_ms = int(end_dt.timestamp() * 1000)
            
            # 해당 날짜의 기존 데이터 삭제
            deleted_count = positions_repo.delete_positions_by_date(target_date)
            logger.info(f"바이낸스 동기화: {date} (기존 {deleted_count}개 삭제)")
        elif start_date and end_date:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            since_ms = int(start_dt.timestamp() * 1000)
            until_ms = int(end_dt.timestamp() * 1000)
            logger.info(f"바이낸스 동기화: {start_date} ~ {end_date}")
        else:
            since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
            until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            logger.info(f"바이낸스 동기화: 최근 {days}일")

        # 바이낸스 선물 전체 거래 히스토리 조회 (필터 없이 모든 심볼)
        all_trades = []

        # 바이낸스 선물의 모든 USDT 마켓 조회
        usdt_markets = [
            symbol for symbol, market in exchange_inst.markets.items()
            if market.get('quote') == 'USDT' and market.get('linear', True)
        ]
        logger.info(f"바이낸스 선물 USDT 마켓: {len(usdt_markets)}개")

        # 모든 마켓에서 거래 조회 (거래가 있는 심볼만)
        for ccxt_symbol in usdt_markets:
            try:
                trades = exchange_inst.fetch_my_trades(
                    symbol=ccxt_symbol,
                    since=since_ms,
                    limit=500
                )
                if trades:
                    all_trades.extend(trades)
                    logger.info(f"거래 조회 성공 ({ccxt_symbol}): {len(trades)}건")
            except Exception as e:
                # 거래 없는 심볼은 무시
                continue

        logger.info(f"총 {len(all_trades)}건 거래 조회됨")

        if not all_trades:
            return {"total": 0, "saved": 0, "skipped": 0, "message": "No trades found"}

        # 거래를 포지션으로 그룹화 (심볼 + side + 시간 기준)
        # 같은 심볼, 같은 방향, 5분 이내 거래는 하나의 포지션으로 묶음
        from collections import defaultdict

        TIME_WINDOW = 5 * 60 * 1000  # 5분 (밀리초)
        positions_map = defaultdict(list)

        # 시간순 정렬
        all_trades.sort(key=lambda x: x['timestamp'])

        for trade in all_trades:
            # BTC/USDT:USDT -> BTCUSDT 정규화
            raw_symbol = trade.get('symbol', '')
            if ':' in raw_symbol:
                raw_symbol = raw_symbol.split(':')[0]
            norm_symbol = raw_symbol.replace('/', '')

            side = trade.get('side', 'buy')
            key = f"{norm_symbol}_{side}"

            # 기존 그룹에 합칠 수 있는지 확인
            added = False
            for group in positions_map[key]:
                if abs(group['last_timestamp'] - trade['timestamp']) <= TIME_WINDOW:
                    group['trades'].append(trade)
                    group['last_timestamp'] = trade['timestamp']
                    added = True
                    break

            if not added:
                positions_map[key].append({
                    'trades': [trade],
                    'last_timestamp': trade['timestamp']
                })

        # 포지션 저장
        saved_count = 0
        skipped_count = 0
        total_count = 0

        for key, groups in positions_map.items():
            for group in groups:
                total_count += 1
                trades = group['trades']
                if not trades:
                    continue

                first_trade = trades[0]
                last_trade = trades[-1]

                # BTC/USDT:USDT -> BTCUSDT 정규화
                raw_symbol = first_trade.get('symbol', '')
                if ':' in raw_symbol:
                    raw_symbol = raw_symbol.split(':')[0]
                norm_symbol = raw_symbol.replace('/', '')

                # 합산 계산
                total_amount = sum(t.get('amount', 0) for t in trades)
                total_cost = sum(t.get('cost', 0) for t in trades)
                total_fee = sum(float(t.get('fee', {}).get('cost', 0) or 0) if t.get('fee') else 0 for t in trades)
                # realizedPnl이 문자열로 올 수 있음
                total_pnl = 0.0
                for t in trades:
                    pnl_val = t.get('info', {}).get('realizedPnl', 0)
                    try:
                        total_pnl += float(pnl_val) if pnl_val else 0
                    except (ValueError, TypeError):
                        pass

                avg_price = total_cost / total_amount if total_amount else 0

                # 포지션 ID 생성 (첫 거래 ID 기반)
                position_id = f"binance_{first_trade.get('id', '')}"

                # 손익률 계산
                pnl_percent = (total_pnl / total_cost * 100) if total_cost else 0

                position_data = {
                    'position_id': position_id,
                    'symbol': norm_symbol,
                    'side': first_trade.get('side', 'buy'),
                    'entry_price': avg_price,
                    'exit_price': avg_price,  # 실제 청산가가 없으므로 평균가 사용
                    'quantity': total_amount,
                    'pnl': total_pnl,
                    'pnl_percent': pnl_percent,
                    'commission': total_fee,
                    'status': 'closed',
                    'leverage': 1,  # 기본값
                    'margin': total_cost,
                    'open_time': datetime.fromtimestamp(first_trade['timestamp'] / 1000, tz=timezone.utc),
                    'close_time': datetime.fromtimestamp(last_trade['timestamp'] / 1000, tz=timezone.utc),
                    'trade_count': len(trades)
                }

                try:
                    if positions_repo.save_position(position_data):
                        saved_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.warning(f"포지션 저장 실패 ({position_id}): {e}")
                    skipped_count += 1

        return {
            "total": total_count,
            "saved": saved_count,
            "skipped": skipped_count,
            "deleted": deleted_count,
            "message": f"Synced {saved_count} positions from Binance (deleted {deleted_count} old positions)" if deleted_count > 0 else f"Synced {saved_count} positions from Binance"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"바이낸스 포지션 동기화 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/signals/match-positions")
async def match_signals_with_positions(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    time_window_hours: int = 2
):
    """
    신호와 DB 포지션 매칭

    DB에 저장된 포지션(바이낸스/CSV)과 신호를 매칭하여
    신호 테이블에 matched_position_id를 저장합니다.

    매칭 조건:
    - 같은 심볼
    - 같은 방향 (buy/sell)
    - 시간 차이가 time_window_hours 이내

    Args:
        symbol: 심볼 필터 (옵션)
        start_date: 시작 날짜 (YYYY-MM-DD)
        end_date: 종료 날짜 (YYYY-MM-DD)
        time_window_hours: 매칭 시간 윈도우 (기본 2시간)

    Returns:
        매칭 결과 (total_signals, matched, already_matched)
    """
    try:
        from datetime import timedelta

        # 1. 미매칭 신호 조회
        unmatched_signals = signals_repo.get_unmatched_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date
        )

        if not unmatched_signals:
            return {
                "total_signals": 0,
                "matched": 0,
                "already_matched": 0,
                "message": "매칭할 신호가 없습니다"
            }

        # 2. 해당 기간의 포지션 조회
        position_query = """
            SELECT position_id, symbol, side, open_time, entry_price
            FROM tradebot_positions
            WHERE 1=1
        """
        params = {}

        if symbol:
            position_query += " AND symbol = %(symbol)s"
            params['symbol'] = symbol

        if start_date:
            position_query += " AND open_time >= %(start_date)s"
            params['start_date'] = start_date

        if end_date:
            position_query += " AND open_time <= %(end_date)s::date + INTERVAL '1 day'"
            params['end_date'] = end_date

        position_query += " ORDER BY open_time DESC"

        import pandas as pd
        positions_df = pd.read_sql(position_query, db_engine, params=params)
        positions = positions_df.to_dict('records')

        if not positions:
            return {
                "total_signals": len(unmatched_signals),
                "matched": 0,
                "already_matched": 0,
                "message": "매칭할 포지션이 없습니다"
            }

        # 3. 신호와 포지션 매칭
        matched_count = 0
        time_window = timedelta(hours=time_window_hours)

        for signal in unmatched_signals:
            signal_time = signal['created_at']
            signal_symbol = signal['symbol']
            signal_side = signal['signal_type']  # 'buy' or 'sell'

            # 같은 심볼, 같은 방향, 시간 차이 내 포지션 찾기
            best_match = None
            min_time_diff = None

            for pos in positions:
                if pos['symbol'] != signal_symbol:
                    continue
                if pos['side'] != signal_side:
                    continue

                pos_time = pos['open_time']
                if isinstance(pos_time, str):
                    pos_time = datetime.fromisoformat(pos_time.replace('Z', '+00:00'))

                time_diff = abs((pos_time - signal_time).total_seconds())

                if time_diff <= time_window.total_seconds():
                    if min_time_diff is None or time_diff < min_time_diff:
                        min_time_diff = time_diff
                        best_match = pos

            # 가장 가까운 포지션과 매칭
            if best_match:
                if signals_repo.update_signal_match(signal['signal_id'], best_match['position_id']):
                    matched_count += 1

        return {
            "total_signals": len(unmatched_signals),
            "matched": matched_count,
            "positions_checked": len(positions),
            "time_window_hours": time_window_hours,
            "message": f"{matched_count}개 신호 매칭 완료"
        }

    except Exception as e:
        logger.error(f"신호-포지션 매칭 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/matched")
async def get_matched_signals(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    매칭된 신호 조회 (포지션 정보 포함)

    Args:
        symbol: 심볼 필터
        start_date: 시작 날짜 (YYYY-MM-DD)
        end_date: 종료 날짜 (YYYY-MM-DD)

    Returns:
        매칭된 신호 리스트 (포지션 정보 포함)
    """
    try:
        matched = signals_repo.get_matched_signals(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date
        )

        # datetime 객체를 ISO 문자열로 변환
        for item in matched:
            for key, value in item.items():
                if isinstance(value, datetime):
                    item[key] = value.isoformat()

        return {
            "count": len(matched),
            "signals": matched
        }

    except Exception as e:
        logger.error(f"매칭된 신호 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/positions/upload-csv")
async def upload_positions_csv(file: UploadFile = File(...)):
    """
    바이낸스 선물 거래 내역 CSV 업로드

    바이낸스 웹에서 다운로드한 CSV 파일을 업로드하여 DB에 저장합니다.
    지원 형식: 바이낸스 선물 거래 내역 (Trade History)

    예상 CSV 컬럼:
    - Time (UTC), Symbol, Side, Price, Quantity, Fee, Realized Profit
    """
    try:
        import io
        import csv

        # 파일 내용 읽기
        content = await file.read()

        # UTF-8 또는 UTF-8-BOM으로 디코딩 시도
        try:
            text = content.decode('utf-8-sig')  # BOM 자동 제거
        except UnicodeDecodeError:
            text = content.decode('utf-8')

        # CSV 파싱
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

        if not rows:
            raise HTTPException(status_code=400, detail="CSV 파일이 비어있습니다")

        logger.info(f"CSV 업로드: {len(rows)}행 읽음, 컬럼: {list(rows[0].keys())}")

        # 바이낸스 CSV 컬럼 매핑 (여러 형식 지원)
        # 형식 1 (Trade History): Date(UTC), Symbol, Side, Price, Quantity, Fee, Realized Profit
        # 형식 2 (Order History): Time(UTC), Symbol, Side, Average Price, Executed Amount, Status
        column_mappings = {
            'time': ['Time(UTC)', 'Time', 'Date(UTC)', 'Date', 'time', 'date', 'Update Time'],
            'symbol': ['Symbol', 'symbol', 'Pair'],
            'side': ['Side', 'side', 'Type'],
            'price': ['Average Price', 'Price', 'price', 'Avg Fill Price'],
            'quantity': ['Executed Amount', 'Quantity', 'Qty', 'qty', 'quantity', 'Amount', 'Filled'],
            'fee': ['Fee', 'fee', 'Trading Fee', 'Commission'],
            'pnl': ['Realized Profit', 'Realized PNL', 'realized_pnl', 'PNL', 'Profit'],
            'status': ['Status', 'status'],
            'executed_quote': ['Executed Quote Amount']  # USDT 기준 금액
        }

        def get_column_value(row, field_names):
            """여러 가능한 컬럼명에서 값 찾기"""
            for name in field_names:
                if name in row and row[name]:
                    return row[name]
            return None

        # 거래를 포지션으로 그룹화
        from collections import defaultdict
        TIME_WINDOW = 5 * 60 * 1000  # 5분 (밀리초)
        positions_map = defaultdict(list)

        # 숫자 필드 파싱 함수
        def parse_number(val):
            if not val:
                return 0.0
            # 쉼표 제거 및 숫자 추출
            val = str(val).replace(',', '').replace(' ', '')
            # 0E-8 같은 과학적 표기법 처리
            try:
                return float(val)
            except ValueError:
                return 0.0

        parsed_trades = []
        skipped_count = 0
        for row in rows:
            try:
                # Status 체크 - FILLED만 처리 (Order History 형식)
                status = get_column_value(row, column_mappings['status'])
                if status and status.upper() != 'FILLED':
                    skipped_count += 1
                    continue

                # 시간 파싱
                time_str = get_column_value(row, column_mappings['time'])
                if not time_str:
                    continue

                # 다양한 시간 형식 지원
                trade_time = None
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
                    try:
                        trade_time = datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
                        break
                    except ValueError:
                        continue

                if not trade_time:
                    logger.warning(f"시간 파싱 실패: {time_str}")
                    continue

                # 심볼 정규화 (BTCUSDT, BTC/USDT, BTC-USDT -> BTCUSDT)
                symbol = get_column_value(row, column_mappings['symbol']) or ''
                symbol = symbol.replace('/', '').replace('-', '').upper()
                if not symbol:
                    continue

                # Side 파싱
                side_str = (get_column_value(row, column_mappings['side']) or '').lower()
                if 'buy' in side_str or 'long' in side_str:
                    side = 'buy'
                elif 'sell' in side_str or 'short' in side_str:
                    side = 'sell'
                else:
                    continue

                price = parse_number(get_column_value(row, column_mappings['price']))
                quantity = parse_number(get_column_value(row, column_mappings['quantity']))
                fee = parse_number(get_column_value(row, column_mappings['fee']))
                pnl = parse_number(get_column_value(row, column_mappings['pnl']))
                executed_quote = parse_number(get_column_value(row, column_mappings['executed_quote']))

                # 가격이 0이면 executed_quote / quantity로 계산
                if price <= 0 and executed_quote > 0 and quantity > 0:
                    price = executed_quote / quantity

                if price <= 0 or quantity <= 0:
                    continue

                timestamp_ms = int(trade_time.timestamp() * 1000)

                parsed_trades.append({
                    'timestamp': timestamp_ms,
                    'datetime': trade_time,
                    'symbol': symbol,
                    'side': side,
                    'price': price,
                    'quantity': quantity,
                    'cost': executed_quote if executed_quote > 0 else price * quantity,
                    'fee': abs(fee),
                    'pnl': pnl
                })

            except Exception as e:
                logger.warning(f"행 파싱 실패: {e}, row={row}")
                continue

        logger.info(f"CSV: FILLED {len(parsed_trades)}건, 스킵 {skipped_count}건")

        if not parsed_trades:
            raise HTTPException(status_code=400, detail="파싱 가능한 거래 데이터가 없습니다")

        logger.info(f"CSV 파싱 완료: {len(parsed_trades)}건 거래")

        # 시간순 정렬
        parsed_trades.sort(key=lambda x: x['timestamp'])

        # 거래를 포지션으로 그룹화 (5분 이내 같은 심볼+방향)
        for trade in parsed_trades:
            key = f"{trade['symbol']}_{trade['side']}"

            added = False
            for group in positions_map[key]:
                if abs(group['last_timestamp'] - trade['timestamp']) <= TIME_WINDOW:
                    group['trades'].append(trade)
                    group['last_timestamp'] = trade['timestamp']
                    added = True
                    break

            if not added:
                positions_map[key].append({
                    'trades': [trade],
                    'last_timestamp': trade['timestamp']
                })

        # 포지션 저장
        saved_count = 0
        skipped_count = 0
        total_count = 0

        for key, groups in positions_map.items():
            for group in groups:
                total_count += 1
                trades = group['trades']
                if not trades:
                    continue

                first_trade = trades[0]

                # 포지션 ID 생성 (CSV 업로드용)
                position_id = f"csv_{first_trade['symbol']}_{first_trade['timestamp']}"

                # 수량 가중 평균 가격, 총 수량, 총 수수료, 총 PnL 계산
                total_qty = sum(t['quantity'] for t in trades)
                total_cost = sum(t.get('cost', t['price'] * t['quantity']) for t in trades)
                avg_price = total_cost / total_qty if total_qty > 0 else 0
                total_fee = sum(t['fee'] for t in trades)
                total_pnl = sum(t['pnl'] for t in trades)

                position_data = {
                    'position_id': position_id,
                    'symbol': first_trade['symbol'],
                    'side': first_trade['side'],
                    'entry_price': avg_price,
                    'exit_price': None,
                    'quantity': total_qty,
                    'pnl': total_pnl,
                    'pnl_percent': (total_pnl / (avg_price * total_qty) * 100) if avg_price * total_qty > 0 else 0,
                    'commission': total_fee,
                    'status': 'closed',
                    'leverage': None,
                    'margin': None,
                    'open_time': first_trade['datetime'],
                    'close_time': trades[-1]['datetime'] if len(trades) > 1 else first_trade['datetime'],
                    'duration_minutes': None,
                    'source': 'csv'
                }

                try:
                    if positions_repo.save_position(position_data):
                        saved_count += 1
                    else:
                        skipped_count += 1
                except Exception as e:
                    logger.warning(f"포지션 저장 실패 ({position_id}): {e}")
                    skipped_count += 1

        return {
            "total": total_count,
            "saved": saved_count,
            "skipped": skipped_count,
            "parsed_trades": len(parsed_trades),
            "message": f"CSV에서 {saved_count}개 포지션 저장 완료"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CSV 업로드 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/daily-pnl")
async def get_positions_daily_pnl(
    symbol: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """일별 손익"""
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None
        daily_pnl = positions_repo.get_daily_pnl(symbol=symbol, days=days, start_date=start_dt, end_date=end_dt)
        return {"daily_pnl": daily_pnl, "count": len(daily_pnl)}
    except Exception as e:
        logger.error(f"일별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/time-analysis")
async def get_positions_time_analysis(
    symbol: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """포지션 기준 요일별/시간별 분석"""
    try:
        # 날짜 범위 설정
        if start_date:
            cutoff = datetime.fromisoformat(start_date)
        else:
            cutoff = datetime.now() - timedelta(days=days)

        end_dt = datetime.fromisoformat(end_date) if end_date else datetime.now()

        # 기본 WHERE 조건
        where_clause = "WHERE p.open_time >= %(cutoff)s AND p.open_time <= %(end_dt)s"
        params = {'cutoff': cutoff, 'end_dt': end_dt}

        if symbol:
            where_clause += " AND p.symbol = %(symbol)s"
            params['symbol'] = symbol

        # 요일별 분석 (PostgreSQL: EXTRACT(DOW FROM ...) returns 0=Sunday)
        day_query = f"""
            SELECT
                EXTRACT(DOW FROM p.open_time)::int as day_of_week,
                COUNT(*)::int as count,
                COUNT(CASE WHEN p.pnl > 0 THEN 1 END)::int as wins,
                COUNT(CASE WHEN p.pnl <= 0 THEN 1 END)::int as losses,
                COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent
            FROM tradebot_positions p
            {where_clause}
            GROUP BY EXTRACT(DOW FROM p.open_time)
            ORDER BY day_of_week
        """

        # 시간별 분석 (KST = UTC + 9)
        hour_query = f"""
            SELECT
                EXTRACT(HOUR FROM p.open_time + INTERVAL '9 hours')::int as hour,
                COUNT(*)::int as count,
                COUNT(CASE WHEN p.pnl > 0 THEN 1 END)::int as wins,
                COUNT(CASE WHEN p.pnl <= 0 THEN 1 END)::int as losses,
                COALESCE(SUM(p.pnl), 0)::float as total_pnl,
                COALESCE(AVG(p.pnl_percent), 0)::float as avg_pnl_percent
            FROM tradebot_positions p
            {where_clause}
            GROUP BY EXTRACT(HOUR FROM p.open_time + INTERVAL '9 hours')
            ORDER BY hour
        """

        logger.info(f"[positions/time-analysis] 쿼리 실행: days={days}, symbol={symbol}")

        day_df = pd.read_sql(day_query, db_engine, params=params)
        hour_df = pd.read_sql(hour_query, db_engine, params=params)

        logger.info(f"[positions/time-analysis] 결과: day={len(day_df)}행, hour={len(hour_df)}행")

        # NaN 처리
        day_df = day_df.fillna(0)
        hour_df = hour_df.fillna(0)

        return {
            "by_day": day_df.to_dict('records'),
            "by_hour": hour_df.to_dict('records')
        }

    except Exception as e:
        logger.error(f"포지션 시간 분석 조회 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/{position_id}")
async def get_position_detail(position_id: str):
    """포지션 상세"""
    try:
        position = positions_repo.get_position_by_id(position_id)
        if not position:
            raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다")
        return position
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"포지션 상세 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 사용자 액션 API (1단계: 진입/청산 기록)
# ============================================================

class EnterActionRequest(BaseModel):
    """진입 액션 요청"""
    signal_id: Optional[str] = None
    symbol: str
    side: str  # 'buy' or 'sell'
    price: Optional[float] = None
    quantity: Optional[float] = None
    reason: Optional[str] = None
    metadata: Optional[Dict] = None


class ExitActionRequest(BaseModel):
    """청산 액션 요청"""
    signal_id: Optional[str] = None
    position_id: Optional[str] = None
    symbol: str
    side: str  # 'buy' or 'sell'
    price: Optional[float] = None
    quantity: Optional[float] = None
    reason: Optional[str] = None
    metadata: Optional[Dict] = None


@app.post("/api/actions/enter")
async def record_enter_action(request: EnterActionRequest, user: dict = Depends(require_auth)):
    """진입 액션 기록"""
    if not user_actions_repo or not events_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        import uuid
        action_id = f"enter_{request.symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = datetime.now()

        # 사용자 액션 저장
        success = user_actions_repo.save_action(
            action_id=action_id,
            action_type='enter',
            symbol=request.symbol,
            side=request.side,
            price=request.price,
            quantity=request.quantity,
            signal_id=request.signal_id,
            reason=request.reason,
            metadata=request.metadata
        )

        if not success:
            raise HTTPException(status_code=500, detail="액션 저장 실패")

        # 이벤트 로그에도 저장
        events_repo.save_event(
            event_id=f"evt_{action_id}",
            event_type='user_action',
            timestamp=now,
            symbol=request.symbol,
            data={
                'action_id': action_id,
                'action_type': 'enter',
                'signal_id': request.signal_id,
                'side': request.side,
                'price': request.price,
                'quantity': request.quantity,
                'reason': request.reason,
                'metadata': request.metadata or {}
            }
        )

        logger.info(f"✅ 진입 액션 기록: {action_id} (신호: {request.signal_id})")
        return {"success": True, "action_id": action_id, "message": "진입 기록 완료"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"진입 액션 기록 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/actions/exit")
async def record_exit_action(request: ExitActionRequest, user: dict = Depends(require_auth)):
    """청산 액션 기록"""
    if not user_actions_repo or not events_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        import uuid
        action_id = f"exit_{request.symbol}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = datetime.now()

        # 사용자 액션 저장
        success = user_actions_repo.save_action(
            action_id=action_id,
            action_type='exit',
            symbol=request.symbol,
            side=request.side,
            price=request.price,
            quantity=request.quantity,
            signal_id=request.signal_id,
            reason=request.reason,
            metadata={**(request.metadata or {}), 'position_id': request.position_id}
        )

        if not success:
            raise HTTPException(status_code=500, detail="액션 저장 실패")

        # 이벤트 로그에도 저장
        events_repo.save_event(
            event_id=f"evt_{action_id}",
            event_type='user_action',
            timestamp=now,
            symbol=request.symbol,
            data={
                'action_id': action_id,
                'action_type': 'exit',
                'signal_id': request.signal_id,
                'position_id': request.position_id,
                'side': request.side,
                'price': request.price,
                'quantity': request.quantity,
                'reason': request.reason,
                'metadata': request.metadata or {}
            }
        )

        logger.info(f"✅ 청산 액션 기록: {action_id} (신호: {request.signal_id}, 포지션: {request.position_id})")
        return {"success": True, "action_id": action_id, "message": "청산 기록 완료"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"청산 액션 기록 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/actions/list")
async def get_actions_list(
    signal_id: Optional[str] = None,
    symbol: Optional[str] = None,
    action_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    user: dict = Depends(require_auth)
):
    """사용자 액션 목록 조회"""
    if not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        actions = user_actions_repo.get_actions(
            signal_id=signal_id,
            symbol=symbol,
            action_type=action_type,
            limit=limit,
            offset=offset
        )
        return {"actions": actions, "count": len(actions)}
    except Exception as e:
        logger.error(f"액션 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events/list")
async def get_events_list(
    event_type: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    limit: int = 1000,
    offset: int = 0,
    user: dict = Depends(require_auth)
):
    """이벤트 로그 조회"""
    if not events_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        events = events_repo.get_events(
            event_type=event_type,
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            offset=offset
        )
        return {"events": events, "count": len(events)}
    except Exception as e:
        logger.error(f"이벤트 목록 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 2단계: 신호-액션 매칭 API
# ============================================================

@app.get("/api/matching/signal/{signal_id}")
async def match_signal_to_action(
    signal_id: str,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """특정 신호에 대한 사용자 액션 매칭"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )
        result = matcher.match_signal_to_action(
            signal_id=signal_id,
            max_time_window_minutes=max_time_window_minutes
        )
        if not result:
            raise HTTPException(status_code=404, detail="신호를 찾을 수 없습니다")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"신호-액션 매칭 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/matching/dataset")
async def get_training_dataset(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """학습 데이터셋 조회 (신호 + 사용자 액션 병합)"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱 (YYYY-MM-DD 형식 지원)
        start_dt = None
        end_dt = None
        if start_date:
            try:
                # 날짜만 있는 경우 (YYYY-MM-DD) 시간 추가
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (start_date): {start_date}")
        if end_date:
            try:
                # 날짜만 있는 경우 (YYYY-MM-DD) 시간 추가 (하루 끝)
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (end_date): {end_date}")

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 매칭 수행
        matched_results = matcher.match_all_signals(
            symbol=symbol,
            start_date=start_dt,
            end_date=end_dt,
            max_time_window_minutes=max_time_window_minutes
        )

        # 학습 데이터셋 구축
        dataset = matcher.build_training_dataset(matched_results)

        # 실제 매칭된 항목 카운트 (matched=True인 것만)
        actual_matched_count = len([r for r in matched_results if r.get('matched', False)])

        # 데이터셋 조회 API에서는 학습하지 않음 (학습은 별도 API에서 수행)
        # 타이밍 학습 모델 학습은 /api/timing/train에서만 수행

        return {
            "dataset": dataset,
            "matched_count": actual_matched_count,
            "total_signals": len(matched_results),
            "dataset_count": len(dataset),
            "filters": {
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "max_time_window_minutes": max_time_window_minutes
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"학습 데이터셋 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"데이터셋 조회 실패: {str(e)}")


@app.post("/api/timing/train")
async def train_timing_model(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """타이밍 학습 모델 훈련"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱
        start_dt = None
        end_dt = None
        if start_date:
            try:
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (start_date): {start_date}")
        if end_date:
            try:
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (end_date): {end_date}")

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 매칭 수행
        matched_results = matcher.match_all_signals(
            symbol=symbol,
            start_date=start_dt,
            end_date=end_dt,
            max_time_window_minutes=max_time_window_minutes
        )

        # 학습 데이터셋 구축
        dataset = matcher.build_training_dataset(matched_results)

        # 타이밍 학습 모델 학습
        timing_learner = TimingLearner()
        training_result = timing_learner.train(dataset, min_samples=10)

        # 학습 상태 저장
        if training_result.get('trained', False):
            key = symbol or 'ALL'
            if key not in app_state.training_status:
                app_state.training_status[key] = {}
            app_state.training_status[key]['timing'] = {
                'trained': True,
                'trained_at': datetime.now(timezone.utc).isoformat(),
                'training_result': training_result,
                'dataset_size': len(dataset),
                'start_date': start_date,
                'end_date': end_date
            }

        return {
            "success": training_result.get('trained', False),
            "training_result": training_result,
            "dataset_size": len(dataset)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"타이밍 모델 학습 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"학습 실패: {str(e)}")


@app.get("/api/timing/predict")
async def predict_timing(
    signal_id: str,
    user: dict = Depends(require_auth)
):
    """신호에 대한 타이밍 예측"""
    if not signals_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 신호 조회
        signals = signals_repo.get_signals(signal_id=signal_id, limit=1)
        if not signals:
            raise HTTPException(status_code=404, detail="신호를 찾을 수 없습니다")

        signal = signals[0]

        # 시장 상태 수집
        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )
        market_state = matcher._get_market_state(signal)

        # 학습 데이터로 모델 학습 (최근 데이터 사용)
        matched_results = matcher.match_all_signals(
            symbol=signal.get('symbol'),
            start_date=datetime.now(timezone.utc) - timedelta(days=30),
            end_date=datetime.now(timezone.utc),
            max_time_window_minutes=60
        )
        dataset = matcher.build_training_dataset(matched_results)

        timing_learner = TimingLearner()
        training_result = timing_learner.train(dataset, min_samples=5)

        if not training_result.get('trained', False):
            return {
                "success": False,
                "message": "학습 데이터 부족",
                "recommendations": {
                    "enter_timing": {"recommended_minutes": 5.0, "confidence": 0.0, "source": "default"},
                    "exit_timing": {"recommended_minutes": 240.0, "confidence": 0.0, "source": "default"},
                    "should_enter": True,
                    "confidence": 0.0
                }
            }

        # 예측
        recommendations = timing_learner.get_recommendations(signal, market_state)

        return {
            "success": True,
            "signal_id": signal_id,
            "recommendations": recommendations,
            "market_state": market_state.get('price_action', {})
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"타이밍 예측 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"예측 실패: {str(e)}")


@app.post("/api/policy/train")
async def train_policy_model(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """정책 모델 훈련"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱
        start_dt = None
        end_dt = None
        if start_date:
            try:
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (start_date): {start_date}")
        if end_date:
            try:
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (end_date): {end_date}")

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 매칭 수행
        matched_results = matcher.match_all_signals(
            symbol=symbol,
            start_date=start_dt,
            end_date=end_dt,
            max_time_window_minutes=max_time_window_minutes
        )

        # 학습 데이터셋 구축
        dataset = matcher.build_training_dataset(matched_results)

        # 정책 모델 학습
        policy_recommender = PolicyRecommender()
        training_result = policy_recommender.train(dataset, min_samples=10)

        # 학습 상태 저장
        if training_result.get('trained', False):
            key = symbol or 'ALL'
            if key not in app_state.training_status:
                app_state.training_status[key] = {}
            app_state.training_status[key]['policy'] = {
                'trained': True,
                'trained_at': datetime.now(timezone.utc).isoformat(),
                'training_result': training_result,
                'dataset_size': len(dataset),
                'start_date': start_date,
                'end_date': end_date
            }

        return {
            "success": training_result.get('trained', False),
            "training_result": training_result,
            "dataset_size": len(dataset)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"정책 모델 학습 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"학습 실패: {str(e)}")


@app.get("/api/policy/recommend")
async def recommend_policy(
    signal_id: str,
    user: dict = Depends(require_auth)
):
    """신호에 대한 정책 추천"""
    if not signals_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 신호 조회
        signals = signals_repo.get_signals(signal_id=signal_id, limit=1)
        if not signals:
            raise HTTPException(status_code=404, detail="신호를 찾을 수 없습니다")

        signal = signals[0]

        # 시장 상태 수집
        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )
        market_state = matcher._get_market_state(signal)

        # 학습 데이터로 모델 학습 (최근 데이터 사용)
        matched_results = matcher.match_all_signals(
            symbol=signal.get('symbol'),
            start_date=datetime.now(timezone.utc) - timedelta(days=30),
            end_date=datetime.now(timezone.utc),
            max_time_window_minutes=60
        )
        dataset = matcher.build_training_dataset(matched_results)

        policy_recommender = PolicyRecommender()
        training_result = policy_recommender.train(dataset, min_samples=5)

        if not training_result.get('trained', False):
            return {
                "success": False,
                "message": "학습 데이터 부족",
                "policy": {
                    "should_enter": True,
                    "confidence": 0.0,
                    "enter_timing_minutes": 5.0,
                    "exit_timing_minutes": 240.0,
                    "price_adjustment_percent": 0.0,
                    "risk_reward": 2.0,
                    "recommendation": "데이터 부족",
                    "reasoning": "학습된 정책 패턴이 없습니다."
                }
            }

        # 정책 추천
        policy = policy_recommender.recommend_policy(signal, market_state)
        parameters = policy_recommender.recommend_parameters(market_state)

        return {
            "success": True,
            "signal_id": signal_id,
            "policy": policy,
            "parameters": parameters,
            "market_state": market_state.get('price_action', {})
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"정책 추천 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"추천 실패: {str(e)}")


@app.get("/api/policy/summary")
async def get_policy_summary(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """정책 모델 요약 정보"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱
        start_dt = None
        end_dt = None
        if start_date:
            try:
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                pass
        if end_date:
            try:
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                pass

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 매칭 수행
        matched_results = matcher.match_all_signals(
            symbol=symbol,
            start_date=start_dt,
            end_date=end_dt,
            max_time_window_minutes=max_time_window_minutes
        )

        # 학습 데이터셋 구축
        dataset = matcher.build_training_dataset(matched_results)

        # 정책 모델 학습
        policy_recommender = PolicyRecommender()
        training_result = policy_recommender.train(dataset, min_samples=10)

        if not training_result.get('trained', False):
            return {
                "success": False,
                "message": training_result.get('message', '학습 데이터 부족'),
                "summary": None
            }

        # 요약 정보
        summary = policy_recommender.get_policy_summary()

        return {
            "success": True,
            "summary": summary,
            "training_result": training_result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"정책 모델 요약 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/training/status")
async def get_training_status(
    user: dict = Depends(require_auth)
):
    """학습 완료 상태 조회 (모든 심볼)"""
    try:
        # 심볼별 학습 상태 반환
        status = {}
        for symbol_key, training_data in app_state.training_status.items():
            status[symbol_key] = {
                'timing_trained': 'timing' in training_data and training_data['timing'].get('trained', False),
                'policy_trained': 'policy' in training_data and training_data['policy'].get('trained', False),
                'timing_trained_at': training_data.get('timing', {}).get('trained_at'),
                'policy_trained_at': training_data.get('policy', {}).get('trained_at')
            }
        return {
            "success": True,
            "status": status
        }
    except Exception as e:
        logger.error(f"학습 상태 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/training/details/{symbol}")
async def get_training_details(
    symbol: str,
    user: dict = Depends(require_auth)
):
    """심볼별 학습 상세 정보 조회"""
    try:
        # 'ALL' 또는 특정 심볼 조회
        symbol_key = symbol if symbol in app_state.training_status else 'ALL'
        
        if symbol_key not in app_state.training_status:
            return {
                "success": False,
                "message": "학습 데이터가 없습니다",
                "details": None
            }
        
        training_data = app_state.training_status[symbol_key]
        details = {
            'symbol': symbol_key,
            'timing': training_data.get('timing'),
            'policy': training_data.get('policy')
        }
        
        return {
            "success": True,
            "details": details
        }
    except Exception as e:
        logger.error(f"학습 상세 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/matching/check-symbols")
async def check_trainable_symbols(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    min_samples: int = 10,
    user: dict = Depends(require_auth)
):
    """학습 가능한 심볼 확인 (충분한 매칭 데이터가 있는 심볼)"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱
        start_dt = None
        end_dt = None
        if start_date:
            try:
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (start_date): {start_date}")
        if end_date:
            try:
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (end_date): {end_date}")

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 모든 심볼 가져오기
        signals = signals_repo.get_signals(limit=10000)
        all_symbols = list(set([s.get('symbol') for s in signals if s.get('symbol')]))

        # 심볼별 매칭 데이터 개수 확인
        symbol_results = []
        for sym in sorted(all_symbols):
            try:
                matched_results = matcher.match_all_signals(
                    symbol=sym,
                    start_date=start_dt,
                    end_date=end_dt,
                    max_time_window_minutes=max_time_window_minutes
                )
                
                dataset = matcher.build_training_dataset(matched_results)
                matched_count = len([r for r in matched_results if r.get('matched', False)])
                
                # PnL 통계
                pnls = [d['output'].get('pnl') for d in dataset if d['output'].get('pnl') is not None]
                avg_pnl = sum(pnls) / len(pnls) if pnls else 0
                wins = len([p for p in pnls if p > 0])
                win_rate = (wins / len(pnls) * 100) if pnls else 0
                
                is_trainable = matched_count >= min_samples
                
                symbol_results.append({
                    'symbol': sym,
                    'total_signals': len(matched_results),
                    'matched_count': matched_count,
                    'dataset_size': len(dataset),
                    'avg_pnl': round(avg_pnl, 2),
                    'win_rate': round(win_rate, 2),
                    'is_trainable': is_trainable,
                    'meets_min_samples': matched_count >= min_samples
                })
            except Exception as e:
                logger.warning(f"심볼 {sym} 확인 실패: {e}")
                continue

        # 학습 가능한 심볼과 불가능한 심볼 분리
        trainable = [s for s in symbol_results if s['is_trainable']]
        not_trainable = [s for s in symbol_results if not s['is_trainable']]

        return {
            "success": True,
            "min_samples": min_samples,
            "trainable_symbols": trainable,
            "not_trainable_symbols": not_trainable,
            "total_checked": len(symbol_results),
            "trainable_count": len(trainable),
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "max_time_window_minutes": max_time_window_minutes
            }
        }
    except Exception as e:
        logger.error(f"학습 가능 심볼 확인 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"확인 실패: {str(e)}")


# ============================================================
# Paper Trading API
# ============================================================

@app.post("/api/paper/start")
async def start_paper_trading(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    """Paper Trading 시작"""
    try:
        if app_state.paper_trading_engine and app_state.paper_trading_engine.is_running:
            return {
                "success": False,
                "message": "Paper Trading이 이미 실행 중입니다"
            }

        # 요청 파라미터 가져오기
        initial_capital = request.get('initial_capital', 10000.0)
        require_approval = request.get('require_approval', False)
        symbol_filter = request.get('symbol')  # 선택된 심볼 (None이면 전체)
        strategy_filter = request.get('strategy')  # 선택된 전략 (None이면 전체)

        # PaperBroker 생성
        broker_config = {
            'initial_capital': initial_capital,
            'commission_rate': 0.001,  # 0.1%
            'slippage_pct': 0.05,  # 0.05%
            'max_positions': 5,
            'max_daily_loss_pct': 0.02  # 2%
        }
        broker = PaperBroker(broker_config)

        # Signal Matcher 생성 (시장 상태 조회용)
        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # Paper Trading Engine 생성 (필터 추가)
        engine = PaperTradingEngine(
            broker=broker,
            signals_repo=signals_repo,
            events_repo=events_repo,
            positions_repo=positions_repo,
            timing_learner=TimingLearner(),
            policy_recommender=PolicyRecommender(),
            signal_matcher=matcher,
            require_approval=bool(require_approval),
            on_position_update=broadcast_paper_trading_update,  # 포지션 업데이트 시 WebSocket 브로드캐스트
            symbol_filter=symbol_filter,  # 심볼 필터
            strategy_filter=strategy_filter  # 전략 필터
        )
        
        # user_actions_repo 연결 (수동 진입 기록용)
        if hasattr(engine, 'user_actions_repo'):
            engine.user_actions_repo = user_actions_repo

        engine.start()
        app_state.paper_trading_engine = engine

        filter_info = []
        if symbol_filter:
            filter_info.append(f"심볼={symbol_filter}")
        if strategy_filter:
            filter_info.append(f"전략={strategy_filter}")
        filter_str = f" ({', '.join(filter_info)})" if filter_info else ""
        logger.info(f"🚀 Paper Trading 시작: 자본=${initial_capital:.2f}{filter_str}")

        return {
            "success": True,
            "message": "Paper Trading이 시작되었습니다",
            "initial_capital": initial_capital,
            "symbol_filter": symbol_filter,
            "strategy_filter": strategy_filter
        }
    except Exception as e:
        logger.error(f"Paper Trading 시작 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"시작 실패: {str(e)}")


@app.post("/api/paper/stop")
async def stop_paper_trading(
    user: dict = Depends(require_auth)
):
    """Paper Trading 중지"""
    try:
        if not app_state.paper_trading_engine:
            return {
                "success": False,
                "message": "Paper Trading이 실행 중이 아닙니다"
            }

        app_state.paper_trading_engine.stop()
        app_state.paper_trading_engine = None

        logger.info("⏹️ Paper Trading 중지")

        return {
            "success": True,
            "message": "Paper Trading이 중지되었습니다"
        }
    except Exception as e:
        logger.error(f"Paper Trading 중지 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"중지 실패: {str(e)}")


@app.get("/api/paper/available-symbols")
async def get_available_symbols(
    user: dict = Depends(require_auth)
):
    """사용 가능한 심볼 목록 조회"""
    try:
        if not signals_repo:
            raise HTTPException(status_code=503, detail="신호 Repository가 초기화되지 않았습니다")
        symbols = signals_repo.get_unique_symbols(limit=100)
        return {"symbols": symbols}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"심볼 목록 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/paper/available-strategies")
async def get_available_strategies(
    user: dict = Depends(require_auth)
):
    """사용 가능한 전략 목록 조회"""
    try:
        if not signals_repo:
            raise HTTPException(status_code=503, detail="신호 Repository가 초기화되지 않았습니다")
        strategies = signals_repo.get_unique_strategies(limit=50)
        return {"strategies": strategies}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"전략 목록 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/paper/status")
async def get_paper_trading_status(
    user: dict = Depends(require_auth)
):
    """Paper Trading 상태 조회"""
    try:
        if not app_state.paper_trading_engine:
            return {
                "success": True,
                "running": False,
                "message": "Paper Trading이 실행 중이 아닙니다"
            }

        engine = app_state.paper_trading_engine
        stats = engine.get_stats()

        # 오픈 포지션 목록
        open_positions = engine.broker.get_open_positions()
        positions_data = []
        for pos in open_positions:
            # 현재 가격 가져오기
            current_price = engine.current_prices.get(pos.symbol, pos.entry_price)
            
            positions_data.append({
                'position_id': pos.position_id,
                'symbol': pos.symbol,
                'side': pos.side.value,
                'entry_price': pos.entry_price,
                'quantity': pos.quantity,
                'current_price': current_price,
                'pnl': pos.pnl,
                'pnl_percent': pos.pnl_percent,
                'stop_loss': pos.stop_loss,
                'take_profit': pos.take_profit,
                'opened_at': pos.opened_at.isoformat() if pos.opened_at else None
            })

        return {
            "success": True,
            "running": engine.is_running,
            "stats": stats,
            "open_positions": positions_data,
            "rules_count": len(engine.rules)
        }
    except Exception as e:
        logger.error(f"Paper Trading 상태 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.post("/api/paper/process-signal")
async def process_paper_signal(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    """신호 처리 (Paper Trading)"""
    try:
        if not app_state.paper_trading_engine or not app_state.paper_trading_engine.is_running:
            raise HTTPException(status_code=400, detail="Paper Trading이 실행 중이 아닙니다")

        engine = app_state.paper_trading_engine
        
        # 요청에서 신호 데이터 가져오기
        signal = request.get('signal')
        signal_id = request.get('signal_id')
        is_manual = request.get('is_manual', False)
        manual_entry_price = request.get('manual_entry_price')
        
        if signal:
            # 직접 신호 데이터 사용
            signal_dict = {
                'signal_id': signal_id or f"{signal.get('symbol')}_{signal.get('timeframe')}_{signal.get('signal_type')}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                'symbol': signal.get('symbol'),
                'timeframe': signal.get('timeframe'),
                'signal_type': signal.get('signal_type'),
                'entry_price': signal.get('entry_price'),
                'stop_loss': signal.get('stop_loss'),
                'take_profit_1': signal.get('take_profit_1'),
                'confidence': signal.get('confidence'),
                'risk_reward': signal.get('risk_reward'),
                'reasons': signal.get('reasons', []),
                'created_at': signal.get('created_at')
            }
        elif signal_id:
            # 신호 ID로 조회
            signals = signals_repo.get_signals(signal_id=signal_id, limit=1)
            if not signals:
                raise HTTPException(status_code=404, detail="신호를 찾을 수 없습니다")
            signal_dict = signals[0]
        else:
            raise HTTPException(status_code=400, detail="signal 또는 signal_id가 필요합니다")

        # 신호 처리 (자동/수동 구분)
        result = await engine.process_signal(signal_dict, is_manual=is_manual, manual_entry_price=manual_entry_price)

        return {
            "success": result.get('success', False),
            "result": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"신호 처리 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"처리 실패: {str(e)}")


@app.post("/api/paper/update-price")
async def update_paper_price(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    """가격 업데이트 (WebSocket에서 호출)"""
    try:
        if not app_state.paper_trading_engine:
            return {"success": False, "message": "Paper Trading이 실행 중이 아닙니다"}

        symbol = request.get('symbol')
        price = request.get('price')
        
        if not symbol or price is None:
            raise HTTPException(status_code=400, detail="symbol과 price가 필요합니다")

        app_state.paper_trading_engine.update_price(symbol, float(price))

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"가격 업데이트 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"업데이트 실패: {str(e)}")


@app.post("/api/paper/close-position")
async def close_paper_position(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    """포지션 수동 청산"""
    try:
        if not app_state.paper_trading_engine or not app_state.paper_trading_engine.is_running:
            raise HTTPException(status_code=400, detail="Paper Trading이 실행 중이 아닙니다")

        position_id = request.get('position_id')
        exit_price = request.get('exit_price')  # 선택사항, None이면 현재 가격 사용

        if not position_id:
            raise HTTPException(status_code=400, detail="position_id가 필요합니다")

        engine = app_state.paper_trading_engine
        
        # 현재 가격 가져오기 (exit_price가 없으면)
        if exit_price is None:
            position = engine.broker.get_position(position_id)
            if position:
                exit_price = engine.current_prices.get(position.symbol, position.entry_price)
            else:
                raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다")

        # 포지션 청산
        success = engine.broker.close_position(position_id, float(exit_price))
        
        if not success:
            raise HTTPException(status_code=400, detail="포지션 청산 실패")

        # 포지션 업데이트 콜백 호출
        if engine.on_position_update:
            try:
                await engine.on_position_update()
            except Exception as e:
                logger.warning(f"⚠️ 포지션 업데이트 콜백 실패: {e}")

        logger.info(f"✅ 포지션 청산 완료: {position_id} @ ${exit_price:.8f}")

        return {
            "success": True,
            "message": "포지션이 청산되었습니다",
            "position_id": position_id,
            "exit_price": exit_price
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"포지션 청산 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"청산 실패: {str(e)}")


@app.post("/api/paper/set-auto-entry")
async def set_auto_entry(
    request: Dict = Body(...),
    user: dict = Depends(require_auth)
):
    """자동 진입 설정"""
    try:
        if not app_state.paper_trading_engine:
            return {"success": False, "message": "Paper Trading이 실행 중이 아닙니다"}

        auto_entry = request.get('auto_entry', False)
        # auto_entry가 True면 require_approval는 False (자동 진입)
        app_state.paper_trading_engine.require_approval = not bool(auto_entry)

        logger.info(f"자동 진입 설정: {auto_entry} (require_approval: {app_state.paper_trading_engine.require_approval})")

        return {
            "success": True,
            "auto_entry": auto_entry
        }
    except Exception as e:
        logger.error(f"자동 진입 설정 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"설정 실패: {str(e)}")


@app.get("/api/matching/stats")
async def get_matching_stats(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_time_window_minutes: int = 60,
    user: dict = Depends(require_auth)
):
    """매칭 통계 조회"""
    if not signals_repo or not user_actions_repo:
        raise HTTPException(status_code=500, detail="Repository 초기화되지 않음")

    try:
        # 날짜 파싱 (YYYY-MM-DD 형식 지원)
        start_dt = None
        end_dt = None
        if start_date:
            try:
                # 날짜만 있는 경우 (YYYY-MM-DD) 시간 추가
                if len(start_date) == 10:
                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00')
                else:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (start_date): {start_date}")
        if end_date:
            try:
                # 날짜만 있는 경우 (YYYY-MM-DD) 시간 추가 (하루 끝)
                if len(end_date) == 10:
                    end_dt = datetime.fromisoformat(end_date + 'T23:59:59')
                else:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                logger.warning(f"날짜 파싱 실패 (end_date): {end_date}")

        matcher = SignalActionMatcher(
            signals_repo=signals_repo,
            user_actions_repo=user_actions_repo,
            positions_repo=positions_repo,
            db_engine=db_engine
        )

        # 매칭 수행
        matched_results = matcher.match_all_signals(
            symbol=symbol,
            start_date=start_dt,
            end_date=end_dt,
            max_time_window_minutes=max_time_window_minutes
        )

        # 학습 데이터셋 구축
        dataset = matcher.build_training_dataset(matched_results)

        # 통계 계산
        total_signals = len(matched_results)
        matched_count = len([r for r in matched_results if r.get('matched')])
        match_rate = (matched_count / total_signals * 100) if total_signals > 0 else 0

        # PnL 통계
        pnls = [d['output'].get('pnl') for d in dataset if d['output'].get('pnl') is not None]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        wins = len([p for p in pnls if p > 0])
        win_rate = (wins / len(pnls) * 100) if pnls else 0

        # 심볼별 통계
        symbol_stats = {}
        for item in dataset:
            sym = item['input']['signal'].get('symbol', 'Unknown')
            if sym not in symbol_stats:
                symbol_stats[sym] = {'count': 0, 'matched': 0, 'pnl_sum': 0, 'wins': 0}
            symbol_stats[sym]['count'] += 1
            if item['output'].get('enter_timing'):
                symbol_stats[sym]['matched'] += 1
            pnl = item['output'].get('pnl')
            if pnl is not None:
                symbol_stats[sym]['pnl_sum'] += pnl
                if pnl > 0:
                    symbol_stats[sym]['wins'] += 1

        return {
            "total_signals": total_signals,
            "matched_count": matched_count,
            "match_rate": round(match_rate, 2),
            "dataset_size": len(dataset),
            "avg_pnl": round(avg_pnl, 2),
            "win_rate": round(win_rate, 2),
            "total_trades": len(pnls),
            "symbol_stats": symbol_stats
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"매칭 통계 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"통계 조회 실패: {str(e)}")


# ============================================================
# 차트 데이터 API
# ============================================================

@app.post("/api/fill-gaps/{symbol}")
async def fill_gaps(symbol: str, timeframe: str = "15m", days: int = 30):
    """캔들 데이터의 갭을 찾아서 채움"""
    import httpx
    from datetime import timezone

    try:
        logger.info(f"📊 갭 채우기 시작: {symbol} {timeframe}")

        # 날짜 범위 설정
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        # DB 연결 정보
        db_config = {
            'user': config.get('db.user'),
            'password': config.get('db.password'),
            'host': config.get('db.host'),
            'port': config.get('db.port'),
            'name': config.get('db.name')
        }

        # 기존 캔들 시간 조회
        query = """
            SELECT open_time
            FROM candles
            WHERE symbol = %s AND tf = %s
              AND open_time >= %s AND open_time <= %s
            ORDER BY open_time
        """
        df = pd.read_sql(query, db_engine, params=(symbol, timeframe, start_date, end_date))
        existing_times = df['open_time'].tolist()

        # 타임프레임을 분으로 변환
        tf_map = {
            '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '2h': 120, '4h': 240, '6h': 360, '8h': 480,
            '12h': 720, '1d': 1440, '3d': 4320, '1w': 10080
        }
        tf_minutes = tf_map.get(timeframe, 15)
        expected_interval = timedelta(minutes=tf_minutes)

        # 갭 찾기
        gaps = []
        if not existing_times:
            gaps.append((start_date, end_date))
        else:
            # 시작 부분 갭
            first_time = existing_times[0]
            if hasattr(first_time, 'tzinfo') and first_time.tzinfo is None:
                first_time = first_time.replace(tzinfo=timezone.utc)
            if first_time > start_date + expected_interval:
                gaps.append((start_date, first_time - expected_interval))

            # 중간 갭
            for i in range(1, len(existing_times)):
                prev_time = existing_times[i-1]
                curr_time = existing_times[i]

                if hasattr(prev_time, 'tzinfo') and prev_time.tzinfo is None:
                    prev_time = prev_time.replace(tzinfo=timezone.utc)
                if hasattr(curr_time, 'tzinfo') and curr_time.tzinfo is None:
                    curr_time = curr_time.replace(tzinfo=timezone.utc)

                expected_next = prev_time + expected_interval
                # 1.5배 간격 이상이면 갭으로 인식 (15분 봉이면 22.5분 이상)
                gap_threshold = timedelta(minutes=tf_minutes * 0.5)
                if curr_time > expected_next + gap_threshold:
                    gaps.append((expected_next, curr_time - expected_interval))

            # 끝 부분 갭
            last_time = existing_times[-1]
            if hasattr(last_time, 'tzinfo') and last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            if last_time < end_date - expected_interval:
                gaps.append((last_time + expected_interval, end_date))

        if not gaps:
            logger.info(f"✅ {symbol} 갭 없음")
            return {
                "success": True,
                "symbol": symbol,
                "timeframe": timeframe,
                "message": "갭이 없습니다. 데이터가 완전합니다.",
                "gaps_found": 0,
                "candles_inserted": 0
            }

        logger.info(f"📊 {symbol} 발견된 갭: {len(gaps)}개")

        # ccxt로 갭 채우기
        import ccxt
        ex = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        ex.load_markets()

        # ccxt 심볼 변환
        ccxt_symbol = symbol
        if '/' not in symbol and symbol.endswith('USDT'):
            base = symbol[:-4]
            ccxt_symbol = f"{base}/USDT:USDT"

        total_inserted = 0

        for gap_start, gap_end in gaps:
            current_start = gap_start

            while current_start < gap_end:
                since_ms = int(current_start.timestamp() * 1000)

                try:
                    ohlcv = ex.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, since=since_ms, limit=1000)
                except Exception as e:
                    logger.error(f"데이터 가져오기 실패: {e}")
                    break

                if not ohlcv:
                    break

                # DB에 저장
                from sqlalchemy import text
                with db_engine.connect() as conn:
                    for candle in ohlcv:
                        candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)

                        if candle_time > gap_end:
                            continue

                        # 존재 여부 확인
                        check_query = text("""
                            SELECT 1 FROM candles
                            WHERE symbol = :symbol AND tf = :tf AND open_time = :open_time
                            LIMIT 1
                        """)
                        exists = conn.execute(check_query, {
                            "symbol": symbol,
                            "tf": timeframe,
                            "open_time": candle_time
                        }).fetchone()

                        if not exists:
                            insert_query = text("""
                                INSERT INTO candles (exchange, market, symbol, tf, open_time, open, high, low, close, volume)
                                VALUES (:exchange, :market, :symbol, :tf, :open_time, :open, :high, :low, :close, :volume)
                            """)
                            conn.execute(insert_query, {
                                "exchange": "binance",
                                "market": "usdtm",
                                "symbol": symbol,
                                "tf": timeframe,
                                "open_time": candle_time,
                                "open": candle[1],
                                "high": candle[2],
                                "low": candle[3],
                                "close": candle[4],
                                "volume": candle[5]
                            })
                            total_inserted += 1

                    conn.commit()

                # 다음 구간
                if ohlcv:
                    last_candle_time = datetime.fromtimestamp(ohlcv[-1][0] / 1000, tz=timezone.utc)
                    current_start = last_candle_time + timedelta(minutes=tf_minutes)
                else:
                    break

                import time
                time.sleep(0.2)

        logger.info(f"✅ {symbol} 갭 채우기 완료: {total_inserted}개 캔들 추가")

        return {
            "success": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "gaps_found": len(gaps),
            "candles_inserted": total_inserted,
            "message": f"{len(gaps)}개의 갭에서 {total_inserted}개의 캔들을 추가했습니다."
        }

    except Exception as e:
        logger.error(f"갭 채우기 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/check-gaps/{symbol}")
async def check_gaps(symbol: str, timeframe: str = "15m", days: int = 7):
    """캔들 데이터 갭 확인 (dry-run)"""
    from datetime import timezone

    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        # 기존 캔들 시간 조회
        query = """
            SELECT open_time
            FROM candles
            WHERE symbol = %s AND tf = %s
              AND open_time >= %s AND open_time <= %s
            ORDER BY open_time
        """
        df = pd.read_sql(query, db_engine, params=(symbol, timeframe, start_date, end_date))
        existing_times = df['open_time'].tolist()

        if not existing_times:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "message": "데이터 없음",
                "total_candles": 0,
                "gaps": []
            }

        # 타임프레임을 분으로 변환
        tf_map = {
            '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '2h': 120, '4h': 240, '6h': 360, '8h': 480,
            '12h': 720, '1d': 1440, '3d': 4320, '1w': 10080
        }
        tf_minutes = tf_map.get(timeframe, 15)
        expected_interval = timedelta(minutes=tf_minutes)

        # 갭 찾기
        gaps = []
        for i in range(1, len(existing_times)):
            prev_time = existing_times[i-1]
            curr_time = existing_times[i]

            # timezone 처리
            if hasattr(prev_time, 'tzinfo') and prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
            if hasattr(curr_time, 'tzinfo') and curr_time.tzinfo is None:
                curr_time = curr_time.replace(tzinfo=timezone.utc)

            expected_next = prev_time + expected_interval
            actual_diff = (curr_time - prev_time).total_seconds() / 60

            # 예상 간격보다 1.5배 이상 크면 갭으로 판단
            if actual_diff > tf_minutes * 1.5:
                missing_candles = int(actual_diff / tf_minutes) - 1
                gaps.append({
                    "gap_start": prev_time.isoformat(),
                    "gap_end": curr_time.isoformat(),
                    "missing_minutes": int(actual_diff - tf_minutes),
                    "missing_candles": missing_candles
                })

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "period": f"{start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}",
            "total_candles": len(existing_times),
            "first_candle": existing_times[0].isoformat() if existing_times else None,
            "last_candle": existing_times[-1].isoformat() if existing_times else None,
            "gaps_found": len(gaps),
            "gaps": gaps[:20]  # 최대 20개만 표시
        }

    except Exception as e:
        logger.error(f"갭 확인 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart-data/{symbol}")
async def get_chart_data(symbol: str, timeframe: str = "15m", limit: int = 100):
    """차트 데이터"""
    try:
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s
            ORDER BY open_time DESC
            LIMIT %s
        """
        df = pd.read_sql(query, db_engine, params=(symbol, timeframe, limit))

        if len(df) == 0:
            raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다")

        df = df.sort_values('open_time')
        candles = []
        for _, row in df.iterrows():
            candles.append({
                'time': int(row['open_time'].timestamp()),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume'])
            })

        return {"symbol": symbol, "timeframe": timeframe, "candles": candles, "count": len(candles)}
    except Exception as e:
        logger.error(f"차트 데이터 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sync-candles/{symbol}")
async def sync_candles_from_binance(symbol: str, timeframe: str = "15m", limit: int = 500):
    """바이낸스에서 캔들 데이터를 가져와 DB에 저장 (기본 500개)"""
    try:
        import ccxt
        from datetime import datetime, timezone
        from sqlalchemy import text

        # ccxt 인스턴스 생성
        ex = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        # ccxt 심볼 변환
        ccxt_symbol = symbol
        if symbol.endswith('USDT'):
            base = symbol[:-4]
            ccxt_symbol = f"{base}/USDT:USDT"

        # 바이낸스에서 데이터 가져오기
        ohlcv = ex.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=limit)

        if not ohlcv:
            return {"success": False, "message": "바이낸스에서 데이터를 가져올 수 없습니다", "inserted": 0}

        # DB에 저장 (DELETE + INSERT 방식으로 연속 데이터 보장)
        # 가져온 기간의 기존 데이터 삭제 후 새로 삽입
        with db_engine.connect() as conn:
            # 가져온 데이터의 시간 범위 계산
            min_time = datetime.fromtimestamp(ohlcv[0][0] / 1000, tz=timezone.utc)
            max_time = datetime.fromtimestamp(ohlcv[-1][0] / 1000, tz=timezone.utc)

            # 해당 범위의 기존 데이터 삭제
            delete_query = text("""
                DELETE FROM candles
                WHERE symbol = :symbol AND tf = :tf
                AND open_time >= :min_time AND open_time <= :max_time
            """)
            conn.execute(delete_query, {
                'symbol': symbol,
                'tf': timeframe,
                'min_time': min_time,
                'max_time': max_time
            })

            # 새 데이터 삽입
            insert_query = text("""
                INSERT INTO candles (exchange, market, symbol, tf, open_time, open, high, low, close, volume)
                VALUES ('binance', 'futures', :symbol, :tf, :open_time, :open, :high, :low, :close, :volume)
            """)

            for candle in ohlcv:
                candle_time = datetime.fromtimestamp(candle[0] / 1000, tz=timezone.utc)
                conn.execute(insert_query, {
                    'symbol': symbol,
                    'tf': timeframe,
                    'open_time': candle_time,
                    'open': candle[1],
                    'high': candle[2],
                    'low': candle[3],
                    'close': candle[4],
                    'volume': candle[5]
                })

            conn.commit()

        logger.info(f"✅ {symbol} {timeframe} 캔들 {len(ohlcv)}개 저장 완료")
        return {"success": True, "symbol": symbol, "timeframe": timeframe, "fetched": len(ohlcv), "saved": len(ohlcv)}

    except Exception as e:
        logger.error(f"바이낸스 캔들 동기화 실패: {e}")
        return {"success": False, "message": str(e), "inserted": 0}


# ============================================================
# 분석 API (분석 서버로 프록시)
# ============================================================

ANALYZER_SERVER_URL = "http://localhost:8887"

@app.get("/api/analyze/{symbol}")
async def analyze_symbol(
    symbol: str,
    timeframe: str = "15m",
    strategies: Optional[str] = None,
    include_rejected: bool = False,
    save_to_db: bool = False
):
    """심볼 분석 - 분석 서버로 요청 전달"""
    import httpx

    try:
        params = {
            "timeframe": timeframe,
            "include_rejected": include_rejected,
            "save_to_db": save_to_db
        }
        if strategies:
            params["strategies"] = strategies

        logger.info(f"📡 분석 서버로 요청: {ANALYZER_SERVER_URL}/api/analyze/{symbol} params={params}")

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                f"{ANALYZER_SERVER_URL}/api/analyze/{symbol}",
                params=params
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ 분석 서버 응답: {len(result.get('signals', []))}개 신호")
                # 신호를 app_state에 저장하고 WebSocket으로 브로드캐스트
                await process_and_broadcast_signals(result.get('signals', []))
                return result
            else:
                logger.error(f"❌ 분석 서버 오류 응답: {response.status_code}")
                raise HTTPException(status_code=response.status_code, detail=response.text)

    except httpx.ConnectError:
        # 분석 서버 연결 실패 시 로컬에서 직접 분석 (폴백)
        logger.warning("⚠️ 분석 서버 연결 실패, 로컬 분석 수행")
        result = await analyze_symbol_local(symbol, timeframe, strategies, include_rejected)
        # 로컬 분석 결과도 저장 및 브로드캐스트
        await process_and_broadcast_signals(result.get('signals', []))
        return result
    except Exception as e:
        logger.error(f"분석 요청 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def analyze_symbol_local(
    symbol: str,
    timeframe: str = "15m",
    strategies: Optional[str] = None,
    include_rejected: bool = False
):
    """로컬 분석 (폴백용)"""
    try:
        if not strategy_manager:
            raise HTTPException(status_code=500, detail="전략 매니저가 초기화되지 않았습니다")

        # DB에서 캔들 데이터 로드
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s
            ORDER BY open_time DESC
            LIMIT 500
        """
        df = pd.read_sql(query, db_engine, params=(symbol, timeframe))

        if len(df) < 50:
            return {"symbol": symbol, "signals": [], "message": "데이터 부족 (최소 50개 캔들 필요)"}

        df = df.sort_values('open_time').reset_index(drop=True)

        # 전략 필터링
        strategy_list = None
        if strategies:
            strategy_list = [s.strip() for s in strategies.split(',')]
            logger.info(f"📋 {symbol} 전략 필터: {strategy_list}")

        # 분석 실행 (전략 필터 전달)
        results = strategy_manager.analyze_all(
            symbol, timeframe, df,
            strategy_filter=strategy_list  # 지정된 전략만 실행
        )

        # 결과 변환
        signals_data = []
        for strategy_name, signals in results.items():

            for signal in signals:
                metadata = signal.metadata if signal.metadata else {}
                ai_analysis = metadata.get('ai_analysis', {})
                ai_decision = ai_analysis.get('decision', 'none')

                if not include_rejected and ai_decision == 'reject':
                    continue

                signal_dict = {
                    "strategy": signal.strategy_name,
                    "symbol": signal.symbol,
                    "timeframe": signal.timeframe,
                    "signal_type": signal.signal_type,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit_1": signal.take_profit_1,
                    "take_profit_2": getattr(signal, 'take_profit_2', None),
                    "confidence": signal.confidence,
                    "risk_reward": signal.risk_reward,
                    "reasons": signal.reasons,
                    "created_at": signal.created_at.isoformat() if signal.created_at else None,
                    "ai_analysis": {
                        "decision": ai_decision,
                        "confidence": ai_analysis.get('confidence'),
                        "reasoning": ai_analysis.get('reasoning'),
                        "risk_assessment": ai_analysis.get('risk_assessment'),
                        "market_context": ai_analysis.get('market_context')
                    } if ai_analysis else None
                }
                signals_data.append(signal_dict)

        logger.info(f"📊 {symbol} 로컬 분석 완료: {len(signals_data)}개 신호")

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "signals": signals_data,
            "count": len(signals_data)
        }

    except Exception as e:
        logger.error(f"로컬 분석 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 거래 API (주문 서버로 전달)
# ============================================================

class AIAnalysisRequest(BaseModel):
    symbol: str
    timeframe: str = "15m"
    lookback: int = 100
    custom_prompt: str = None  # 사용자 정의 프롬프트 (선택)


class PromptSaveRequest(BaseModel):
    content: str


# 프롬프트 파일 경로
PROMPT_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ai', 'prompts', 'market_analysis.txt')


@app.get("/api/ai-prompt")
async def get_ai_prompt():
    """AI 분석 프롬프트 파일 읽기"""
    try:
        if os.path.exists(PROMPT_FILE_PATH):
            with open(PROMPT_FILE_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
            return {"success": True, "content": content}
        else:
            return {"success": False, "error": "프롬프트 파일이 없습니다", "content": ""}
    except Exception as e:
        logger.error(f"프롬프트 읽기 실패: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/ai-prompt")
async def save_ai_prompt(request: PromptSaveRequest):
    """AI 분석 프롬프트 파일 저장"""
    try:
        # 디렉토리 확인/생성
        os.makedirs(os.path.dirname(PROMPT_FILE_PATH), exist_ok=True)

        with open(PROMPT_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(request.content)

        logger.info(f"✅ 프롬프트 저장 완료: {PROMPT_FILE_PATH}")
        return {"success": True, "message": "프롬프트가 저장되었습니다"}
    except Exception as e:
        logger.error(f"프롬프트 저장 실패: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/ai-analysis")
async def run_ai_analysis(request: AIAnalysisRequest):
    """
    AI 시장 분석
    - DB에서 캔들 데이터 확인
    - 누락된 캔들은 바이낸스에서 가져옴
    - OLLAMA AI로 시장 분석 수행
    """
    import requests as req

    symbol = request.symbol.upper()
    timeframe = request.timeframe
    lookback = request.lookback

    logger.info(f"🤖 AI 분석 요청: {symbol} {timeframe} lookback={lookback}")

    try:
        # 1. DB에서 캔들 데이터 로드
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s
            ORDER BY open_time DESC
            LIMIT %s
        """
        df = pd.read_sql(query, db_engine, params=(symbol, timeframe, lookback))

        candles_from_db = len(df)
        candles_fetched = 0

        if len(df) > 0:
            logger.info(f"📊 DB 캔들: {len(df)}개, 최근가: {float(df.iloc[0]['close']):.8f}")

        # 2. 항상 바이낸스에서 최신 데이터 가져오기 (DB 데이터가 오래되었을 수 있음)
        # DB 데이터가 충분해도 바이낸스에서 최신 데이터 확인
        fetch_from_binance = True
        if fetch_from_binance:
            logger.info(f"📊 DB 캔들: {len(df)}개, 바이낸스에서 추가 fetch 필요")

            try:
                if exchange_ccxt:
                    # 바이낸스에서 캔들 가져오기
                    ohlcv = exchange_ccxt.fetch_ohlcv(
                        symbol,
                        timeframe=timeframe,
                        limit=lookback
                    )

                    if ohlcv:
                        fetched_df = pd.DataFrame(
                            ohlcv,
                            columns=['open_time', 'open', 'high', 'low', 'close', 'volume']
                        )
                        # 바이낸스 데이터: UTC, tz-naive로 변환
                        fetched_df['open_time'] = pd.to_datetime(fetched_df['open_time'], unit='ms', utc=True).dt.tz_localize(None)

                        # DB에 저장 (UPSERT)
                        try:
                            from sqlalchemy import text
                            with db_engine.begin() as conn:
                                insert_count = 0
                                for _, row in fetched_df.iterrows():
                                    conn.execute(text("""
                                        INSERT INTO candles (exchange, symbol, market, tf, open_time, open, high, low, close, volume)
                                        VALUES (:exchange, :symbol, :market, :tf, :open_time, :open, :high, :low, :close, :volume)
                                        ON CONFLICT (exchange, symbol, market, tf, open_time) DO UPDATE SET
                                            open = EXCLUDED.open,
                                            high = EXCLUDED.high,
                                            low = EXCLUDED.low,
                                            close = EXCLUDED.close,
                                            volume = EXCLUDED.volume
                                    """), {
                                        'exchange': 'binance',
                                        'symbol': symbol,
                                        'market': 'usdtm',
                                        'tf': timeframe,
                                        'open_time': row['open_time'],
                                        'open': float(row['open']),
                                        'high': float(row['high']),
                                        'low': float(row['low']),
                                        'close': float(row['close']),
                                        'volume': float(row['volume'])
                                    })
                                    insert_count += 1
                                logger.info(f"💾 DB에 {insert_count}개 캔들 저장 완료")
                        except Exception as db_err:
                            logger.warning(f"⚠️ DB 저장 실패 (분석은 계속): {db_err}")

                        # 바이낸스 데이터를 사용 (최신 데이터이므로 DB 데이터 대신 사용)
                        candles_fetched = len(fetched_df)
                        df = fetched_df
                        logger.info(f"✅ 바이낸스 데이터 사용 ({candles_fetched}개), 현재가: {float(fetched_df.iloc[-1]['close']):.8f}")
            except Exception as e:
                logger.warning(f"⚠️ 바이낸스 fetch 실패: {e}")

        if len(df) < 20:
            return {
                "success": False,
                "error": f"데이터 부족: {len(df)}개 (최소 20개 필요)",
                "symbol": symbol,
                "timeframe": timeframe
            }

        # 타임존 통일 (tz-naive)
        if df['open_time'].dt.tz is not None:
            df['open_time'] = df['open_time'].dt.tz_localize(None)

        # 정렬
        df = df.sort_values('open_time').reset_index(drop=True)
        df = df.tail(lookback)  # 요청한 개수만큼만

        # 최종 데이터 확인 로그
        latest_price = float(df.iloc[-1]['close'])
        latest_time = df.iloc[-1]['open_time']
        logger.info(f"📈 최종 분석 데이터: {symbol} {timeframe}, 최신캔들={latest_time}, 현재가={latest_price:.8f}")

        # Decimal → float 변환 (DB에서 가져온 데이터 처리)
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df[col].astype(float)

        # 3. 기술적 지표 계산
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # ATR
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=14).mean()

        # Bollinger Bands
        df['bb_mid'] = df['close'].rolling(window=20).mean()
        df['bb_std'] = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_mid'] + 2 * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - 2 * df['bb_std']

        # 4. 시장 분석 데이터 준비
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest

        current_price = float(latest['close'])
        price_change_pct = ((current_price - float(df.iloc[0]['close'])) / float(df.iloc[0]['close'])) * 100

        # 트렌드 판단
        trend = "sideways"
        if float(latest['ema20']) > float(latest['ema50']) > float(latest['ema200']):
            trend = "strong_uptrend"
        elif float(latest['ema20']) > float(latest['ema50']):
            trend = "uptrend"
        elif float(latest['ema20']) < float(latest['ema50']) < float(latest['ema200']):
            trend = "strong_downtrend"
        elif float(latest['ema20']) < float(latest['ema50']):
            trend = "downtrend"

        # 변동성
        volatility = "normal"
        atr_pct = (float(latest['atr']) / current_price) * 100
        if atr_pct > 3:
            volatility = "high"
        elif atr_pct < 1:
            volatility = "low"

        # RSI 상태
        rsi_value = float(latest['rsi']) if pd.notna(latest['rsi']) else 50
        rsi_status = "neutral"
        if rsi_value > 70:
            rsi_status = "overbought"
        elif rsi_value < 30:
            rsi_status = "oversold"

        # BB 상태
        bb_status = "inside"
        if current_price > float(latest['bb_upper']):
            bb_status = "above_upper"
        elif current_price < float(latest['bb_lower']):
            bb_status = "below_lower"

        # 최근 캔들 요약
        recent_candles_summary = []
        for i in range(-5, 0):
            if abs(i) <= len(df):
                row = df.iloc[i]
                candle_type = "bullish" if float(row['close']) > float(row['open']) else "bearish"
                body_size = abs(float(row['close']) - float(row['open']))
                recent_candles_summary.append({
                    "type": candle_type,
                    "open": round(float(row['open']), 8),
                    "high": round(float(row['high']), 8),
                    "low": round(float(row['low']), 8),
                    "close": round(float(row['close']), 8),
                    "body_size": round(body_size, 8)
                })

        # 5. AI 프롬프트 구성
        market_summary = f"""
## 시장 분석 요청

**심볼**: {symbol}
**타임프레임**: {timeframe}
**분석 캔들 수**: {len(df)}개

### 현재 시장 상태
- **현재가**: {current_price:.8f}
- **기간 변화율**: {price_change_pct:+.2f}%
- **트렌드**: {trend}
- **변동성**: {volatility} (ATR: {atr_pct:.2f}%)

### 기술적 지표
- **EMA20**: {float(latest['ema20']):.8f}
- **EMA50**: {float(latest['ema50']):.8f}
- **EMA200**: {float(latest['ema200']):.8f}
- **RSI(14)**: {rsi_value:.1f} ({rsi_status})
- **BB Upper**: {float(latest['bb_upper']):.8f}
- **BB Lower**: {float(latest['bb_lower']):.8f}
- **BB 상태**: {bb_status}

### 최근 5개 캔들
"""
        for i, candle in enumerate(recent_candles_summary):
            market_summary += f"- 캔들 {i+1}: {candle['type']} (O:{candle['open']} H:{candle['high']} L:{candle['low']} C:{candle['close']})\n"

        # 5. AI 프롬프트 로드 (사용자 정의 또는 파일에서)
        if request.custom_prompt:
            # 사용자가 직접 입력한 프롬프트 사용
            prompt_template = request.custom_prompt
            logger.info("📝 사용자 정의 프롬프트 사용")
        else:
            # 파일에서 로드
            try:
                with open(PROMPT_FILE_PATH, 'r', encoding='utf-8') as f:
                    prompt_template = f.read()
                logger.info(f"📝 프롬프트 로드: {PROMPT_FILE_PATH}")
            except FileNotFoundError:
                logger.warning(f"⚠️ 프롬프트 파일 없음, 기본 프롬프트 사용")
                prompt_template = """당신은 전문 암호화폐 선물 트레이더입니다. 다음 시장 데이터를 분석해주세요.

{market_summary}

JSON 형식으로 응답: {{"market_assessment": "평가", "trade_suggestion": {{"direction": "long/short/neutral", "entry": 가격, "stop_loss": 가격, "take_profit_1": 가격}}}}
"""

        ai_prompt = prompt_template.format(market_summary=market_summary)

        # 6. OLLAMA API 호출
        ollama_url = config.get('ollama.host', 'http://localhost:11434') if config else 'http://localhost:11434'
        ollama_model = config.get('ollama.llm_model', 'qwen2.5:7b-instruct') if config else 'qwen2.5:7b-instruct'

        try:
            response = req.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": ai_prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 2000
                    }
                },
                timeout=60
            )

            if response.status_code == 200:
                result = response.json()
                ai_response = result.get('response', '')

                # JSON 파싱 시도
                try:
                    # JSON 부분 추출
                    import re
                    json_match = re.search(r'\{[\s\S]*\}', ai_response)
                    if json_match:
                        ai_report = json.loads(json_match.group())
                    else:
                        ai_report = {"raw_response": ai_response}
                except json.JSONDecodeError:
                    ai_report = {"raw_response": ai_response}

                logger.info(f"✅ AI 분석 완료: {symbol}")

                return {
                    "success": True,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "candles_analyzed": len(df),
                    "candles_from_db": candles_from_db,
                    "candles_fetched": candles_fetched,
                    "market_data": {
                        "current_price": current_price,
                        "price_change_pct": round(price_change_pct, 2),
                        "trend": trend,
                        "volatility": volatility,
                        "rsi": round(rsi_value, 1),
                        "rsi_status": rsi_status,
                        "bb_status": bb_status,
                        "atr_pct": round(atr_pct, 2)
                    },
                    "ai_report": ai_report,
                    "model": ollama_model,
                    "analyzed_at": datetime.now().isoformat()
                }
            else:
                logger.error(f"❌ OLLAMA 오류: {response.status_code}")
                return {
                    "success": False,
                    "error": f"OLLAMA 응답 오류: {response.status_code}",
                    "symbol": symbol,
                    "timeframe": timeframe
                }

        except req.exceptions.ConnectionError:
            logger.error("❌ OLLAMA 서버 연결 실패")
            return {
                "success": False,
                "error": "OLLAMA 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.",
                "symbol": symbol,
                "timeframe": timeframe,
                "market_data": {
                    "current_price": current_price,
                    "price_change_pct": round(price_change_pct, 2),
                    "trend": trend,
                    "volatility": volatility,
                    "rsi": round(rsi_value, 1),
                    "rsi_status": rsi_status
                }
            }
        except req.exceptions.Timeout:
            logger.error("❌ OLLAMA 응답 타임아웃")
            return {
                "success": False,
                "error": "AI 분석 타임아웃 (60초 초과)",
                "symbol": symbol,
                "timeframe": timeframe
            }

    except Exception as e:
        logger.error(f"❌ AI 분석 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": str(e),
            "symbol": symbol,
            "timeframe": timeframe
        }


class OrderRequest(BaseModel):
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_percent: float = 2.0
    leverage: int = 10


@app.post("/api/trade")
async def execute_trade(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    quantity: Optional[float] = None,
    leverage: Optional[int] = None,
    signal_id: Optional[str] = None
):
    """거래 실행 (주문 서버로 전달)"""
    try:
        if side not in ['buy', 'sell']:
            raise HTTPException(status_code=400, detail="side는 'buy' 또는 'sell'이어야 합니다")

        logger.info(f"🚀 거래 요청: {symbol} {side.upper()} @ ${entry_price:,.2f}")

        if not order_client:
            return {
                "success": True,
                "message": "데모 모드 (주문 서버 미연결)",
                "order": {
                    "symbol": symbol, "side": side, "entry_price": entry_price,
                    "stop_loss": stop_loss, "take_profit": take_profit,
                    "status": "demo"
                }
            }

        result = await order_client.create_order(
            symbol=symbol, side=side, order_type='market',
            quantity=quantity, stop_loss=stop_loss, take_profit=take_profit,
            leverage=leverage, signal_id=signal_id, strategy='ICT+AI'
        )

        return result

    except Exception as e:
        logger.error(f"❌ 거래 실행 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/futures/balance")
async def get_futures_balance():
    """선물 잔고"""
    import time
    t0 = time.time()
    try:
        if not exchange_ccxt:
            raise HTTPException(status_code=400, detail="거래소가 초기화되지 않았습니다")

        balance = exchange_ccxt.fetch_balance()
        usdt = balance.get('USDT', {})

        logger.info(f"[API /futures/balance] {(time.time()-t0)*1000:.0f}ms")
        return {
            "balance": usdt.get('total', 0),
            "available": usdt.get('free', 0),
            "used": usdt.get('used', 0),
            "demo": False
        }
    except Exception as e:
        logger.error(f"잔고 조회 실패: {e} ({(time.time()-t0)*1000:.0f}ms)")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/futures/positions")
async def get_futures_positions():
    """현재 포지션"""
    import time
    t0 = time.time()
    try:
        if not exchange_ccxt:
            return []

        positions = exchange_ccxt.fapiPrivateV2GetPositionRisk()

        active = []
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))

                active.append({
                    'symbol': pos.get('symbol'),
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'amount': abs(amt),
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'liquidation_price': float(pos.get('liquidationPrice', 0)),
                    'leverage': int(pos.get('leverage', 1)),
                    'unrealized_pnl': unrealized_pnl,
                    'pnl_percent': (unrealized_pnl / (entry_price * abs(amt))) * 100 if entry_price > 0 else 0
                })

        logger.info(f"[API /futures/positions] {(time.time()-t0)*1000:.0f}ms ({len(active)}개)")
        return active
    except Exception as e:
        logger.error(f"포지션 조회 실패: {e} ({(time.time()-t0)*1000:.0f}ms)")
        return []


def get_precision_decimals(precision_value) -> int:
    """정밀도 값을 소수점 자릿수로 변환

    Binance는 precision을 두 가지 방식으로 반환:
    1. 소수점 자릿수 (정수): 예) 2 → 소수점 2자리
    2. tick size (소수): 예) 0.0001 → 소수점 4자리
    """
    if precision_value is None:
        return 8

    if isinstance(precision_value, int):
        return precision_value

    if isinstance(precision_value, float):
        if precision_value >= 1:
            return 0
        # tick size를 자릿수로 변환: 0.0001 → 4
        import math
        return max(0, -int(math.floor(math.log10(precision_value))))

    return 8


def round_to_precision(value: float, precision_value) -> float:
    """값을 정밀도에 맞게 반올림

    tick size 방식과 자릿수 방식 모두 지원
    """
    if precision_value is None:
        return value

    if isinstance(precision_value, float) and precision_value < 1:
        # tick size 방식: 0.0001 단위로 반올림
        return round(value / precision_value) * precision_value
    else:
        # 자릿수 방식
        decimals = int(precision_value) if isinstance(precision_value, (int, float)) else 8
        return round(value, decimals)


class CalculateRequest(BaseModel):
    """포지션 계산 요청"""
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_percent: float = 1.0
    leverage: int = 5


@app.post("/api/futures/calculate")
async def calculate_position(request: CalculateRequest):
    """포지션 크기 계산 (주문 미리보기)"""
    try:
        if not exchange_ccxt:
            raise HTTPException(status_code=400, detail="거래소가 초기화되지 않았습니다")

        # 잔고 조회
        balance = exchange_ccxt.fetch_balance()
        available = balance.get('USDT', {}).get('free', 0)

        # 리스크 금액
        risk_amount = available * (request.risk_percent / 100)

        # 손절폭 계산
        price_diff = abs(request.entry_price - request.stop_loss)
        stop_loss_percent = (price_diff / request.entry_price) * 100

        # 포지션 크기 계산
        if stop_loss_percent > 0:
            position_value = risk_amount / (stop_loss_percent / 100)
            position_size = position_value / request.entry_price
        else:
            position_value = 0
            position_size = 0

        # 필요 마진 계산
        margin_required = position_value / request.leverage if request.leverage > 0 else position_value

        # 마켓 정보로 정밀도 적용
        try:
            market = exchange_ccxt.market(request.symbol)
            amount_precision = market['precision']['amount']
            position_size = round_to_precision(position_size, amount_precision)
        except:
            position_size = round(position_size, 4)

        return {
            "position_size": position_size,
            "position_value": position_value,
            "margin_required": margin_required,
            "risk_amount": risk_amount,
            "stop_loss_percent": stop_loss_percent,
            "available_balance": available,
            "leverage": request.leverage
        }

    except Exception as e:
        logger.error(f"포지션 계산 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/futures/order")
async def create_futures_order(order: OrderRequest):
    """선물 주문 생성"""
    try:
        if not exchange_ccxt:
            raise HTTPException(status_code=400, detail="거래소가 초기화되지 않았습니다")

        # 잔고 조회
        balance = exchange_ccxt.fetch_balance()
        available = balance.get('USDT', {}).get('free', 0)

        # 포지션 크기 계산
        risk_amount = available * (order.risk_percent / 100)
        price_diff = abs(order.entry_price - order.stop_loss)
        stop_loss_percent = (price_diff / order.entry_price) * 100
        position_value = risk_amount / (stop_loss_percent / 100)
        position_size = position_value / order.entry_price

        # 마켓 정보
        market = exchange_ccxt.market(order.symbol)
        min_amount = market['limits']['amount']['min']
        amount_precision = market['precision']['amount']

        # 수량 정밀도 적용
        position_size = round_to_precision(position_size, amount_precision)
        if min_amount and position_size < min_amount:
            position_size = min_amount

        # 가격 정밀도 적용
        price_precision = market['precision'].get('price')
        stop_loss_price = round_to_precision(order.stop_loss, price_precision)
        take_profit_price = round_to_precision(order.take_profit, price_precision)

        logger.info(f"📊 정밀도 정보: {order.symbol} price_prec={price_precision}, amount_prec={amount_precision}")
        logger.info(f"📊 SL: {order.stop_loss} → {stop_loss_price}, TP: {order.take_profit} → {take_profit_price}")

        logger.info(f"📊 주문: {order.symbol} {order.side} {position_size}")

        # 레버리지 설정
        exchange_ccxt.fapiPrivatePostLeverage({
            'symbol': order.symbol,
            'leverage': order.leverage
        })

        # 시장가 주문
        main_order = exchange_ccxt.create_order(
            symbol=order.symbol,
            type='MARKET',
            side=order.side,
            amount=position_size
        )

        # 손절
        stop_side = 'SELL' if order.side == 'BUY' else 'BUY'
        try:
            stop_order = exchange_ccxt.create_order(
                symbol=order.symbol,
                type='STOP_MARKET',
                side=stop_side,
                amount=position_size,
                params={'stopPrice': stop_loss_price, 'reduceOnly': True}
            )
        except Exception as e:
            stop_order = {'error': str(e)}

        # 익절
        try:
            tp_order = exchange_ccxt.create_order(
                symbol=order.symbol,
                type='TAKE_PROFIT_MARKET',
                side=stop_side,
                amount=position_size,
                params={'stopPrice': take_profit_price, 'reduceOnly': True}
            )
        except Exception as e:
            tp_order = {'error': str(e)}

        return {
            'success': True,
            'main_order': main_order,
            'stop_order': stop_order,
            'tp_order': tp_order
        }

    except Exception as e:
        logger.error(f"주문 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/futures/close/{symbol}")
async def close_futures_position(symbol: str):
    """포지션 청산"""
    try:
        import ccxt

        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')

        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="API 키가 설정되지 않았습니다")

        exchange_ccxt = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange_ccxt.load_markets()

        positions = exchange_ccxt.fapiPrivateV2GetPositionRisk({'symbol': symbol})
        if not positions:
            raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다")

        pos = positions[0]
        amt = float(pos.get('positionAmt', 0))
        if amt == 0:
            raise HTTPException(status_code=400, detail="포지션이 없습니다")

        side = 'SELL' if amt > 0 else 'BUY'
        order = exchange_ccxt.create_order(
            symbol=symbol,
            type='MARKET',
            side=side,
            amount=abs(amt),
            params={'reduceOnly': True}
        )

        return {'success': True, 'order': order}
    except Exception as e:
        logger.error(f"포지션 청산 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/futures/trades")
async def get_futures_trades(
    days: int = 7,
    symbol: Optional[str] = None,
    match_signals: bool = True,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    바이낸스 거래 히스토리 조회 및 신호 매칭

    Args:
        days: 조회할 기간 (일) - start_date가 없을 때 사용
        symbol: 특정 심볼만 조회 (선택)
        match_signals: 신호와 매칭 여부
        start_date: 시작 날짜 (YYYY-MM-DD 형식)
        end_date: 종료 날짜 (YYYY-MM-DD 형식)

    Returns:
        거래 목록 + 매칭된 신호 정보
    """
    try:
        import ccxt

        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')

        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="API 키가 설정되지 않았습니다")

        exchange_inst = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        exchange_inst.load_markets()

        # 시작/종료 시간 계산 (UTC)
        if start_date:
            # YYYY-MM-DD 형식 파싱
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            since_ms = int(start_dt.timestamp() * 1000)
        else:
            since_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)

        end_ms = None
        if end_date:
            # 종료일의 끝 (23:59:59)
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
            end_ms = int(end_dt.timestamp() * 1000)

        trades = []
        if symbol:
            # 특정 심볼 조회 - BTCUSDT -> BTC/USDT 변환
            ccxt_symbol = symbol
            if symbol.endswith('USDT') and '/' not in symbol:
                base = symbol[:-4]
                ccxt_symbol = f"{base}/USDT"

            sym_trades = exchange_inst.fetch_my_trades(
                symbol=ccxt_symbol,
                since=since_ms,
                limit=500
            )
            trades.extend(sym_trades)
        else:
            # 바이낸스 선물 전체 USDT 마켓에서 거래 조회 (필터 없이 모든 심볼)
            usdt_markets = [
                sym for sym, market in exchange_inst.markets.items()
                if market.get('quote') == 'USDT' and market.get('linear', True)
            ]

            for sym in usdt_markets:
                try:
                    sym_trades = exchange_inst.fetch_my_trades(
                        symbol=sym,
                        since=since_ms,
                        limit=500
                    )
                    if sym_trades:
                        trades.extend(sym_trades)
                except Exception as e:
                    # 거래 없는 심볼은 무시
                    continue

        # 거래 데이터 가공
        processed_trades = []
        for trade in trades:
            # end_ms 필터링
            if end_ms and trade['timestamp'] > end_ms:
                continue

            trade_time = datetime.fromtimestamp(trade['timestamp'] / 1000, tz=timezone.utc)

            # 심볼 정규화: 'BTC/USDT:USDT' -> 'BTCUSDT'
            raw_symbol = trade.get('symbol', '')
            clean_symbol = raw_symbol.split(':')[0].replace('/', '')

            processed = {
                'trade_id': trade.get('id'),
                'order_id': trade.get('order'),
                'symbol': clean_symbol,
                'side': trade.get('side', '').lower(),
                'price': float(trade.get('price', 0)),
                'amount': float(trade.get('amount', 0)),
                'cost': float(trade.get('cost', 0)),
                'fee': trade.get('fee', {}).get('cost', 0),
                'fee_currency': trade.get('fee', {}).get('currency', 'USDT'),
                'timestamp': trade['timestamp'],
                'datetime': trade_time.isoformat(),
                'is_maker': trade.get('maker', False),
                'reduce_only': trade.get('info', {}).get('reduceOnly', False),
                'realized_pnl': float(trade.get('info', {}).get('realizedPnl', 0)),
                'source': 'binance',
                'matched_signal': None
            }
            processed_trades.append(processed)

        # 신호 매칭 (match_signals가 True일 때)
        if match_signals and signals_repo and processed_trades:
            # 기간 내 신호 조회
            start_date = datetime.now() - timedelta(days=days)
            all_signals = signals_repo.get_signals(
                start_date=start_date.strftime('%Y-%m-%d'),
                limit=1000
            )

            # 심볼별로 신호 그룹화
            signals_by_symbol = {}
            for sig in all_signals:
                sym = sig.get('symbol', '')
                if sym not in signals_by_symbol:
                    signals_by_symbol[sym] = []
                signals_by_symbol[sym].append(sig)

            # 각 거래에 대해 매칭되는 신호 찾기
            for trade in processed_trades:
                trade_sym = trade['symbol']
                trade_side = trade['side']
                trade_time = datetime.fromtimestamp(trade['timestamp'] / 1000, tz=timezone.utc)

                if trade_sym in signals_by_symbol:
                    # 거래 시간 ±2시간 내의 같은 방향 신호 찾기
                    for sig in signals_by_symbol[trade_sym]:
                        sig_time = sig.get('created_at')
                        if isinstance(sig_time, str):
                            sig_time = datetime.fromisoformat(sig_time.replace('Z', '+00:00'))
                        elif sig_time and sig_time.tzinfo is None:
                            sig_time = sig_time.replace(tzinfo=timezone.utc)

                        if sig_time:
                            time_diff = abs((trade_time - sig_time).total_seconds())
                            if time_diff <= 7200:  # 2시간 = 7200초
                                sig_type = sig.get('signal_type', '').lower()
                                if sig_type == trade_side:
                                    trade['matched_signal'] = {
                                        'signal_id': sig.get('signal_id'),
                                        'strategy': sig.get('strategy_name'),
                                        'entry_price': sig.get('entry_price'),
                                        'stop_loss': sig.get('stop_loss'),
                                        'take_profit_1': sig.get('take_profit_1'),
                                        'confidence': sig.get('confidence'),
                                        'time_diff_minutes': round(time_diff / 60, 1)
                                    }
                                    break

        # 시간순 정렬 (최신순)
        processed_trades.sort(key=lambda x: x['timestamp'], reverse=True)

        return {
            'trades': processed_trades,
            'count': len(processed_trades),
            'days': days,
            'matched_count': sum(1 for t in processed_trades if t['matched_signal'])
        }

    except Exception as e:
        logger.error(f"거래 히스토리 조회 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/signals/link-trade")
async def link_signal_to_trade(data: dict):
    """
    신호와 거래를 연결하고 결과 업데이트

    Args:
        signal_id: 신호 ID
        trade_id: 거래 ID (바이낸스)
        exit_price: 청산가
        pnl: 손익
        notes: 메모
    """
    try:
        signal_id = data.get('signal_id')
        trade_id = data.get('trade_id')
        exit_price = data.get('exit_price')
        pnl = data.get('pnl')
        pnl_percent = data.get('pnl_percent')
        notes = data.get('notes', '')

        if not signal_id:
            raise HTTPException(status_code=400, detail="signal_id 필요")

        # 신호 결과 업데이트
        result_data = {
            'signal_id': signal_id,
            'status': 'tp1_hit' if pnl and pnl > 0 else 'sl_hit' if pnl and pnl < 0 else 'pending',
            'exit_price': exit_price,
            'exit_time': datetime.now(timezone.utc).isoformat() if exit_price else None,
            'pnl': pnl,
            'pnl_percent': pnl_percent,
            'is_simulation': False,  # 실제 거래
            'notes': f"Linked to trade {trade_id}. {notes}" if trade_id else notes
        }

        if signals_repo.save_result(result_data):
            return {'success': True, 'message': '신호-거래 연결 완료'}
        else:
            raise HTTPException(status_code=500, detail="결과 저장 실패")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"신호-거래 연결 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 신호 처리 및 브로드캐스트
# ============================================================

async def process_and_broadcast_signals(signals_data: List[Dict]):
    """분석 결과를 app_state에 저장하고 WebSocket으로 브로드캐스트"""
    if not signals_data:
        return

    # Dict를 TradeSignal 객체로 변환
    new_signals = []
    for s in signals_data:
        try:
            # created_at 파싱
            created_at = None
            if s.get('created_at'):
                try:
                    created_at = datetime.fromisoformat(s['created_at'].replace('Z', '+00:00'))
                except:
                    created_at = datetime.now()
            else:
                created_at = datetime.now()

            # AI 분석 정보를 metadata에 포함
            metadata = s.get('metadata', {}) or {}
            if s.get('ai_analysis'):
                metadata['ai_analysis'] = s['ai_analysis']

            signal = TradeSignal(
                strategy_name=s.get('strategy', 'unknown'),
                symbol=s.get('symbol', ''),
                timeframe=s.get('timeframe', '15m'),
                signal_type=s.get('signal_type', ''),
                entry_price=float(s.get('entry_price', 0)),
                stop_loss=float(s.get('stop_loss', 0)),
                take_profit_1=float(s.get('take_profit_1', 0)),
                take_profit_2=float(s.get('take_profit_2', 0)) if s.get('take_profit_2') else None,
                confidence=float(s.get('confidence', 0.5)),
                risk_reward=float(s.get('risk_reward', 0)),
                reasons=s.get('reasons', []),
                metadata=metadata,
                created_at=created_at
            )
            new_signals.append(signal)
        except Exception as e:
            logger.error(f"신호 변환 실패: {e}, data: {s}")
            continue

    if new_signals:
        # app_state에 저장 (기존 신호 유지, 새 신호 추가)
        # 최대 100개까지만 유지
        app_state.signals = new_signals + app_state.signals
        if len(app_state.signals) > 100:
            app_state.signals = app_state.signals[:100]
        app_state.last_update = datetime.now()

        logger.info(f"📡 실시간 신호 업데이트: {len(new_signals)}개 추가 (총 {len(app_state.signals)}개)")

        # WebSocket으로 브로드캐스트
        signals_for_broadcast = []
        for s in new_signals:
            signal_dict = {
                "strategy": s.strategy_name,
                "symbol": s.symbol,
                "timeframe": s.timeframe,
                "signal_type": s.signal_type,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit_1": s.take_profit_1,
                "confidence": s.confidence,
                "risk_reward": s.risk_reward,
                "reasons": s.reasons,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            if s.metadata and 'ai_analysis' in s.metadata:
                ai = s.metadata['ai_analysis']
                signal_dict['ai_analysis'] = {
                    'decision': ai.get('decision'),
                    'confidence': ai.get('confidence'),
                    'reasoning': ai.get('reasoning'),
                    'rejected': ai.get('rejected', False)
                }
            signals_for_broadcast.append(signal_dict)

        # Paper Trading 엔진에 신호 전달 (실행 중인 경우)
        if app_state.paper_trading_engine and app_state.paper_trading_engine.is_running:
            for signal in new_signals:
                try:
                    signal_dict = {
                        'signal_id': f"{signal.symbol}_{signal.timeframe}_{signal.signal_type}_{signal.created_at.strftime('%Y%m%d%H%M%S') if signal.created_at else ''}",
                        'symbol': signal.symbol,
                        'timeframe': signal.timeframe,
                        'signal_type': signal.signal_type,
                        'entry_price': signal.entry_price,
                        'stop_loss': signal.stop_loss,
                        'take_profit_1': signal.take_profit_1,
                        'confidence': signal.confidence,
                        'risk_reward': signal.risk_reward,
                        'reasons': signal.reasons,
                        'created_at': signal.created_at.isoformat() if signal.created_at else None,
                        'strategy': signal.strategy_name,  # 전략 필터링을 위해 추가
                        'strategy_name': signal.strategy_name  # 호환성을 위해 둘 다 추가
                    }
                    # 비동기로 신호 처리 (논블로킹)
                    result = await app_state.paper_trading_engine.process_signal(signal_dict)
                    if result.get('success'):
                        logger.info(f"✅ Paper Trading 신호 처리 성공: {signal.symbol} {signal.signal_type} ({signal.strategy_name})")
                    else:
                        reason = result.get('reason', 'unknown')
                        # 필터링은 정상 동작이지만 로그로 확인 가능하도록
                        if reason in ['symbol_filtered', 'strategy_filtered']:
                            logger.info(f"⏭️ Paper Trading 신호 필터링: {signal.symbol} {signal.signal_type} ({signal.strategy_name}) - {reason}")
                        else:
                            logger.warning(f"⚠️ Paper Trading 신호 처리 실패: {signal.symbol} {signal.signal_type} ({signal.strategy_name}) - {reason}")
                except Exception as e:
                    logger.error(f"❌ Paper Trading 신호 처리 실패: {e}", exc_info=True)

        # 연결된 모든 클라이언트에게 전송
        message = json.dumps({
            "type": "signals_update",
            "signals": signals_for_broadcast,
            "count": len(signals_for_broadcast),
            "timestamp": datetime.now().isoformat()
        })

        disconnected = []
        for ws in active_connections:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning(f"WebSocket 전송 실패: {e}")
                disconnected.append(ws)

        # 끊어진 연결 제거
        for ws in disconnected:
            if ws in active_connections:
                active_connections.remove(ws)


# ============================================================
# WebSocket
# ============================================================

@app.websocket("/ws/signals")
async def websocket_signals(websocket: WebSocket):
    """실시간 신호 WebSocket"""
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        active_connections.remove(websocket)


@app.websocket("/ws/paper-trading")
async def websocket_paper_trading(websocket: WebSocket):
    """Paper Trading 실시간 데이터 WebSocket"""
    await websocket.accept()
    paper_trading_connections.append(websocket)
    logger.info(f"[WS/paper-trading] 클라이언트 연결 (총 {len(paper_trading_connections)}개)")
    
    try:
        # 초기 상태 전송
        if app_state.paper_trading_engine and app_state.paper_trading_engine.is_running:
            await broadcast_paper_trading_update()
        
        while True:
            try:
                # 클라이언트로부터 메시지 수신 (ping/pong)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in paper_trading_connections:
            paper_trading_connections.remove(websocket)
        logger.info(f"[WS/paper-trading] 클라이언트 연결 해제 (총 {len(paper_trading_connections)}개)")


async def broadcast_paper_trading_update():
    """Paper Trading 상태를 모든 연결된 클라이언트에게 브로드캐스트"""
    if not paper_trading_connections:
        return
    
    if not app_state.paper_trading_engine or not app_state.paper_trading_engine.is_running:
        return
    
    try:
        engine = app_state.paper_trading_engine
        stats = engine.get_stats()
        open_positions = engine.broker.get_open_positions()
        
        # 포지션 데이터 변환
        positions_data = []
        for pos in open_positions:
            current_price = engine.current_prices.get(pos.symbol, pos.entry_price)
            positions_data.append({
                'position_id': pos.position_id,
                'symbol': pos.symbol,
                'side': pos.side.value,
                'entry_price': pos.entry_price,
                'quantity': pos.quantity,
                'current_price': current_price,
                'pnl': pos.pnl,
                'pnl_percent': pos.pnl_percent,
                'stop_loss': pos.stop_loss,
                'take_profit': pos.take_profit,
                'opened_at': pos.opened_at.isoformat() if pos.opened_at else None
            })
        
        message = json.dumps({
            "type": "paper_trading_update",
            "stats": stats,
            "open_positions": positions_data,
            "timestamp": datetime.now().isoformat()
        })
        
        # 모든 연결된 클라이언트에게 전송
        disconnected = []
        for ws in paper_trading_connections:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.debug(f"[WS/paper-trading] 전송 실패: {e}")
                disconnected.append(ws)
        
        # 끊어진 연결 제거
        for ws in disconnected:
            if ws in paper_trading_connections:
                paper_trading_connections.remove(ws)
                
    except Exception as e:
        logger.error(f"[WS/paper-trading] 브로드캐스트 실패: {e}")


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """실시간 로그 WebSocket"""
    await websocket.accept()
    try:
        while True:
            try:
                log_entry = log_queue.get(timeout=1)
                await websocket.send_json(log_entry)
            except queue.Empty:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass


# 포지션 WebSocket 연결 관리
positions_connections: List[WebSocket] = []


@app.websocket("/ws/positions")
async def websocket_positions(websocket: WebSocket):
    """실시간 포지션 WebSocket - 바이낸스 포지션 실시간 업데이트"""
    await websocket.accept()
    positions_connections.append(websocket)
    logger.info(f"[WS/positions] 클라이언트 연결 (총 {len(positions_connections)}개)")

    try:
        # 초기 포지션 전송
        positions = await _fetch_positions()
        await websocket.send_json({"type": "positions", "data": positions})

        # 주기적으로 포지션 업데이트 (1초마다)
        last_positions = positions
        while True:
            try:
                # 클라이언트 메시지 확인 (ping/pong)
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                    if data == "ping":
                        await websocket.send_text("pong")
                except asyncio.TimeoutError:
                    pass

                # 포지션 업데이트
                current_positions = await _fetch_positions()

                # 변경 감지 (간단히 JSON 비교)
                if current_positions != last_positions:
                    await websocket.send_json({"type": "positions", "data": current_positions})
                    last_positions = current_positions

            except Exception as e:
                logger.debug(f"[WS/positions] 루프 에러: {e}")
                await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    finally:
        if websocket in positions_connections:
            positions_connections.remove(websocket)
        logger.info(f"[WS/positions] 클라이언트 연결 해제 (총 {len(positions_connections)}개)")


async def _fetch_positions():
    """바이낸스 포지션 조회 (내부 함수)"""
    try:
        if not exchange_ccxt:
            return []

        positions = exchange_ccxt.fapiPrivateV2GetPositionRisk()

        active = []
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))

                active.append({
                    'symbol': pos.get('symbol'),
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'amount': abs(amt),
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'liquidation_price': float(pos.get('liquidationPrice', 0)),
                    'leverage': int(pos.get('leverage', 1)),
                    'unrealized_pnl': unrealized_pnl,
                    'pnl_percent': (unrealized_pnl / (entry_price * abs(amt))) * 100 if entry_price > 0 else 0
                })

        return active
    except Exception as e:
        logger.error(f"[WS/positions] 포지션 조회 실패: {e}")
        return []


# ============================================================
# 메인
# ============================================================

if __name__ == "__main__":
    import uvicorn
    import signal

    # Windows Ctrl+C 핸들러
    def signal_handler(signum, frame):
        logger.info("👋 종료 신호 수신, 서버를 종료합니다...")
        os._exit(0)  # Python 3.14 asyncio 호환성 문제 우회

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # HTTPS 설정
    use_https = os.getenv('USE_HTTPS', 'false').lower() == 'true'

    if use_https:
        from auth.ssl_setup import generate_self_signed_cert, CERT_FILE, KEY_FILE

        # 인증서 생성 (없으면)
        cert_path, key_path = generate_self_signed_cert()

        logger.info(f"🔒 HTTPS 모드로 시작 (포트 8888)")
        logger.info(f"   인증서: {cert_path}")
        logger.info(f"   종료: Ctrl+C")

        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8888,
            log_level="info",
            ssl_certfile=cert_path,
            ssl_keyfile=key_path
        )
    else:
        logger.info(f"🌐 HTTP 모드로 시작 (포트 8888)")
        logger.info(f"   종료: Ctrl+C")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8888,
            log_level="info"
        )
