"""
실시간 트레이딩 테스트용 API 서버

개발 및 테스트 완료 후 web_server.py에 통합
"""

import os
import sys
import asyncio
import json
import logging
import math
from typing import Dict, Optional, List
from pathlib import Path
from datetime import datetime, timezone

# 프로젝트 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))  # tradeBot_dev/servers
_tradebot_dev_dir = os.path.dirname(_current_dir)  # tradeBot_dev
_pgdb_dir = os.path.dirname(_tradebot_dev_dir)  # PGdb
_tradebot_dir = os.path.join(_pgdb_dir, 'tradeBot')  # tradeBot (운영 시스템)

# 경로 추가 (운영 시스템 모듈 사용)
sys.path.insert(0, _tradebot_dir)
sys.path.insert(0, _pgdb_dir)
# 개발용 폴더도 추가 (realtime_trading_engine.py import용)
sys.path.insert(0, _tradebot_dev_dir)

from fastapi import FastAPI, WebSocket, HTTPException, Depends, Body, Request, Query
from pydantic import BaseModel
from fastapi.websockets import WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# 운영 시스템 모듈 import
from core.config_loader import get_config
from strategies import create_strategy_manager
from brokers.paper_broker import PaperBroker
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from database.positions_repo import PositionsRepo
from database.strategy_settings_repo import StrategySettingsRepo
import pandas as pd

# 개발용 모듈 import (경로 조정)
_tradebot_dev_services = os.path.join(_tradebot_dev_dir, 'services')
sys.path.insert(0, _tradebot_dev_services)
from realtime_trading_engine import RealtimeTradingEngine
from adaptive_strategy_manager import AdaptiveStrategyManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Realtime Trading API (Dev)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 전역 상태
app_state = {
    'realtime_engines': {},  # 엔진 ID -> RealtimeTradingEngine 인스턴스
    'strategy_manager': None,
    'config': None,
    'positions_repo': None,
    'strategy_settings_repo': None,
    'websocket_connections': []  # WebSocket 연결 목록
}

# 초기화
try:
    config = get_config()
    app_state['config'] = config
    logger.info("✅ Config 로드 완료")
except Exception as e:
    logger.error(f"❌ Config 로드 실패: {e}", exc_info=True)
    config = None
    app_state['config'] = None

# DB 연결 및 PositionsRepo 초기화
try:
    if config:
        db_user = config.get('db.user') or 'trader'
        db_password = config.get('db.password') or ''
        db_host = config.get('db.host') or 'localhost'
        db_port = config.get('db.port') or 5432
        db_name = config.get('db.name') or 'marketdb'
        
        db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        db_engine = create_engine(db_url)
        positions_repo = PositionsRepo(db_engine)
        strategy_settings_repo = StrategySettingsRepo(db_engine)
        app_state['positions_repo'] = positions_repo
        app_state['strategy_settings_repo'] = strategy_settings_repo
        logger.info("✅ DB 연결 및 PositionsRepo, StrategySettingsRepo 초기화 완료")
    else:
        logger.warning("⚠️ Config가 없어 DB 초기화를 건너뜁니다")
        app_state['positions_repo'] = None
except Exception as e:
    logger.error(f"❌ DB 초기화 실패: {e}", exc_info=True)
    app_state['positions_repo'] = None

# 전략 매니저 초기화 (DB 기반, config.yaml strategies 사용 안 함)
try:
    if app_state.get('strategy_settings_repo'):
        cfg = (config.config if config and hasattr(config, 'config') else {}) or {}
        strategy_manager = create_strategy_manager(strategy_settings_repo=app_state['strategy_settings_repo'], config=cfg)
        app_state['strategy_manager'] = strategy_manager
        logger.info("✅ 전략 매니저 초기화 완료 (DB 기반)")
    else:
        logger.warning("⚠️ strategy_settings_repo 없어 전략 매니저 초기화를 건너뜁니다")
        app_state['strategy_manager'] = None
except Exception as e:
    logger.error(f"❌ 전략 매니저 초기화 실패: {e}", exc_info=True)
    app_state['strategy_manager'] = None

logger.info("✅ 실시간 트레이딩 API 서버 초기화 완료")

# 실행 종목 지표 스냅샷: 매 정시, 15분, 30분, 45분에 저장
SNAPSHOT_MINUTES = (0, 15, 30, 45)


def _ensure_snapshots_table():
    """realtime_indicator_snapshots 테이블 생성 (없으면) - 인라인 SQL로 경로 무관 동작"""
    if not app_state.get('positions_repo') or not hasattr(app_state['positions_repo'], 'engine'):
        return
    try:
        db_engine = app_state['positions_repo'].engine
        with db_engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS realtime_indicator_snapshots (
                    id                  SERIAL PRIMARY KEY,
                    snapshot_time       TIMESTAMP WITH TIME ZONE NOT NULL,
                    engine_id           VARCHAR(128) NOT NULL,
                    symbol              VARCHAR(32) NOT NULL,
                    strategy            VARCHAR(64) NOT NULL,
                    timeframe           VARCHAR(16) NOT NULL,
                    current_price       NUMERIC(20, 8),
                    ema_20              NUMERIC(20, 8),
                    ema_50              NUMERIC(20, 8),
                    alignment           VARCHAR(16),
                    rsi                 NUMERIC(8, 2),
                    ema_spread          NUMERIC(10, 4),
                    entry_score         NUMERIC(5, 2),
                    golden_cross        BOOLEAN,
                    dead_cross          BOOLEAN,
                    position_snapshot   JSONB,
                    exit_info           JSONB,
                    created_at          TIMESTAMP WITH TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_time
                ON realtime_indicator_snapshots(snapshot_time DESC)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_engine
                ON realtime_indicator_snapshots(engine_id)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_realtime_snapshots_symbol
                ON realtime_indicator_snapshots(symbol)
            """))
        with db_engine.begin() as conn2:
            for col in ('golden_cross', 'dead_cross'):
                try:
                    conn2.execute(text(f"ALTER TABLE realtime_indicator_snapshots ADD COLUMN {col} BOOLEAN"))
                except Exception:
                    pass
        logger.info("✅ realtime_indicator_snapshots 테이블 확인/생성 완료")
    except Exception as e:
        logger.warning(f"⚠️ 스냅샷 테이블 생성 실패: {e}")


async def _save_all_snapshots():
    """실행 중인 모든 엔진의 지표+진입/청산 정보를 DB에 저장"""
    if not app_state.get('positions_repo') or not hasattr(app_state['positions_repo'], 'engine'):
        return
    db_engine = app_state['positions_repo'].engine
    now_utc = datetime.now(timezone.utc)
    # 스냅샷 시각을 해당 분의 0초로 (정시/15/30/45)
    snapshot_time = now_utc.replace(second=0, microsecond=0)

    for engine_id, engine in list(app_state['realtime_engines'].items()):
        if not engine.is_running:
            continue
        try:
            status = engine.get_status()
            ind = status.get('indicators') or {}
            cross = status.get('last_candle_cross') or {}
            pos = status.get('position') or {}
            exit_info = pos.get('exit_info') if pos else None
            position_snapshot = None
            if pos:
                position_snapshot = {
                    'position_id': pos.get('position_id'),
                    'side': pos.get('side'),
                    'entry_price': pos.get('entry_price'),
                    'quantity': pos.get('quantity'),
                    'current_price': pos.get('current_price'),
                    'pnl': pos.get('pnl'),
                    'pnl_percent': pos.get('pnl_percent'),
                    'stop_loss': pos.get('stop_loss'),
                    'take_profit': pos.get('take_profit'),
                }
            query = text("""
                INSERT INTO realtime_indicator_snapshots
                (snapshot_time, engine_id, symbol, strategy, timeframe,
                 current_price, ema_20, ema_50, alignment, rsi, ema_spread, entry_score,
                 golden_cross, dead_cross, position_snapshot, exit_info)
                VALUES (:snapshot_time, :engine_id, :symbol, :strategy, :timeframe,
                        :current_price, :ema_20, :ema_50, :alignment, :rsi, :ema_spread, :entry_score,
                        :golden_cross, :dead_cross,
                        CAST(:position_snapshot AS jsonb), CAST(:exit_info AS jsonb))
            """)
            pos_json = json.dumps(position_snapshot) if position_snapshot else None
            exit_json = json.dumps(exit_info) if exit_info else None
            with db_engine.begin() as conn:
                conn.execute(query, {
                    'snapshot_time': snapshot_time,
                    'engine_id': engine_id,
                    'symbol': status.get('symbol', ''),
                    'strategy': status.get('strategy', ''),
                    'timeframe': status.get('timeframe', ''),
                    'current_price': status.get('current_price'),
                    'ema_20': ind.get('ema_20'),
                    'ema_50': ind.get('ema_50'),
                    'alignment': ind.get('alignment'),
                    'rsi': ind.get('rsi'),
                    'ema_spread': ind.get('ema_spread'),
                    'entry_score': ind.get('entry_score'),
                    'golden_cross': cross.get('golden_cross'),
                    'dead_cross': cross.get('dead_cross'),
                    'position_snapshot': pos_json,
                    'exit_info': exit_json,
                })
            logger.info(f"📸 스냅샷 저장: {engine_id} @ {snapshot_time.strftime('%H:%M')}")
        except Exception as e:
            logger.warning(f"⚠️ 스냅샷 저장 실패 ({engine_id}): {e}")


async def _snapshot_scheduler_loop():
    """매 정시/15/30/45분에 스냅샷 저장 루프"""
    last_done_minute = -1
    while True:
        try:
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)
            if now.minute not in SNAPSHOT_MINUTES:
                continue
            if now.minute == last_done_minute:
                continue
            last_done_minute = now.minute
            if app_state['realtime_engines']:
                await _save_all_snapshots()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"⚠️ 스냅샷 스케줄러 오류: {e}")


# 전역 예외 핸들러
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """전역 예외 처리"""
    logger.error(f"❌ 예외 발생 ({request.method} {request.url}): {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": str(exc),
            "error": str(exc),
            "type": type(exc).__name__,
            "path": str(request.url.path)
        }
    )


# Static files
static_dir = Path(_tradebot_dev_dir) / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/favicon.ico")
async def favicon():
    """Favicon 요청 처리 (404 방지)"""
    return HTMLResponse("", status_code=204)  # No Content

@app.get("/")
async def root():
    """메인 페이지"""
    html_file = static_dir / "realtime_trading.html"
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse("<h1>Realtime Trading (Dev)</h1><p>realtime_trading.html을 생성하세요.</p>")

@app.get("/monitor")
async def monitor_page():
    """실시간 모니터링 페이지"""
    html_file = static_dir / "realtime_monitor.html"
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse("<h1>실시간 모니터링</h1><p>realtime_monitor.html을 생성하세요.</p>")

@app.get("/history")
async def history_page():
    """거래 히스토리 페이지"""
    html_file = static_dir / "realtime_history.html"
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse("<h1>거래 히스토리</h1><p>realtime_history.html을 생성하세요.</p>")


@app.get("/snapshots")
async def snapshots_page():
    """실행 종목 지표 스냅샷 조회 페이지 (매 정시/15/30/45분 저장분)"""
    html_file = static_dir / "realtime_snapshots.html"
    if html_file.exists():
        return FileResponse(html_file)
    return HTMLResponse("<h1>실행 종목 지표 스냅샷</h1><p>realtime_snapshots.html을 생성하세요.</p>")


# Binance USD-M Futures API (USDT-margined perpetual)
BINANCE_USDM_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"


@app.get("/api/binance-symbols")
async def get_binance_symbols(query: str = "", limit: int = 500):
    """바이낸스 USD-M 선물(USDT 마진 영구선물) 전체 심볼 목록 (검색 필터 지원)"""
    import httpx

    try:
        if 'binance_symbols' not in app_state or not app_state.get('binance_symbols'):
            async with httpx.AsyncClient() as client:
                res = await client.get(BINANCE_USDM_EXCHANGE_INFO, timeout=10.0)
                data = res.json()
                app_state['binance_symbols'] = [
                    s['symbol'] for s in data.get('symbols', [])
                    if s.get('status') == 'TRADING'
                    and s.get('contractType') == 'PERPETUAL'
                    and s.get('symbol', '').endswith('USDT')
                ]
                app_state['binance_symbols'].sort()

        symbols = app_state['binance_symbols']
        if query:
            q = query.upper().strip()
            symbols = [s for s in symbols if q in s]
        symbols = symbols[:limit]
        return {"symbols": symbols, "count": len(symbols), "total": len(app_state['binance_symbols'])}
    except Exception as e:
        logger.error(f"바이낸스 심볼 조회 실패: {e}")
        default = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT',
                   'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT', 'UNIUSDT', 'ATOMUSDT']
        if query:
            q = query.upper()
            default = [s for s in default if q in s]
        return {"symbols": default, "count": len(default), "total": len(default)}


@app.on_event("startup")
async def startup_event():
    """앱 시작 시 스냅샷 테이블 생성 및 스케줄러 시작"""
    _ensure_snapshots_table()
    asyncio.create_task(_snapshot_scheduler_loop())
    logger.info("📸 스냅샷 스케줄러 시작 (매 정시/15/30/45분)")


def _generate_engine_id(symbol: str, strategy: str, timeframe: str) -> str:
    """엔진 ID 생성"""
    return f"{symbol}_{strategy}_{timeframe}"


@app.post("/api/realtime/start")
async def start_realtime_trading(request: Dict = Body(...)):
    """실시간 모니터링 시작 (여러 엔진 지원)"""
    try:
        # 필수 컴포넌트 확인
        if not app_state['strategy_manager']:
            raise HTTPException(status_code=500, detail="전략 매니저가 초기화되지 않았습니다. 서버 로그를 확인하세요.")

        symbol = request.get('symbol')
        strategy_name = request.get('strategy')
        timeframe = request.get('timeframe', '15m')
        initial_capital = request.get('initial_capital', 10000.0)

        if not symbol or not strategy_name:
            raise HTTPException(status_code=400, detail="symbol과 strategy가 필요합니다")
        
        # 심볼 정규화: ETHUSDT.P -> ETHUSDT (TradingView 형식 제거)
        # 실제 Binance API/CCXT에서는 .P 형식을 사용하지 않음
        symbol = symbol.replace('.P', '').upper()
        
        # 엔진 ID 생성
        engine_id = _generate_engine_id(symbol, strategy_name, timeframe)
        
        # 이미 같은 엔진이 실행 중인지 확인
        if engine_id in app_state['realtime_engines']:
            existing_engine = app_state['realtime_engines'][engine_id]
            if existing_engine.is_running:
                return {
                    "success": False,
                    "message": f"이미 실행 중입니다 ({symbol} {strategy_name} {timeframe})",
                    "engine_id": engine_id
                }

        # PaperBroker 생성
        broker_config = {
            'initial_capital': initial_capital,
            'commission_rate': 0.001,
            'slippage_pct': 0.05,
            'max_positions': 1,  # 단일 심볼이므로 1개만
            'max_daily_loss_pct': 0.02
        }
        broker = PaperBroker(broker_config)

        # 로그/포지션 업데이트 콜백 (WebSocket으로 브로드캐스트)
        async def broadcast_update(notification_type=None, message=None, trade_event=None):
            """WebSocket으로 업데이트 브로드캐스트"""
            if app_state['websocket_connections']:
                status = engine.get_status() if engine else None
                for ws in app_state['websocket_connections']:
                    try:
                        await ws.send_json({
                            "type": "status_update",
                            "engine_id": engine_id,
                            "data": status
                        })
                        # 청산 알림이 있으면 별도로 전송
                        if notification_type == 'exit' and message:
                            await ws.send_json({
                                "type": "notification",
                                "engine_id": engine_id,
                                "notification_type": "exit",
                                "message": message
                            })
                        # 거래 이벤트 (진입/청산) 전송
                        if trade_event:
                            await ws.send_json({
                                "type": "trade_event",
                                "engine_id": engine_id,
                                "event": trade_event
                            })
                    except Exception as e:
                        logger.warning(f"WebSocket 전송 실패: {e}")

        # 거래 이벤트 콜백 (진입/청산)
        async def on_trade_event(event):
            """거래 이벤트 (진입/청산) WebSocket 브로드캐스트"""
            if app_state['websocket_connections']:
                for ws in app_state['websocket_connections']:
                    try:
                        await ws.send_json({
                            "type": "trade_event",
                            "engine_id": engine_id,
                            "event": event
                        })
                    except Exception as e:
                        logger.warning(f"거래 이벤트 WebSocket 전송 실패: {e}")

        # RealtimeTradingEngine 생성
        engine = RealtimeTradingEngine(
            symbol=symbol,
            strategy_name=strategy_name,
            timeframe=timeframe,
            broker=broker,
            strategy_manager=strategy_manager,
            positions_repo=positions_repo,
            on_log_update=None,  # WebSocket으로 전송 (나중에 구현)
            on_position_update=broadcast_update
        )
        
        # 거래 이벤트 콜백 설정
        engine.on_trade_event = on_trade_event
        
        # 적응형 전략 매니저 초기화 (DB가 있을 때만)
        if positions_repo and hasattr(positions_repo, 'engine'):
            try:
                adaptive_manager = AdaptiveStrategyManager(
                    db_engine=positions_repo.engine,
                    symbol=symbol,
                    timeframe=timeframe,
                    auto_switch=False,  # 기본값: 제안만
                    suggest_switches=True,
                    min_confidence=0.7,
                    min_switch_interval_hours=24,
                    min_trades_for_evaluation=10
                )
                engine.adaptive_manager = adaptive_manager
                logger.info(f"✅ 적응형 전략 매니저 초기화 완료: {symbol} {timeframe}")
            except Exception as e:
                logger.warning(f"⚠️ 적응형 전략 매니저 초기화 실패: {e}")
                engine.adaptive_manager = None

        # 시작
        await engine.start()
        app_state['realtime_engines'][engine_id] = engine

        logger.info(f"🚀 실시간 모니터링 시작: {symbol} {strategy_name} {timeframe} (ID: {engine_id})")

        return {
            "success": True,
            "message": "실시간 모니터링이 시작되었습니다",
            "engine_id": engine_id,
            "symbol": symbol,
            "strategy": strategy_name,
            "timeframe": timeframe
        }

    except Exception as e:
        logger.error(f"시작 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"시작 실패: {str(e)}")


@app.post("/api/realtime/stop")
async def stop_realtime_trading(request: Dict = Body(...)):
    """실시간 모니터링 중지 (엔진 ID 기반)"""
    try:
        engine_id = request.get('engine_id')
        
        # engine_id가 없으면 모든 엔진 중지
        if not engine_id:
            stopped_count = 0
            for eid, engine in list(app_state['realtime_engines'].items()):
                if engine.is_running:
                    await engine.stop()
                    del app_state['realtime_engines'][eid]
                    stopped_count += 1
            
            if stopped_count == 0:
                return {
                    "success": False,
                    "message": "실행 중인 엔진이 없습니다"
                }
            
            logger.info(f"⏹️ 모든 실시간 모니터링 중지 ({stopped_count}개)")
            return {
                "success": True,
                "message": f"실시간 모니터링이 중지되었습니다 ({stopped_count}개)"
            }
        
        # 특정 엔진 중지
        if engine_id not in app_state['realtime_engines']:
            return {
                "success": False,
                "message": f"엔진을 찾을 수 없습니다: {engine_id}"
            }
        
        engine = app_state['realtime_engines'][engine_id]
        await engine.stop()
        del app_state['realtime_engines'][engine_id]
        
        logger.info(f"⏹️ 실시간 모니터링 중지: {engine_id}")

        return {
            "success": True,
            "message": "실시간 모니터링이 중지되었습니다",
            "engine_id": engine_id
        }

    except Exception as e:
        logger.error(f"중지 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"중지 실패: {str(e)}")


@app.get("/api/realtime/status")
async def get_realtime_status(engine_id: Optional[str] = Query(None)):
    """모니터링 상태 조회 (여러 엔진 지원)"""
    try:
        # engine_id가 없으면 모든 엔진 상태 반환
        if not engine_id:
            engines_status = {}
            for eid, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    engines_status[eid] = engine.get_status()
            
            return {
                "success": True,
                "engines": engines_status,
                "count": len(engines_status)
            }
        
        # 특정 엔진 상태 조회
        if engine_id not in app_state['realtime_engines']:
            return {
                "success": False,
                "message": f"엔진을 찾을 수 없습니다: {engine_id}"
            }
        
        engine = app_state['realtime_engines'][engine_id]
        status = engine.get_status()
        status['engine_id'] = engine_id

        return {
            "success": True,
            **status
        }

    except Exception as e:
        logger.error(f"상태 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/realtime/strategy-rules")
async def get_strategy_entry_rules(engine_id: Optional[str] = Query(None)):
    """현재 전략의 진입 규칙 조회"""
    try:
        # engine_id가 없으면 첫 번째 실행 중인 엔진 사용
        if not engine_id:
            for eid, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    engine_id = eid
                    break
            
            if not engine_id:
                return {
                    "success": False,
                    "message": "실행 중인 엔진이 없습니다"
                }
        
        if engine_id not in app_state['realtime_engines']:
            return {
                "success": False,
                "message": f"엔진을 찾을 수 없습니다: {engine_id}"
            }

        engine = app_state['realtime_engines'][engine_id]
        rules = engine.get_strategy_entry_rules()
        rules['engine_id'] = engine_id

        return {
            "success": True,
            **rules
        }

    except Exception as e:
        logger.error(f"진입 규칙 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.post("/api/realtime/strategy-config")
async def update_strategy_config(request: Dict = Body(...)):
    """실행 중인 엔진의 전략 설정 업데이트 (진입/청산 관련 옵션)"""
    try:
        engine_id = request.get('engine_id')
        config_updates = request.get('config', {})

        if not engine_id:
            return {"success": False, "message": "engine_id가 필요합니다."}
        if engine_id not in app_state.get('realtime_engines', {}):
            return {"success": False, "message": f"엔진을 찾을 수 없습니다: {engine_id}"}

        engine = app_state['realtime_engines'][engine_id]
        if not engine.strategy:
            return {"success": False, "message": "전략 객체가 없습니다."}

        # config_updates 값 타입 변환 (스키마 기반)
        schema = getattr(engine.strategy.__class__, 'get_config_schema', lambda: {})()
        for key, val in list(config_updates.items()):
            if key in schema:
                s = schema[key]
                t = s.get('type', '')
                if t == 'number' and val is not None:
                    try:
                        config_updates[key] = float(val) if '.' in str(val) or 'e' in str(val).lower() else int(val)
                    except (ValueError, TypeError):
                        pass
                elif t == 'checkbox':
                    config_updates[key] = bool(val) if isinstance(val, bool) else str(val).lower() in ('true', '1', 'yes', 'on')

        engine.strategy.update_config(config_updates)
        strategy_name = engine.strategy_name

        # DB에 영구 저장 (다음 로드 시 유지)
        ss_repo = app_state.get('strategy_settings_repo')
        if ss_repo:
            ok = ss_repo.patch(strategy_name, config_patch=config_updates)
            if ok:
                logger.info(f"전략 설정 DB 저장 완료: {strategy_name}, 변경: {list(config_updates.keys())}")
            else:
                logger.warning(f"전략 설정 DB 저장 실패: {strategy_name}")

        logger.info(f"전략 설정 업데이트: {engine_id}, 변경: {list(config_updates.keys())}")

        return {
            "success": True,
            "message": "설정이 저장되었습니다.",
            "config": engine.strategy.config
        }
    except Exception as e:
        logger.error(f"전략 설정 업데이트 실패: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def _fetch_snapshots_from_db(db_engine, params, where):
    """스냅샷 SELECT 실행 (테이블 없으면 예외)"""
    query = text(f"""
        SELECT id, snapshot_time, engine_id, symbol, strategy, timeframe,
               current_price, ema_20, ema_50, alignment, rsi, ema_spread, entry_score,
               golden_cross, dead_cross, position_snapshot, exit_info, created_at
        FROM realtime_indicator_snapshots
        WHERE {where}
        ORDER BY snapshot_time DESC
        LIMIT :limit
    """)
    with db_engine.connect() as conn:
        result = conn.execute(query, params)
        return result.fetchall()


@app.get("/api/realtime/snapshots")
async def get_realtime_snapshots(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD 또는 YYYY-MM-DDTHH:mm"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD 또는 YYYY-MM-DDTHH:mm"),
    limit: int = Query(200, ge=1, le=1000),
):
    """실행 종목 지표 스냅샷 목록 조회 (매 정시/15/30/45분 저장분)"""
    if not app_state.get('positions_repo') or not hasattr(app_state['positions_repo'], 'engine'):
        return {"success": True, "snapshots": [], "count": 0}
    db_engine = app_state['positions_repo'].engine
    conditions = ["1=1"]
    params = {"limit": limit}
    if symbol:
        conditions.append("symbol = :symbol")
        params["symbol"] = symbol
    if strategy:
        conditions.append("strategy = :strategy")
        params["strategy"] = strategy
    if from_date:
        conditions.append("snapshot_time >= :from_date")
        params["from_date"] = from_date.replace("Z", "+00:00")
    if to_date:
        conditions.append("snapshot_time <= :to_date")
        params["to_date"] = to_date.replace("Z", "+00:00")
    where = " AND ".join(conditions)

    try:
        rows = _fetch_snapshots_from_db(db_engine, params, where)
    except ProgrammingError as e:
        err_str = str(e)
        if "realtime_indicator_snapshots" in err_str and ("does not exist" in err_str or "릴레이션" in err_str or "UndefinedTable" in err_str):
            logger.info("📸 스냅샷 테이블 없음, 생성 후 재시도")
            _ensure_snapshots_table()
            try:
                rows = _fetch_snapshots_from_db(db_engine, params, where)
            except Exception as e2:
                logger.warning(f"스냅샷 조회 재시도 실패: {e2}")
                return {"success": True, "snapshots": [], "count": 0}
        elif "golden_cross" in err_str or "dead_cross" in err_str or "column" in err_str:
            logger.info("📸 스냅샷 컬럼 없음, ALTER 후 재시도")
            _ensure_snapshots_table()
            try:
                rows = _fetch_snapshots_from_db(db_engine, params, where)
            except Exception as e2:
                logger.warning(f"스냅샷 조회 재시도 실패: {e2}")
                return {"success": True, "snapshots": [], "count": 0}
        else:
            raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")
    except Exception as e:
        logger.error(f"스냅샷 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")

    snapshots = []
    for row in rows:
        # row: id, snapshot_time, engine_id, symbol, strategy, timeframe, current_price, ema_20, ema_50,
        #      alignment, rsi, ema_spread, entry_score, golden_cross, dead_cross, position_snapshot, exit_info, created_at
        snap = {
            "id": row[0],
            "snapshot_time": row[1].isoformat() if row[1] else None,
            "engine_id": row[2],
            "symbol": row[3],
            "strategy": row[4],
            "timeframe": row[5],
            "current_price": float(row[6]) if row[6] is not None else None,
            "ema_20": float(row[7]) if row[7] is not None else None,
            "ema_50": float(row[8]) if row[8] is not None else None,
            "alignment": row[9],
            "rsi": float(row[10]) if row[10] is not None else None,
            "ema_spread": float(row[11]) if row[11] is not None else None,
            "entry_score": float(row[12]) if row[12] is not None else None,
            "golden_cross": bool(row[13]) if row[13] is not None else None,
            "dead_cross": bool(row[14]) if row[14] is not None else None,
            "position_snapshot": row[15] if isinstance(row[15], dict) else (json.loads(row[15]) if row[15] else None),
            "exit_info": row[16] if isinstance(row[16], dict) else (json.loads(row[16]) if row[16] else None),
            "created_at": row[17].isoformat() if row[17] else None,
        }
        snapshots.append(snap)

    # 종목별 P&L 요약 (엔진별 최신 스냅샷의 pnl을 심볼별 합산)
    by_engine = {}
    for s in snapshots:
        pos = s.get("position_snapshot")
        if not pos:
            continue
        pnl = pos.get("pnl")
        if pnl is None or (isinstance(pnl, (int, float)) and (pnl != pnl or isinstance(pnl, bool))):
            continue
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        key = s.get("engine_id") or f"{s.get('symbol')}_{s.get('strategy')}_{s.get('timeframe')}"
        if key not in by_engine:
            by_engine[key] = {"symbol": s.get("symbol", ""), "pnl": pnl, "pnl_percent": pos.get("pnl_percent")}
    by_symbol = {}
    for v in by_engine.values():
        sym = v["symbol"]
        if sym not in by_symbol:
            by_symbol[sym] = {"symbol": sym, "pnl": 0.0, "pnl_percent": None}
        by_symbol[sym]["pnl"] += v["pnl"]
        if v.get("pnl_percent") is not None:
            by_symbol[sym]["pnl_percent"] = v["pnl_percent"]
    symbol_pnl_summary = [
        {"symbol": s["symbol"], "pnl": s["pnl"], "pnl_percent": s["pnl_percent"]}
        for s in by_symbol.values()
        if s["pnl"] != 0 or s["pnl_percent"] is not None
    ]
    symbol_pnl_summary.sort(key=lambda x: x["pnl"] or 0, reverse=True)

    return {"success": True, "snapshots": snapshots, "count": len(snapshots), "symbol_pnl_summary": symbol_pnl_summary}


@app.get("/api/realtime/trade-sets")
async def get_realtime_trade_sets(
    symbol: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """진입·청산 세트 목록 (tradebot_positions, source=realtime_trading, status=closed)"""
    if not app_state.get('positions_repo') or not hasattr(app_state['positions_repo'], 'engine'):
        return {"success": True, "trade_sets": [], "count": 0}
    db_engine = app_state['positions_repo'].engine
    conditions = ["status = 'closed'", "COALESCE(source, 'binance') = 'realtime_trading'"]
    params = {"limit": limit}
    if symbol:
        conditions.append("symbol = :symbol")
        params["symbol"] = symbol
    if strategy:
        conditions.append("(metadata->>'strategy' = :strategy OR metadata->>'strategy_name' = :strategy)")
        params["strategy"] = strategy
    if from_date:
        conditions.append("close_time >= :from_date")
        params["from_date"] = from_date.replace("Z", "+00:00") if "Z" in from_date else (from_date if "T" in from_date else from_date + "T00:00:00")
    if to_date:
        conditions.append("close_time <= :to_date")
        params["to_date"] = to_date.replace("Z", "+00:00") if "Z" in to_date else (to_date if "T" in to_date else to_date + "T23:59:59")
    where = " AND ".join(conditions)
    try:
        q = text(f"""
            SELECT position_id, symbol, side, entry_price, exit_price, quantity,
                   pnl, pnl_percent, open_time, close_time, duration_minutes, metadata
            FROM tradebot_positions
            WHERE {where}
            ORDER BY close_time DESC NULLS LAST
            LIMIT :limit
        """)
        with db_engine.connect() as conn:
            rows = conn.execute(q, params).fetchall()
    except Exception as e:
        logger.warning(f"거래세트 조회 실패 (테이블 없을 수 있음): {e}")
        return {"success": True, "trade_sets": [], "count": 0}
    trade_sets = []
    for r in rows:
        meta = r[11] if len(r) > 11 and r[11] else {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta) or {}
            except Exception:
                meta = {}
        trade_sets.append({
            "position_id": r[0],
            "symbol": r[1],
            "side": r[2],
            "entry_price": float(r[3]) if r[3] is not None else None,
            "exit_price": float(r[4]) if r[4] is not None else None,
            "quantity": float(r[5]) if r[5] is not None else None,
            "pnl": float(r[6]) if r[6] is not None else None,
            "pnl_percent": float(r[7]) if r[7] is not None else None,
            "open_time": r[8].isoformat() if r[8] else None,
            "close_time": r[9].isoformat() if r[9] else None,
            "duration_minutes": r[10] if len(r) > 10 else None,
            "strategy": meta.get("strategy") or meta.get("strategy_name") or "-",
            "timeframe": meta.get("timeframe") or "-",
            "exit_reason": meta.get("exit_reason") or "-",
        })
    return {"success": True, "trade_sets": trade_sets, "count": len(trade_sets)}


class DeleteTradeSetsRequest(BaseModel):
    position_ids: List[str] = []


@app.post("/api/realtime/trade-sets/delete")
async def delete_realtime_trade_sets(req: DeleteTradeSetsRequest):
    """선택한 거래 세트 삭제 (position_id 목록, realtime_trading만 허용)"""
    position_ids = [str(pid).strip() for pid in (req.position_ids or []) if pid]

    if not position_ids:
        return {"success": False, "message": "삭제할 항목을 선택하세요.", "deleted": 0}
    if not app_state.get('positions_repo') or not hasattr(app_state['positions_repo'], 'engine'):
        return {"success": False, "message": "DB 연결 없음", "deleted": 0}
    db_engine = app_state['positions_repo'].engine
    try:
        placeholders = ", ".join([f":id{i}" for i in range(len(position_ids))])
        q = text(f"""
            DELETE FROM tradebot_positions
            WHERE position_id IN ({placeholders})
              AND COALESCE(source, 'binance') = 'realtime_trading'
        """)
        params = {f"id{i}": str(pid) for i, pid in enumerate(position_ids)}
        with db_engine.connect() as conn:
            result = conn.execute(q, params)
            deleted = result.rowcount
            conn.commit()
        logger.info(f"거래세트 {deleted}건 삭제 완료: {position_ids[:5]}{'...' if len(position_ids) > 5 else ''}")
        return {"success": True, "message": f"{deleted}건 삭제됨", "deleted": deleted}
    except Exception as e:
        logger.error(f"거래세트 삭제 실패: {e}", exc_info=True)
        return {"success": False, "message": str(e), "deleted": 0}
async def get_realtime_logs(engine_id: Optional[str] = Query(None)):
    """실시간 로그 조회"""
    try:
        # engine_id가 없으면 모든 엔진의 로그 반환
        if not engine_id:
            all_logs = {}
            for eid, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    all_logs[eid] = engine.logs
            return {
                "success": True,
                "logs": all_logs
            }
        
        if engine_id not in app_state['realtime_engines']:
            return {
                "success": False,
                "message": f"엔진을 찾을 수 없습니다: {engine_id}"
            }

        engine = app_state['realtime_engines'][engine_id]
        return {
            "success": True,
            "engine_id": engine_id,
            "logs": engine.logs
        }

    except Exception as e:
        logger.error(f"로그 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/realtime/trade-history")
async def get_trade_history(engine_id: Optional[str] = Query(None)):
    """거래 히스토리 조회 (메모리 + DB, 여러 엔진 지원)"""
    try:
        trade_history = []
        
        # engine_id가 없으면 모든 엔진의 히스토리 반환
        if not engine_id:
            all_history = {}
            all_trades_flat = []  # 모든 거래를 평탄화한 리스트 (히스토리 페이지 호환성)
            
            # 실행 중인 엔진의 메모리 히스토리
            for eid, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    engine_history = engine.trade_history or []
                    all_history[eid] = {
                        'trade_history': engine_history,
                        'symbol': engine.symbol,
                        'strategy': engine.strategy_name,
                        'timeframe': engine.timeframe
                    }
                    all_trades_flat.extend(engine_history)
            
            # (DB 조회 제거: 메모리 히스토리만 사용)
            # 각 엔진별로 시간순 정렬
            for eid in all_history:
                all_history[eid]['trade_history'].sort(key=lambda x: x.get('timestamp') or x.get('time') or '', reverse=True)
            
            # 평탄화된 리스트도 시간순 정렬
            all_trades_flat.sort(key=lambda x: x.get('timestamp') or x.get('time') or '', reverse=True)
            
            return {
                "success": True,
                "engines": all_history,
                "trade_history": all_trades_flat,  # 히스토리 페이지 호환성
                "count": len(all_history)
            }
        
        # 특정 엔진의 히스토리 조회
        if engine_id not in app_state['realtime_engines']:
            return {
                "success": False,
                "message": f"엔진을 찾을 수 없습니다: {engine_id}",
                "trade_history": []
            }
        
        engine = app_state['realtime_engines'][engine_id]
        memory_history = engine.trade_history or []
        trade_history.extend(memory_history)
        
        # (DB 조회 제거: 메모리 히스토리만 사용)
        # 시간순 정렬 (최신순)
        trade_history.sort(key=lambda x: x.get('timestamp') or x.get('time') or '', reverse=True)
        
        return {
            "success": True,
            "engine_id": engine_id,
            "trade_history": trade_history,
            "symbol": engine.symbol,
            "strategy": engine.strategy_name,
            "timeframe": engine.timeframe
        }

    except Exception as e:
        logger.error(f"거래 히스토리 조회 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"조회 실패: {str(e)}")


@app.get("/api/chart-data/{symbol}")
async def get_chart_data(symbol: str, timeframe: str = "15m", limit: int = 500):
    """차트 캔들 데이터 조회 (마감된 캔들은 DB에서, 진행 중인 캔들은 실시간 엔진에서)"""
    try:
        # 심볼 정규화: ETHUSDT.P -> ETHUSDT (DB에는 .P 없이 저장됨)
        db_symbol = symbol.replace('.P', '').upper()
        
        logger.debug(f"차트 데이터 조회: 원본 심볼={symbol}, DB 심볼={db_symbol}, 타임프레임={timeframe}")
        
        # 실행 중인 실시간 엔진 찾기 (진행 중인 캔들용)
        current_engine = None
        for engine_id, engine in app_state['realtime_engines'].items():
            if engine.is_running and engine.symbol == db_symbol and engine.timeframe == timeframe:
                current_engine = engine
                logger.info(f"📊 실시간 엔진 발견: {engine_id}")
                break
        
        # DB에서 마감된 캔들 가져오기
        if not app_state['positions_repo'] or not hasattr(app_state['positions_repo'], 'engine'):
            raise HTTPException(status_code=500, detail="DB 연결이 없습니다")
        
        db_engine = app_state['positions_repo'].engine
        
        # 마감된 캔들 조회 (DB에서)
        # PostgreSQL의 timestamp without time zone은 서버의 timezone 설정에 따라 해석됩니다.
        # 명시적으로 UTC로 변환하여 조회
        query = text("""
            SELECT 
                open_time,
                open, high, low, close, volume
            FROM candles
            WHERE symbol = :symbol AND tf = :timeframe
            ORDER BY open_time DESC
            LIMIT :limit
        """)
        
        logger.info(f"📊 DB에서 차트 데이터 조회 시작: symbol={db_symbol}, timeframe={timeframe}, limit={limit}")
        
        # PostgreSQL의 timestamp without time zone은 시간대 정보가 없으므로
        # pandas가 로컬 시간대(KST)로 해석할 수 있습니다.
        # 따라서 명시적으로 UTC로 변환해야 합니다.
        df = pd.read_sql(query, db_engine, params={"symbol": db_symbol, "timeframe": timeframe, "limit": limit})
        
        logger.info(f"📊 DB에서 조회된 캔들 개수: {len(df)}개")
        if len(df) > 0:
            logger.info(f"📊 DB 첫 캔들: {df.iloc[0]['open_time']}, 마지막 캔들: {df.iloc[-1]['open_time']}")
        
        if len(df) == 0:
            raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다")
        
        df = df.sort_values('open_time')
        closed_candles = []
        
        for _, row in df.iterrows():
            open_time = row['open_time']
            
            # 타임스탬프 변환 (UTC 기준으로 통일)
            # PostgreSQL의 timestamp without time zone은 시간대 정보가 없으므로
            # pandas가 로컬 시간대(KST, UTC+9)로 해석할 수 있습니다.
            # DB에 저장된 시간은 UTC이므로, 로컬 시간대로 해석된 경우 9시간을 빼야 합니다.
            
            if isinstance(open_time, pd.Timestamp):
                # pandas Timestamp 처리
                if open_time.tz is None:
                    # naive datetime: pandas가 로컬 시간대(KST)로 해석했을 수 있음
                    # DB에 저장된 시간이 UTC라고 가정하고, 로컬 시간대에서 9시간을 빼서 UTC로 변환
                    # 또는 명시적으로 UTC로 가정
                    # PostgreSQL의 timestamp without time zone은 UTC로 저장되었다고 가정
                    open_time_utc = open_time.tz_localize('UTC')
                else:
                    # timezone-aware: UTC로 변환
                    open_time_utc = open_time.tz_convert('UTC')
                timestamp = int(open_time_utc.timestamp())
            elif hasattr(open_time, 'timestamp'):
                # datetime 객체 처리
                if open_time.tzinfo is None:
                    # naive datetime: UTC로 가정
                    # PostgreSQL의 timestamp without time zone은 UTC로 저장되었다고 가정
                    open_time_utc = open_time.replace(tzinfo=timezone.utc)
                else:
                    # timezone-aware: UTC로 변환
                    open_time_utc = open_time.astimezone(timezone.utc)
                timestamp = int(open_time_utc.timestamp())
            else:
                # 다른 타입 (문자열 등)
                if isinstance(open_time, str):
                    # 문자열 파싱
                    if 'Z' in open_time or '+00:00' in open_time:
                        open_time = datetime.fromisoformat(open_time.replace('Z', '+00:00'))
                        timestamp = int(open_time.timestamp())
                    else:
                        # 시간대 정보가 없으면 UTC로 가정
                        open_time = datetime.fromisoformat(open_time)
                        open_time_utc = open_time.replace(tzinfo=timezone.utc)
                        timestamp = int(open_time_utc.timestamp())
                else:
                    # 다른 타입은 timestamp 메서드가 있다고 가정
                    timestamp = int(open_time.timestamp())
            
            candle_data = {
                'time': timestamp,  # Unix timestamp (초 단위, UTC 기준)
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume'])
            }
            closed_candles.append(candle_data)
            
            # 첫 캔들과 마지막 캔들만 로그 출력 (너무 많으면 로그가 과도함)
            if len(closed_candles) == 1 or len(closed_candles) == len(df):
                logger.debug(f"📊 변환된 캔들: time={candle_data['time']} ({datetime.fromtimestamp(candle_data['time'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC), O={candle_data['open']:.2f}, C={candle_data['close']:.2f}")
        
        # 실시간 엔진에서 진행 중인 캔들 가져오기
        realtime_candle = None
        if current_engine and hasattr(current_engine, 'current_candle') and current_engine.current_candle:
            try:
                current_candle = current_engine.current_candle
                # is_closed가 False인 경우만 (진행 중인 캔들)
                if not current_candle.get('is_closed', True):
                    open_time = current_candle.get('open_time')
                    timestamp = 0
                    
                    if open_time is None:
                        pass  # skip
                    else:
                        # datetime 타입 체크 (파일 상단에서 import한 datetime 사용)
                        if isinstance(open_time, datetime):
                            if open_time.tzinfo is not None:
                                timestamp = int(open_time.astimezone(timezone.utc).timestamp())
                            else:
                                # naive datetime은 UTC로 가정
                                timestamp = int(open_time.timestamp())
                        elif hasattr(open_time, 'timestamp'):
                            timestamp = int(open_time.timestamp())
                        
                        if timestamp > 0:
                            realtime_candle = {
                                'time': timestamp,
                                'open': float(current_candle.get('open', 0)),
                                'high': float(current_candle.get('high', 0)),
                                'low': float(current_candle.get('low', 0)),
                                'close': float(current_candle.get('close', 0)),
                                'volume': float(current_candle.get('volume', 0))
                            }
                            logger.info(f"📊 진행 중인 캔들 추가: time={timestamp}, close={realtime_candle['close']}")
            except Exception as e:
                logger.warning(f"진행 중인 캔들 변환 실패: {e}")
        
        # 마감된 캔들과 진행 중인 캔들 합치기
        all_candles = closed_candles.copy()
        
        # 진행 중인 캔들이 있고, 마지막 마감된 캔들보다 최신이면 추가
        if realtime_candle:
            # 마지막 마감된 캔들의 시간 확인
            if len(closed_candles) > 0:
                last_closed_time = closed_candles[-1]['time']
                # 진행 중인 캔들이 마지막 마감된 캔들보다 최신이면 추가
                if realtime_candle['time'] > last_closed_time:
                    all_candles.append(realtime_candle)
                    logger.info(f"📊 진행 중인 캔들을 차트에 추가: {realtime_candle['time']}")
                elif realtime_candle['time'] == last_closed_time:
                    # 같은 시간이면 진행 중인 캔들로 교체 (더 최신 데이터)
                    all_candles[-1] = realtime_candle
                    logger.info(f"📊 마지막 캔들을 진행 중인 캔들로 교체: {realtime_candle['time']}")
            else:
                # 마감된 캔들이 없으면 진행 중인 캔들만 추가
                all_candles.append(realtime_candle)
        
        # 시간순 정렬
        all_candles.sort(key=lambda x: x['time'])
        
        # 디버깅: 첫 캔들과 마지막 캔들의 시간 확인
        if len(all_candles) > 0:
            first_time = datetime.fromtimestamp(all_candles[0]['time'], tz=timezone.utc)
            last_time = datetime.fromtimestamp(all_candles[-1]['time'], tz=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            time_diff = (now_utc - last_time).total_seconds() / 3600  # 시간 차이
            
            # KST 시간도 함께 표시 (UTC+9)
            from datetime import timedelta
            kst_offset = timedelta(hours=9)
            first_time_kst = first_time + kst_offset
            last_time_kst = last_time + kst_offset
            now_kst = now_utc + kst_offset
            
            # 시간 차이를 분 단위로도 표시
            time_diff_minutes = (now_utc - last_time).total_seconds() / 60
            
            logger.info(f"📊 차트 데이터 시간 범위 (UTC): {first_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {last_time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"📊 차트 데이터 시간 범위 (KST): {first_time_kst.strftime('%Y-%m-%d %H:%M:%S')} ~ {last_time_kst.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"📊 현재 시간 (UTC): {now_utc.strftime('%Y-%m-%d %H:%M:%S')}, (KST): {now_kst.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"📊 마지막 캔들과 차이: {time_diff:.2f}시간 ({time_diff_minutes:.1f}분)")
            logger.info(f"📊 마감된 캔들: {len(closed_candles)}개, 진행 중인 캔들: {1 if realtime_candle else 0}개, 총: {len(all_candles)}개")
            
            # 첫 캔들과 마지막 캔들의 상세 정보
            if len(all_candles) > 0:
                first_candle_utc = datetime.fromtimestamp(all_candles[0]['time'], tz=timezone.utc)
                first_candle_kst = first_candle_utc + kst_offset
                last_candle_utc = datetime.fromtimestamp(all_candles[-1]['time'], tz=timezone.utc)
                last_candle_kst = last_candle_utc + kst_offset
                logger.info(f"📊 첫 캔들: time={all_candles[0]['time']} ({first_candle_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC / {first_candle_kst.strftime('%Y-%m-%d %H:%M:%S')} KST), O={all_candles[0]['open']:.2f}, H={all_candles[0]['high']:.2f}, L={all_candles[0]['low']:.2f}, C={all_candles[0]['close']:.2f}")
                logger.info(f"📊 마지막 캔들: time={all_candles[-1]['time']} ({last_candle_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC / {last_candle_kst.strftime('%Y-%m-%d %H:%M:%S')} KST), O={all_candles[-1]['open']:.2f}, H={all_candles[-1]['high']:.2f}, L={all_candles[-1]['low']:.2f}, C={all_candles[-1]['close']:.2f}")
            
            # 갭이 있으면 (마지막 캔들이 현재 시간보다 과거이면) 빈 캔들 채우기
            if time_diff > 0:  # 마지막 캔들이 현재보다 과거
                # 타임프레임에 따른 캔들 길이 계산
                tf_minutes = {
                    '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
                    '1h': 60, '4h': 240, '1d': 1440
                }
                tf_min = tf_minutes.get(timeframe, 5)
                candle_length_seconds = tf_min * 60
                
                # 현재 캔들 시작 시간 계산
                current_candle_start_utc = now_utc.replace(minute=(now_utc.minute // tf_min) * tf_min, second=0, microsecond=0)
                current_candle_start_timestamp = int(current_candle_start_utc.timestamp())
                
                last_candle_timestamp = all_candles[-1]['time']
                time_diff_seconds = current_candle_start_timestamp - last_candle_timestamp
                
                # 갭이 있으면 (1개 이상의 캔들이 누락됨)
                if time_diff_seconds > candle_length_seconds:
                    missing_count = int(time_diff_seconds / candle_length_seconds)
                    logger.info(f"📊 갭 감지: 마지막 캔들({last_candle_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC)과 현재 캔들({current_candle_start_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC) 사이에 {missing_count}개 캔들 누락, 갭 채우기 시작...")
                    
                    # 누락된 캔들들을 생성
                    next_candle_time = last_candle_timestamp + candle_length_seconds
                    prev_close = all_candles[-1]['close']
                    filled_candles = []
                    
                    while next_candle_time < current_candle_start_timestamp:
                        missing_candle = {
                            'time': next_candle_time,
                            'open': prev_close,
                            'high': prev_close,
                            'low': prev_close,
                            'close': prev_close,
                        }
                        filled_candles.append(missing_candle)
                        prev_close = prev_close
                        next_candle_time += candle_length_seconds
                    
                    # 현재 진행 중인 캔들 추가 (실시간 엔진에서 가져온 캔들이 없으면)
                    if not realtime_candle:
                        current_price = prev_close  # 기본값
                        if current_engine and hasattr(current_engine, 'current_price') and current_engine.current_price:
                            current_price = current_engine.current_price
                        
                        new_candle = {
                            'time': current_candle_start_timestamp,
                            'open': prev_close,
                            'high': current_price,
                            'low': current_price,
                            'close': current_price,
                        }
                        filled_candles.append(new_candle)
                        logger.info(f"📊 현재 진행 중인 캔들 추가: {current_candle_start_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                    
                    # 채운 캔들들을 all_candles에 추가
                    all_candles.extend(filled_candles)
                    all_candles.sort(key=lambda x: x['time'])
                    
                    logger.info(f"📊 갭 채우기 완료: {len(filled_candles)}개 캔들 추가, 총 {len(all_candles)}개 캔들")
        
        # 반환할 캔들 데이터 (최신 limit개만)
        result_candles = all_candles[-limit:] if len(all_candles) > limit else all_candles
        
        logger.info(f"📊 최종 반환 캔들 개수: {len(result_candles)}개 (전체: {len(all_candles)}개, limit: {limit})")
        if len(result_candles) > 0:
            first_result = datetime.fromtimestamp(result_candles[0]['time'], tz=timezone.utc)
            last_result = datetime.fromtimestamp(result_candles[-1]['time'], tz=timezone.utc)
            logger.info(f"📊 반환 캔들 시간 범위: {first_result.strftime('%Y-%m-%d %H:%M:%S')} UTC ~ {last_result.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        
        return {
            "symbol": db_symbol,  # 정규화된 심볼 반환
            "timeframe": timeframe,
            "candles": result_candles,  # 최신 limit개만
            "count": len(all_candles)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"차트 데이터 조회 실패: {e}", exc_info=True)
        import traceback
        logger.error(f"상세 에러: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"차트 데이터 조회 실패: {str(e)}")


@app.post("/api/realtime/manual-entry")
async def manual_entry(request: Dict = Body(...)):
    """수동 진입 (엔진 ID 기반)"""
    try:
        engine_id = request.get('engine_id')
        
        # engine_id가 없으면 첫 번째 실행 중인 엔진 사용
        if not engine_id:
            for eid, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    engine_id = eid
                    break
        
        if not engine_id or engine_id not in app_state['realtime_engines']:
            raise HTTPException(status_code=400, detail="실시간 모니터링이 실행 중이 아닙니다")

        engine = app_state['realtime_engines'][engine_id]
        if not engine.is_running:
            raise HTTPException(status_code=400, detail="실시간 모니터링이 실행 중이 아닙니다")

        side = request.get('side')  # 'BUY' or 'SELL'
        quantity = request.get('quantity')  # Optional
        risk_percent = request.get('risk_percent')  # Optional
        stop_loss = request.get('stop_loss')  # Optional
        take_profit = request.get('take_profit')  # Optional

        if not side:
            raise HTTPException(status_code=400, detail="side가 필요합니다 ('BUY' 또는 'SELL')")

        result = await engine.manual_entry(
            side=side,
            quantity=quantity,
            risk_percent=risk_percent,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        
        result['engine_id'] = engine_id
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"수동 진입 실패: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"수동 진입 실패: {str(e)}")


@app.websocket("/ws/realtime")
async def websocket_realtime(websocket: WebSocket):
    """실시간 업데이트 WebSocket"""
    await websocket.accept()
    app_state['websocket_connections'].append(websocket)
    logger.info(f"🔌 실시간 트레이딩 WebSocket 연결 (총 {len(app_state['websocket_connections'])}개)")

    try:
        while True:
            # 클라이언트로부터 메시지 수신 (ping/pong)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                pass

            # 모든 엔진의 상태 업데이트 전송
            for engine_id, engine in app_state['realtime_engines'].items():
                if engine.is_running:
                    status = engine.get_status()
                    await websocket.send_json({
                        "type": "status_update",
                        "engine_id": engine_id,
                        "data": status
                    })

            await asyncio.sleep(1)  # 1초마다 업데이트

    except WebSocketDisconnect as e:
        # 정상적인 WebSocket 종료 (클라이언트가 연결을 끊음)
        # WebSocketDisconnect는 일반적으로 정상 종료를 의미
        logger.debug(f"🔌 WebSocket 정상 종료: {e}")
    except Exception as e:
        # 예외 메시지에서 종료 코드 확인
        error_str = str(e)
        
        # 정상 종료 코드 (1000, 1001)를 포함하는 경우
        if '1000' in error_str or '1001' in error_str or '(1001' in error_str:
            # 정상 종료로 간주 (DEBUG 레벨)
            logger.debug(f"🔌 WebSocket 정상 종료: {e}")
        else:
            # 실제 오류만 ERROR로 로깅
            logger.error(f"WebSocket 오류: {e}", exc_info=True)
    finally:
        if websocket in app_state['websocket_connections']:
            app_state['websocket_connections'].remove(websocket)
        logger.info(f"🔌 실시간 트레이딩 WebSocket 연결 종료 (남은 연결: {len(app_state['websocket_connections'])}개)")


if __name__ == "__main__":
    import uvicorn
    logger.info("🌐 실시간 트레이딩 API 서버 시작 (포트 8886)")
    uvicorn.run(app, host="0.0.0.0", port=8886, log_level="info")
