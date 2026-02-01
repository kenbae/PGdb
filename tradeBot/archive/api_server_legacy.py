"""
FastAPI 백엔드

자동매매 시스템 API 서버
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import List, Dict, Optional
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
import queue
import os
import math

# 프로젝트 모듈
import sys
_current_dir = os.path.dirname(os.path.abspath(__file__))
_pgdb_dir = os.path.dirname(_current_dir)  # PGdb 폴더
sys.path.insert(0, _current_dir)
sys.path.insert(0, _pgdb_dir)  # 공용 폴더 접근용

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv()

import requests
import html as html_module

from core.config_loader import get_config
# 공용 전략 폴더 사용 (PGdb/strategies/)
from strategies import create_strategy_manager
from sqlalchemy import create_engine
import pandas as pd
from database.watched_symbols_repo import WatchedSymbolsRepo
from database.signals_repo import SignalsRepo
from database.positions_repo import PositionsRepo
from services.order_client import OrderClient

# 로그 큐 (WebSocket으로 전송하기 위해)
log_queue = queue.Queue(maxsize=1000)

# 커스텀 로그 핸들러
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
            # 큐가 꽉 차면 오래된 것 제거
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
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 큐 핸들러 추가 (모든 로거에)
queue_handler = QueueHandler()
queue_handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s'))
logging.getLogger().addHandler(queue_handler)

# 전역 변수
config = None
strategy_manager = None
exchange = None  # 거래소 인스턴스
db_engine = None  # psycopg2 → SQLAlchemy
watched_symbols_repo = None  # 감시 심볼 Repository
signals_repo = None  # 신호 Repository
positions_repo = None  # 포지션 Repository
order_client = None  # 주문 클라이언트 (주문 서버로 요청)
active_connections: List[WebSocket] = []

# 상태
class AppState:
    def __init__(self):
        self.signals = []
        self.positions = []
        self.last_update = None
        self.watched_symbols = []  # DB에서 로드
        self.timeframe = '15m'
        self.analysis_interval = 300  # 5분

app_state = AppState()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기 관리"""
    global config, strategy_manager, exchange, db_engine, watched_symbols_repo, signals_repo, positions_repo, order_client

    # 시작
    logger.info("🚀 서버 시작 중...")

    # Config 로드
    config = get_config()
    logger.info("✅ Config 로드")

    # DB 연결 (SQLAlchemy)
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    db_engine = create_engine(db_url)
    logger.info("✅ DB 연결")

    # Repository 초기화
    watched_symbols_repo = WatchedSymbolsRepo(db_engine)
    logger.info("✅ 감시 심볼 Repository 초기화")

    signals_repo = SignalsRepo(db_engine)
    logger.info("✅ 신호 Repository 초기화")

    positions_repo = PositionsRepo(db_engine)
    logger.info("✅ 포지션 Repository 초기화")

    # 거래소 초기화
    try:
        from exchanges.binance_live import BinanceLive
        import os

        api_key = os.getenv('BINANCE_LIVE_API_KEY')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET')

        if api_key and api_secret:
            exchange = BinanceLive(api_key=api_key, api_secret=api_secret)
            logger.info("✅ 바이낸스 거래소 연결")
        else:
            logger.warning("⚠️  바이낸스 API 키 없음 - 동기화 기능 비활성화")
    except Exception as e:
        logger.error(f"❌ 거래소 초기화 실패: {e}")

    # DB에서 감시 심볼 로드
    symbols = watched_symbols_repo.get_symbols_list(enabled_only=True)
    if symbols:
        app_state.watched_symbols = symbols
        logger.info(f"✅ DB에서 감시 심볼 로드: {symbols}")
    else:
        # DB에 데이터가 없으면 기본값 사용
        app_state.watched_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
        logger.info(f"⚠️  DB에 감시 심볼 없음 - 기본값 사용: {app_state.watched_symbols}")

    # 전략 초기화 - config.yaml 기반 동적 로드
    raw_config = config.config if hasattr(config, 'config') else {}
    strategy_manager = create_strategy_manager(raw_config)
    logger.info(f"✅ 전략 초기화 완료: {strategy_manager.list_strategies()}")

    # 주문 클라이언트 초기화 (주문 서버로 요청)
    order_client = OrderClient(order_server_url="http://localhost:8889")

    # 주문 서버 연결 확인
    order_server_healthy = await order_client.health_check()
    if order_server_healthy:
        logger.info("✅ 주문 서버 연결 성공 (포트 8889)")
    else:
        logger.warning("⚠️  주문 서버 연결 실패 - 주문 기능 비활성화")

    # 백그라운드 분석 시작
    asyncio.create_task(background_analyzer())
    logger.info("✅ 백그라운드 분석 시작")

    # 백그라운드 결과 업데이트 - 수동 실행으로 변경
    # asyncio.create_task(background_result_updater())
    # logger.info("✅ 백그라운드 결과 업데이트 시작")
    logger.info("ℹ️  신호 결과 업데이트: 수동 시뮬레이션 모드 (신호 페이지에서 실행)")

    logger.info("🎉 서버 준비 완료!")

    # 서버 시작 텔레그램 알림
    send_server_startup_notification()

    yield

    # 종료
    if order_client:
        await order_client.close()
        logger.info("✅ 주문 클라이언트 종료")

    if db_engine:
        db_engine.dispose()
        logger.info("✅ DB 연결 종료")


# FastAPI 앱
app = FastAPI(
    title="Auto Trading Dashboard",
    description="ICT + AI 자동매매 시스템",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REST API 엔드포인트
# ============================================================

@app.get("/")
async def root():
    """루트 - 대시보드 페이지"""
    static_dir = Path(__file__).parent / "static"
    index_file = static_dir / "index.html"
    
    if index_file.exists():
        return FileResponse(index_file)
    else:
        return HTMLResponse(content="""
        <html>
            <head><title>Auto Trading Dashboard</title></head>
            <body>
                <h1>Auto Trading Dashboard</h1>
                <p>static/index.html 파일을 생성하세요.</p>
            </body>
        </html>
        """)


@app.get("/signals")
async def signals_page():
    """신호 리스트 페이지"""
    static_dir = Path(__file__).parent / "static"
    signals_file = static_dir / "signals.html"

    if signals_file.exists():
        return FileResponse(signals_file)
    else:
        return HTMLResponse(content="<h1>signals.html 파일을 찾을 수 없습니다</h1>")


@app.get("/positions")
async def positions_page():
    """마이 포지션 페이지"""
    static_dir = Path(__file__).parent / "static"
    positions_file = static_dir / "positions.html"

    if positions_file.exists():
        return FileResponse(positions_file)
    else:
        return HTMLResponse(content="<h1>positions.html 파일을 찾을 수 없습니다</h1>")


@app.get("/api/health")
async def health_check():
    """헬스 체크"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/signals/current")
async def get_current_signals():
    """현재 메모리 상의 신호 목록 (최근 분석 결과)"""
    signals_data = []
    for s in app_state.signals:
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
        
        # AI 분석 정보 추가
        if 'ai_analysis' in s.metadata:
            ai = s.metadata['ai_analysis']
            signal_dict['ai_analysis'] = {
                'decision': ai.get('decision'),
                'confidence': ai.get('confidence'),
                'reasoning': ai.get('reasoning'),
                'risk_assessment': ai.get('risk_assessment'),
                'market_context': ai.get('market_context'),
                'rejected': ai.get('rejected', False)
            }
        
        signals_data.append(signal_dict)
    
    return {
        "signals": signals_data,
        "count": len(app_state.signals),
        "last_update": app_state.last_update.isoformat() if app_state.last_update else None
    }


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
    
    return {
        "strategies": strategies,
        "count": len(strategies)
    }


@app.post("/api/strategies/{strategy_name}/toggle")
async def toggle_strategy(strategy_name: str):
    """전략 on/off"""
    strategy = strategy_manager.get_strategy(strategy_name)
    
    if not strategy:
        raise HTTPException(status_code=404, detail="전략을 찾을 수 없습니다")
    
    strategy.enabled = not strategy.enabled
    
    return {
        "strategy": strategy_name,
        "enabled": strategy.enabled
    }


@app.post("/api/analyze/{symbol}")
@app.get("/api/analyze/{symbol}")
async def analyze_symbol(
    symbol: str,
    timeframe: str = "15m",
    include_rejected: bool = False,
    strategies: str = None
):
    """특정 심볼 분석"""
    try:
        # 전략 필터 파싱
        strategy_filter = None
        if strategies:
            strategy_filter = [s.strip() for s in strategies.split(',') if s.strip()]
            logger.info(f"🎯 선택된 전략: {strategy_filter}")

        # 1. 최신 캔들 업데이트
        logger.info(f"📡 {symbol} 최신 캔들 업데이트 중...")
        await update_latest_candles([symbol], timeframe)

        # 2. 데이터 로드
        query = """
            SELECT open_time, open, high, low, close, volume, symbol, tf
            FROM candles
            WHERE symbol = %s AND tf = %s
            ORDER BY open_time DESC
            LIMIT 500
        """

        df = pd.read_sql(query, db_engine, params=(symbol, timeframe))

        if len(df) == 0:
            raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다")

        df = df.sort_values('open_time').reset_index(drop=True)

        # 3. 분석 (전략 필터 적용)
        results = strategy_manager.analyze_all(symbol, timeframe, df, strategy_filter=strategy_filter)
        
        signals = []
        for strategy_name, strategy_signals in results.items():
            for signal in strategy_signals:
                signal_data = {
                    "strategy": signal.strategy_name,
                    "symbol": signal.symbol,
                    "signal_type": signal.signal_type,
                    "entry_price": signal.entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit_1": signal.take_profit_1,
                    "confidence": signal.confidence,
                    "risk_reward": signal.risk_reward,
                    "reasons": signal.reasons
                }
                
                # AI 분석 정보 포함
                if 'ai_analysis' in signal.metadata:
                    ai = signal.metadata['ai_analysis']
                    signal_data['ai_analysis'] = {
                        'decision': ai.get('decision'),
                        'confidence': ai.get('confidence'),
                        'reasoning': ai.get('reasoning'),
                        'risk_assessment': ai.get('risk_assessment'),
                        'market_context': ai.get('market_context'),
                        'rejected': ai.get('rejected', False)
                    }
                    
                    # 거부된 신호 필터링
                    if ai.get('rejected') and not include_rejected:
                        continue
                
                signals.append(signal_data)

        # 결과 로그
        if signals:
            logger.info(f"✅ {symbol} 분석 완료: {len(signals)}개 신호 발견")
            for sig in signals:
                logger.info(f"   - {sig['strategy']}: {sig['signal_type'].upper()} @ ${sig['entry_price']:.2f}")
        else:
            logger.info(f"⏸️ {symbol} 분석 완료: 신호 없음 (전략 필터: {strategy_filter or '모두'})")

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "signals": signals,
            "count": len(signals),
            "strategies_analyzed": strategy_filter or "all"
        }

    except Exception as e:
        logger.error(f"분석 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/symbols")
async def get_symbols():
    """저장된 심볼 목록"""
    try:
        query = "SELECT DISTINCT symbol FROM candles ORDER BY symbol"
        df = pd.read_sql(query, db_engine)
        symbols = df['symbol'].tolist()
        
        return {
            "symbols": symbols,
            "count": len(symbols)
        }
    
    except Exception as e:
        logger.error(f"심볼 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watched-symbols")
async def get_watched_symbols():
    """자동 분석 중인 심볼 목록 (DB에서 조회)"""
    try:
        # DB에서 전체 정보 조회
        watched_list = watched_symbols_repo.get_all(enabled_only=False)

        return {
            "symbols": app_state.watched_symbols,  # 현재 활성화된 심볼
            "watched_list": watched_list,  # 전체 감시 심볼 정보
            "timeframe": app_state.timeframe,
            "interval": app_state.analysis_interval,
            "count": len(app_state.watched_symbols)
        }
    except Exception as e:
        logger.error(f"감시 심볼 조회 실패: {e}")
        # 에러 발생 시 메모리 상태 반환
        return {
            "symbols": app_state.watched_symbols,
            "timeframe": app_state.timeframe,
            "interval": app_state.analysis_interval,
            "count": len(app_state.watched_symbols)
        }


@app.post("/api/watched-symbols/add")
async def add_watched_symbol(symbol: str, timeframe: str = "15m"):
    """감시 심볼 추가 (DB에 저장)"""
    try:
        # DB에 추가
        success = watched_symbols_repo.add(symbol, timeframe, enabled=True)

        if success:
            # 메모리 상태 업데이트
            if symbol not in app_state.watched_symbols:
                app_state.watched_symbols.append(symbol)

            logger.info(f"✅ 감시 심볼 추가: {symbol} ({timeframe})")

            return {
                "success": True,
                "symbol": symbol,
                "timeframe": timeframe,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            return {
                "success": False,
                "message": "DB 저장 실패",
                "watched_symbols": app_state.watched_symbols
            }

    except Exception as e:
        logger.error(f"감시 심볼 추가 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/remove")
async def remove_watched_symbol(symbol: str):
    """감시 심볼 제거 (DB에서 삭제)"""
    try:
        # DB에서 삭제
        success = watched_symbols_repo.remove(symbol)

        if success:
            # 메모리 상태 업데이트
            if symbol in app_state.watched_symbols:
                app_state.watched_symbols.remove(symbol)

            logger.info(f"🗑️  감시 심볼 제거: {symbol}")

            return {
                "success": True,
                "symbol": symbol,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            return {
                "success": False,
                "message": "감시 목록에 없는 심볼입니다",
                "watched_symbols": app_state.watched_symbols
            }

    except Exception as e:
        logger.error(f"감시 심볼 제거 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watched-symbols/with-strategies")
async def get_watched_symbols_with_strategies():
    """전략 정보를 포함한 감시 심볼 목록"""
    try:
        watched_list = watched_symbols_repo.get_all_with_strategies(enabled_only=False)

        return {
            "symbols": app_state.watched_symbols,
            "watched_list": watched_list,
            "count": len(watched_list)
        }
    except Exception as e:
        logger.error(f"감시 심볼 (전략 포함) 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/sync-from-backtest")
async def sync_from_backtest():
    """백테스트 watch_symbols에서 심볼+전략 동기화"""
    try:
        result = watched_symbols_repo.sync_from_backtest_watch_symbols()

        if result.get('added', 0) > 0:
            # 메모리 상태 업데이트
            for symbol in result.get('symbols', []):
                if symbol not in app_state.watched_symbols:
                    app_state.watched_symbols.append(symbol)

            logger.info(f"✅ 백테스트에서 {result['added']}개 심볼 동기화")

        return {
            "success": True,
            "added": result.get('added', 0),
            "symbols": result.get('symbols', []),
            "watched_symbols": app_state.watched_symbols
        }
    except Exception as e:
        logger.error(f"백테스트 동기화 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/add-with-strategy")
async def add_watched_symbol_with_strategy(
    symbol: str,
    strategies: str = "",
    timeframe: str = "15m",
    notes: str = ""
):
    """전략 정보와 함께 감시 심볼 추가"""
    try:
        success = watched_symbols_repo.add_with_strategy(
            symbol=symbol,
            strategies=strategies,
            timeframe=timeframe,
            enabled=True,
            notes=notes
        )

        if success:
            if symbol not in app_state.watched_symbols:
                app_state.watched_symbols.append(symbol)

            logger.info(f"✅ 감시 심볼 추가 (전략: {strategies}): {symbol}")

            return {
                "success": True,
                "symbol": symbol,
                "strategies": strategies,
                "watched_symbols": app_state.watched_symbols
            }
        else:
            return {
                "success": False,
                "message": "추가 실패",
                "watched_symbols": app_state.watched_symbols
            }

    except Exception as e:
        logger.error(f"감시 심볼 추가 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-symbols/update-strategies")
async def update_symbol_strategies(symbol: str, strategies: str):
    """심볼의 전략 정보 업데이트"""
    try:
        success = watched_symbols_repo.update_strategies(symbol, strategies)

        if success:
            logger.info(f"✅ 전략 업데이트: {symbol} → {strategies}")
            return {
                "success": True,
                "symbol": symbol,
                "strategies": strategies
            }
        else:
            return {
                "success": False,
                "message": "심볼을 찾을 수 없습니다"
            }

    except Exception as e:
        logger.error(f"전략 업데이트 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade")
async def execute_trade(
    symbol: str,
    side: str,  # 'buy' or 'sell'
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    quantity: Optional[float] = None,
    leverage: Optional[int] = None,
    signal_id: Optional[str] = None
):
    """
    거래 실행 (주문 서버로 요청)

    신호 분석 서버에서 주문 서버로 주문을 전달합니다.
    """
    try:
        # 입력 검증
        if side not in ['buy', 'sell']:
            raise HTTPException(status_code=400, detail="side는 'buy' 또는 'sell'이어야 합니다")

        logger.info(f"🚀 거래 실행 요청:")
        logger.info(f"   심볼: {symbol}")
        logger.info(f"   방향: {side.upper()}")
        logger.info(f"   진입: ${entry_price:,.2f}")
        logger.info(f"   손절: ${stop_loss:,.2f}")
        logger.info(f"   익절: ${take_profit:,.2f}")

        # 주문 서버가 연결되어 있는지 확인
        if not order_client:
            logger.warning("⚠️  주문 서버 연결 없음 - 데모 모드로 실행")
            return {
                "success": True,
                "message": "주문이 실행되었습니다 (데모 모드 - 주문 서버 미연결)",
                "order": {
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "quantity": quantity or 0.001,
                    "status": "demo",
                    "order_id": f"DEMO_{symbol}_{int(datetime.now().timestamp())}"
                }
            }

        # 주문 서버로 주문 요청
        result = await order_client.create_order(
            symbol=symbol,
            side=side,
            order_type='market',  # 시장가 주문
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=leverage,
            signal_id=signal_id,
            strategy='ICT+AI'
        )

        if result.get('success'):
            logger.info(f"✅ 주문 성공: {result.get('message')}")
        else:
            logger.error(f"❌ 주문 실패: {result.get('error')}")

        return result

    except Exception as e:
        logger.error(f"❌ 거래 실행 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart-data/{symbol}")
async def get_chart_data(symbol: str, timeframe: str = "15m", limit: int = 100):
    """차트 데이터 조회"""
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
        
        # 차트 데이터 형식으로 변환
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
        
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": candles,
            "count": len(candles)
        }
    
    except Exception as e:
        logger.error(f"차트 데이터 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def get_positions():
    """활성 포지션 조회 (현재는 데모)"""
    # TODO: 실제 포지션 조회
    return {
        "positions": [],
        "count": 0
    }


# ============================================================
# 신호 관리 API
# ============================================================

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
    """
    신호 리스트 조회

    Args:
        limit: 최대 개수 (기본 100)
        offset: 오프셋 (페이징용)
        symbol: 심볼 필터
        timeframe: 타임프레임 필터
        signal_type: 'buy' or 'sell'
        ai_decision: 'approve', 'reject', 'caution'
        result_status: 'pending', 'tp1_hit', 'tp2_hit', 'sl_hit'
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        신호 리스트
    """
    try:
        # 날짜 파싱
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        # DB에서 조회
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

        # 전체 개수 조회 (동일한 필터 적용, limit/offset 제외)
        total_count = signals_repo.get_signals_count(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=signal_type,
            ai_decision=ai_decision,
            result_status=result_status,
            start_date=start_dt,
            end_date=end_dt
        )

        # NaN 값 정리 (JSON 직렬화 오류 방지)
        for signal in signals:
            for key in ['pnl', 'pnl_percent', 'r_multiple', 'max_favorable_excursion', 'max_adverse_excursion']:
                if key in signal and signal[key] is not None:
                    if math.isnan(signal[key]) or math.isinf(signal[key]):
                        signal[key] = 0.0

        return {
            "signals": signals,
            "count": total_count,  # 전체 개수
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
    """
    신호 통계 조회

    Args:
        symbol: 심볼 필터
        timeframe: 타임프레임 필터
        days: 최근 N일

    Returns:
        통계 정보
    """
    try:
        stats = signals_repo.get_statistics(
            symbol=symbol,
            timeframe=timeframe,
            days=days
        )

        return stats

    except Exception as e:
        logger.error(f"신호 통계 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/signals/simulate")
async def simulate_signals(
    capital: float,
    risk_percent: float,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    signal_type: Optional[str] = None,
    ai_decision: Optional[str] = None,
    result_status: Optional[str] = None
):
    """
    신호 결과 시뮬레이션 (수동 실행)

    Args:
        capital: 투자금 ($)
        risk_percent: 리스크 퍼센트 (%)
        symbol: 심볼 필터
        timeframe: 타임프레임 필터
        signal_type: 'buy' or 'sell'
        ai_decision: 'approve', 'reject', 'caution'
        result_status: 결과 상태 필터

    Returns:
        시뮬레이션 결과
    """
    try:
        logger.info(f"🎯 신호 시뮬레이션 시작: capital=${capital}, risk={risk_percent}%")

        # 필터에 맞는 pending 신호 조회
        filters = {
            'symbol': symbol,
            'timeframe': timeframe,
            'signal_type': signal_type,
            'ai_decision': ai_decision
        }

        # result_status가 지정되지 않았으면 pending만
        if not result_status:
            filters['result_status'] = 'pending'
        else:
            filters['result_status'] = result_status

        # None 값 제거
        filters = {k: v for k, v in filters.items() if v is not None}

        signals = signals_repo.get_signals(limit=1000, offset=0, **filters)

        if not signals:
            return {
                "success": True,
                "message": "시뮬레이션할 신호가 없습니다",
                "updated": 0,
                "total": 0
            }

        logger.info(f"📋 {len(signals)}개 신호에 대해 시뮬레이션 실행 중...")
        updated_count = 0

        for signal in signals:
            try:
                signal_id = signal['signal_id']
                symbol = signal['symbol']
                signal_type = signal['signal_type']
                entry_price = float(signal['entry_price'])
                stop_loss = float(signal['stop_loss'])
                take_profit_1 = signal.get('take_profit_1')
                take_profit_2 = signal.get('take_profit_2')

                # timezone 정보 제거 (호환성 보장)
                created_at = signal['created_at']
                if hasattr(created_at, 'tzinfo') and created_at.tzinfo:
                    created_at = created_at.replace(tzinfo=None)

                # 최신 캔들 조회 (신호 생성 이후)
                query = """
                    SELECT open_time, high, low, close
                    FROM candles
                    WHERE symbol = %s
                      AND tf = %s
                      AND open_time >= %s
                    ORDER BY open_time ASC
                """

                timeframe = signal['timeframe']
                df = pd.read_sql(query, db_engine, params=(symbol, timeframe, created_at))

                if len(df) == 0:
                    continue

                # TP/SL 체크
                status = None
                exit_price = None
                exit_time = None
                mfe = 0.0  # Max Favorable Excursion
                mae = 0.0  # Max Adverse Excursion

                for _, row in df.iterrows():
                    high = float(row['high'])
                    low = float(row['low'])
                    timestamp = row['open_time']

                    # timezone 정보 제거 (호환성 보장)
                    if hasattr(timestamp, 'tzinfo') and timestamp.tzinfo:
                        timestamp = timestamp.replace(tzinfo=None)

                    if signal_type == 'buy':
                        # MFE/MAE 계산
                        current_mfe = ((high - entry_price) / entry_price) * 100
                        if current_mfe > mfe:
                            mfe = current_mfe

                        current_mae = ((entry_price - low) / entry_price) * 100
                        if current_mae > mae:
                            mae = current_mae

                        # TP2 체크 (우선)
                        if take_profit_2 and high >= float(take_profit_2):
                            status = 'tp2_hit'
                            exit_price = float(take_profit_2)
                            exit_time = timestamp
                            break

                        # TP1 체크
                        if take_profit_1 and high >= float(take_profit_1):
                            status = 'tp1_hit'
                            exit_price = float(take_profit_1)
                            exit_time = timestamp
                            break

                        # SL 체크
                        if low <= stop_loss:
                            status = 'sl_hit'
                            exit_price = stop_loss
                            exit_time = timestamp
                            break

                    else:  # sell
                        # MFE/MAE 계산
                        current_mfe = ((entry_price - low) / entry_price) * 100
                        if current_mfe > mfe:
                            mfe = current_mfe

                        current_mae = ((high - entry_price) / entry_price) * 100
                        if current_mae > mae:
                            mae = current_mae

                        # TP2 체크 (우선)
                        if take_profit_2 and low <= float(take_profit_2):
                            status = 'tp2_hit'
                            exit_price = float(take_profit_2)
                            exit_time = timestamp
                            break

                        # TP1 체크
                        if take_profit_1 and low <= float(take_profit_1):
                            status = 'tp1_hit'
                            exit_price = float(take_profit_1)
                            exit_time = timestamp
                            break

                        # SL 체크
                        if high >= stop_loss:
                            status = 'sl_hit'
                            exit_price = stop_loss
                            exit_time = timestamp
                            break

                # 결과 DB 업데이트
                if status and exit_price:
                    # TP/SL에 도달한 경우 - PnL 계산
                    if signal_type == 'buy':
                        pnl = exit_price - entry_price
                        pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                        risk = entry_price - stop_loss
                    else:  # sell
                        pnl = entry_price - exit_price
                        pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                        risk = stop_loss - entry_price

                    # R-multiple 계산
                    r_multiple = pnl / risk if risk > 0 else 0

                    # Duration 계산 (분) - 이미 timezone 제거됨
                    duration_minutes = int((exit_time - created_at).total_seconds() / 60)

                    # NaN 값을 0.0으로 대체 (JSON 직렬화 오류 방지)
                    if math.isnan(mfe) or math.isinf(mfe):
                        mfe = 0.0
                    if math.isnan(mae) or math.isinf(mae):
                        mae = 0.0
                    if math.isnan(r_multiple) or math.isinf(r_multiple):
                        r_multiple = 0.0
                    if math.isnan(pnl_percent) or math.isinf(pnl_percent):
                        pnl_percent = 0.0
                    if math.isnan(pnl) or math.isinf(pnl):
                        pnl = 0.0

                    # 결과 업데이트
                    result_data = {
                        'signal_id': signal_id,
                        'status': status,
                        'exit_price': exit_price,
                        'exit_time': exit_time,
                        'pnl': pnl,
                        'pnl_percent': pnl_percent,
                        'r_multiple': r_multiple,
                        'max_favorable_excursion': mfe,
                        'max_adverse_excursion': mae,
                        'duration_minutes': duration_minutes,
                        'is_simulation': True,
                        'notes': f"Manual simulation: ${capital} @ {risk_percent}% risk"
                    }

                    if signals_repo.save_result(result_data):
                        updated_count += 1
                        logger.info(f"✅ {signal_id}: {status} @ ${exit_price:,.2f} (PnL: {pnl_percent:+.2f}%, R: {r_multiple:.2f})")
                else:
                    # TP/SL에 아직 도달하지 않은 경우 - pending 상태로 기록
                    # MFE/MAE 정리
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
                        'max_favorable_excursion': mfe,
                        'max_adverse_excursion': mae,
                        'duration_minutes': None,
                        'is_simulation': True,
                        'notes': f"Pending - No TP/SL hit yet (simulation: ${capital} @ {risk_percent}%)"
                    }

                    if signals_repo.save_result(result_data):
                        updated_count += 1
                        logger.info(f"⏳ {signal_id}: still pending (MFE: {mfe:.2f}%, MAE: {mae:.2f}%)")

            except Exception as e:
                logger.error(f"❌ 신호 시뮬레이션 실패 ({signal.get('signal_id', 'unknown')}): {e}")
                continue

        logger.info(f"📊 시뮬레이션 완료: {updated_count}/{len(signals)}개")

        return {
            "success": True,
            "message": f"{updated_count}개 신호 시뮬레이션 완료",
            "updated": updated_count,
            "total": len(signals)
        }

    except Exception as e:
        logger.error(f"❌ 신호 시뮬레이션 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "message": f"시뮬레이션 실패: {str(e)}",
            "updated": 0,
            "total": 0
        }


# ============================================================
# 포지션 히스토리 API
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
    """
    포지션 리스트 조회

    Args:
        limit: 최대 개수 (기본 100)
        offset: 오프셋 (페이징용)
        symbol: 심볼 필터
        side: 'buy', 'sell' 필터
        status: 'open', 'closed', 'liquidated' 필터
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        포지션 리스트
    """
    try:
        # 날짜 파싱
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        # DB에서 조회
        positions = positions_repo.get_positions(
            limit=limit,
            offset=offset,
            symbol=symbol,
            side=side,
            status=status,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "positions": positions,
            "count": len(positions),
            "limit": limit,
            "offset": offset
        }

    except Exception as e:
        logger.error(f"포지션 리스트 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/stats")
async def get_positions_statistics(
    symbol: Optional[str] = None,
    days: int = 30
):
    """
    포지션 통계 조회

    Args:
        symbol: 심볼 필터
        days: 최근 N일

    Returns:
        통계 정보
    """
    try:
        stats = positions_repo.get_statistics(
            symbol=symbol,
            days=days
        )

        return stats

    except Exception as e:
        logger.error(f"포지션 통계 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/daily-pnl")
async def get_positions_daily_pnl(
    symbol: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    일별 손익 조회

    Args:
        symbol: 심볼 필터
        days: 최근 N일
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        일별 손익 리스트
    """
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        daily_pnl = positions_repo.get_daily_pnl(
            symbol=symbol,
            days=days,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "daily_pnl": daily_pnl,
            "count": len(daily_pnl)
        }

    except Exception as e:
        logger.error(f"일별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/hourly-pnl")
async def get_hourly_pnl(
    symbol: Optional[str] = None,
    days: int = 7,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    시간별 손익 조회

    Args:
        symbol: 심볼 필터
        days: 최근 N일
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        시간별 손익 리스트
    """
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        hourly_pnl = positions_repo.get_hourly_pnl(
            symbol=symbol,
            days=days,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "hourly_pnl": hourly_pnl,
            "count": len(hourly_pnl)
        }

    except Exception as e:
        logger.error(f"시간별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/monthly-pnl")
async def get_monthly_pnl(
    symbol: Optional[str] = None,
    months: int = 12,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    월별 손익 조회

    Args:
        symbol: 심볼 필터
        months: 최근 N개월
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        월별 손익 리스트
    """
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        monthly_pnl = positions_repo.get_monthly_pnl(
            symbol=symbol,
            months=months,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "monthly_pnl": monthly_pnl,
            "count": len(monthly_pnl)
        }

    except Exception as e:
        logger.error(f"월별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/symbol-pnl")
async def get_symbol_pnl(days: int = 30):
    """
    심볼별 손익 조회

    Args:
        days: 최근 N일

    Returns:
        심볼별 손익 리스트
    """
    try:
        symbol_pnl = positions_repo.get_symbol_pnl(days=days)

        return {
            "symbol_pnl": symbol_pnl,
            "count": len(symbol_pnl)
        }

    except Exception as e:
        logger.error(f"심볼별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/weekday-pnl")
async def get_weekday_pnl(
    symbol: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    요일별 손익 조회

    Args:
        symbol: 심볼 필터 (옵션)
        days: 최근 N일
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        요일별 손익 리스트
    """
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        weekday_pnl = positions_repo.get_weekday_pnl(
            symbol=symbol,
            days=days,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "weekday_pnl": weekday_pnl,
            "count": len(weekday_pnl)
        }

    except Exception as e:
        logger.error(f"요일별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/hour-pnl")
async def get_hour_pnl(
    symbol: Optional[str] = None,
    days: int = 30,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    시간대별 손익 조회

    Args:
        symbol: 심볼 필터 (옵션)
        days: 최근 N일
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        시간대별 손익 리스트
    """
    try:
        start_dt = datetime.fromisoformat(start_date) if start_date else None
        end_dt = datetime.fromisoformat(end_date) if end_date else None

        hour_pnl = positions_repo.get_hour_pnl(
            symbol=symbol,
            days=days,
            start_date=start_dt,
            end_date=end_dt
        )

        return {
            "hour_pnl": hour_pnl,
            "count": len(hour_pnl)
        }

    except Exception as e:
        logger.error(f"시간대별 손익 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions/{position_id}")
async def get_position_detail(position_id: str):
    """포지션 상세 조회"""
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


@app.post("/api/positions/sync-binance")
async def sync_binance_positions(days: int = 30):
    """바이낸스에서 거래 히스토리 가져와서 DB에 저장"""
    try:
        # exchange가 초기화되었는지 확인
        if not exchange:
            raise HTTPException(
                status_code=503,
                detail="거래소가 연결되지 않았습니다. .env 파일에서 BINANCE_LIVE_API_KEY와 BINANCE_LIVE_API_SECRET을 확인하세요."
            )

        from exchanges.binance_live import BinanceLive
        from services.binance_sync import BinanceSyncService

        # BinanceLive 인스턴스인지 확인
        if not isinstance(exchange, BinanceLive):
            raise HTTPException(status_code=400, detail="바이낸스 거래소만 지원합니다")

        # 동기화 서비스 초기화
        sync_service = BinanceSyncService(exchange, positions_repo)

        # 동기화 실행
        result = sync_service.sync_positions(days=days)

        if not result['success']:
            raise HTTPException(status_code=500, detail=result['message'])

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 바이낸스 동기화 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals/{signal_id}")
async def get_signal_detail(signal_id: str):
    """신호 상세 조회"""
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


# ============================================================
# 수동 AI 검증 API
# ============================================================

from ai.ollama_analyzer import OLLAMAAnalyzer

# AI 분석기 (지연 초기화)
_ai_analyzer = None

def get_ai_analyzer():
    """AI 분석기 인스턴스 반환 (싱글톤)"""
    global _ai_analyzer
    if _ai_analyzer is None:
        _ai_analyzer = OLLAMAAnalyzer(
            host=config.get('ollama.host', 'http://localhost:11434'),
            model=config.get('ollama.llm_model', 'qwen2.5:7b-instruct')
        )
    return _ai_analyzer


@app.post("/api/ai/verify")
async def verify_signal_with_ai(signal_data: dict):
    """
    수동 AI 검증

    Request body:
    {
        "symbol": "BTCUSDT",
        "signal_type": "buy",
        "entry_price": 92750.0,
        "stop_loss": 92450.0,
        "take_profit_1": 93235.0,
        "confidence": 0.785,
        "risk_reward": 2.15,
        "reasons": ["bullish_ob", "bullish_fvg"]
    }
    """
    try:
        ai_analyzer = get_ai_analyzer()

        # 신호 데이터 구성
        signal_dict = {
            'signal_type': signal_data.get('signal_type', 'unknown'),
            'symbol': signal_data.get('symbol', 'UNKNOWN'),
            'entry_price': signal_data.get('entry_price', 0),
            'stop_loss': signal_data.get('stop_loss', 0),
            'take_profit_1': signal_data.get('take_profit_1', 0),
            'confidence': signal_data.get('confidence', 0.5),
            'risk_reward': signal_data.get('risk_reward', 1.0),
            'confluence': signal_data.get('reasons', [])
        }

        # 시장 데이터 (현재가 = 진입가로 가정)
        market_data = {
            'current_price': signal_data.get('entry_price', 0),
            'trend': '알 수 없음',
            'volatility': '보통'
        }

        logger.info(f"🤖 수동 AI 검증 시작: {signal_dict['symbol']} {signal_dict['signal_type'].upper()}")

        # AI 분석 실행
        ai_result = ai_analyzer.analyze_signal(signal_dict, market_data)

        result = {
            'success': True,
            'decision': ai_result.decision,
            'confidence': ai_result.confidence,
            'reasoning': ai_result.reasoning,
            'risk_assessment': ai_result.risk_assessment,
            'market_context': ai_result.market_context,
            'adjustments': {
                'entry': ai_result.adjusted_entry,
                'stop': ai_result.adjusted_stop,
                'target': ai_result.adjusted_target
            },
            'model': ai_result.model,
            'analyzed_at': ai_result.analyzed_at.isoformat() if ai_result.analyzed_at else None
        }

        logger.info(f"✅ AI 검증 완료: {ai_result.decision.upper()} (신뢰도: {ai_result.confidence:.1%})")

        return result

    except Exception as e:
        logger.error(f"❌ AI 검증 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 바이낸스 선물 거래 API
# ============================================================

from pydantic import BaseModel

class OrderRequest(BaseModel):
    symbol: str
    side: str  # 'BUY' or 'SELL'
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_percent: float = 2.0  # 기본 2%
    leverage: int = 10  # 기본 10배


@app.get("/api/futures/balance")
async def get_futures_balance():
    """선물 계정 잔고 조회 (실제 바이낸스 API)"""
    try:
        import ccxt
        import os
        
        # 환경변수에서 Binance API 키 로드
        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')
        
        if not api_key or not api_secret:
            raise HTTPException(
                status_code=400, 
                detail="API 키가 설정되지 않았습니다. .env 파일에서 BINANCE_LIVE_API_KEY와 BINANCE_LIVE_API_SECRET을 설정하세요."
            )
        
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'  # 선물 거래
            }
        })
        
        # 마켓 정보 로드
        exchange.load_markets()
        
        # 실제 잔고 조회
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {})
        
        total = usdt_balance.get('total', 0)
        free = usdt_balance.get('free', 0)
        used = usdt_balance.get('used', 0)
        
        logger.info(f"💰 잔고 조회: 총 ${total:.2f}, 사용가능 ${free:.2f}, 사용중 ${used:.2f}")
        
        return {
            "balance": total,
            "available": free,
            "used": used,
            "demo": False
        }
    
    except Exception as e:
        logger.error(f"❌ 잔고 조회 실패: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"잔고 조회 실패: {str(e)}"
        )


@app.get("/api/futures/positions")
async def get_futures_positions():
    """현재 포지션 조회"""
    try:
        import ccxt
        import os
        
        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')
        
        if not api_key or not api_secret:
            return []
        
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # 마켓 정보 로드
        exchange.load_markets()
        
        # 포지션 조회
        positions = exchange.fapiPrivateV2GetPositionRisk()
        
        # 포지션이 있는 것만 필터링
        active_positions = []
        for pos in positions:
            amt = float(pos.get('positionAmt', 0))
            if amt != 0:
                symbol = pos.get('symbol')
                entry_price = float(pos.get('entryPrice', 0))
                mark_price = float(pos.get('markPrice', 0))
                unrealized_pnl = float(pos.get('unRealizedProfit', 0))
                
                # 해당 심볼의 오픈 오더 조회 (TP/SL)
                tp_price = None
                sl_price = None
                
                try:
                    logger.info(f"🔍 {symbol} 포지션의 TP/SL 검색 중... (현재가: {mark_price})")
                    
                    # 심볼별로 오픈 오더 조회
                    open_orders = exchange.fetch_open_orders(symbol)
                    logger.info(f"📋 {symbol} 오픈 오더 {len(open_orders)}개 조회됨")
                    
                    # 디버깅: 전체 오더 JSON 출력
                    if len(open_orders) > 0:
                        import json
                        logger.info(f"🔍 오픈 오더 전체 데이터:\n{json.dumps(open_orders, indent=2, default=str)}")
                    
                    for order in open_orders:
                        ord_symbol = order.get('symbol', '')
                        is_reduce_only = order.get('reduceOnly', False)
                        order_type = order.get('type', '').upper()
                        
                        # stopPrice 또는 price 사용
                        stop_price = order.get('stopPrice')
                        if not stop_price:
                            stop_price = order.get('price')
                        if not stop_price:
                            stop_price = order.get('triggerPrice')
                        
                        logger.info(f"  → 오더: {order_type}, reduceOnly={is_reduce_only}, stopPrice={stop_price}")
                        
                        if is_reduce_only and stop_price:
                            stop_price = float(stop_price)
                            # LONG 포지션
                            if amt > 0:
                                if order_type == 'TAKE_PROFIT_MARKET' or order_type == 'TAKE_PROFIT' or stop_price > mark_price:
                                    tp_price = stop_price
                                    logger.info(f"  ✅ TP 설정: {tp_price}")
                                elif order_type == 'STOP_MARKET' or order_type == 'STOP_LOSS_MARKET' or order_type == 'STOP' or stop_price < mark_price:
                                    sl_price = stop_price
                                    logger.info(f"  ✅ SL 설정: {sl_price}")
                            # SHORT 포지션
                            else:
                                if order_type == 'TAKE_PROFIT_MARKET' or order_type == 'TAKE_PROFIT' or stop_price < mark_price:
                                    tp_price = stop_price
                                    logger.info(f"  ✅ TP 설정: {tp_price}")
                                elif order_type == 'STOP_MARKET' or order_type == 'STOP_LOSS_MARKET' or order_type == 'STOP' or stop_price > mark_price:
                                    sl_price = stop_price
                                    logger.info(f"  ✅ SL 설정: {sl_price}")
                
                except Exception as e:
                    logger.error(f"❌ {symbol} 오픈 오더 조회 실패: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                
                active_positions.append({
                    'symbol': symbol,
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'amount': abs(amt),
                    'entry_price': entry_price,
                    'mark_price': mark_price,
                    'liquidation_price': float(pos.get('liquidationPrice', 0)),
                    'leverage': int(pos.get('leverage', 1)),
                    'unrealized_pnl': unrealized_pnl,
                    'pnl_percent': (unrealized_pnl / (entry_price * abs(amt))) * 100 if entry_price > 0 else 0,
                    'tp_price': tp_price,
                    'sl_price': sl_price
                })
                
                logger.info(f"📊 {symbol}: TP={tp_price}, SL={sl_price}")
        
        return active_positions
    
    except Exception as e:
        logger.error(f"포지션 조회 실패: {e}")
        return []


@app.post("/api/futures/calculate")
async def calculate_position(order: OrderRequest):
    """포지션 크기 계산 (실제 주문 없이)"""
    try:
        # 잔고 조회
        balance_data = await get_futures_balance()
        available = balance_data['available']
        
        # 리스크 금액 = 잔고 * 리스크 퍼센트
        risk_amount = available * (order.risk_percent / 100)
        
        # 손절폭 (%)
        price_diff = abs(order.entry_price - order.stop_loss)
        stop_loss_percent = (price_diff / order.entry_price) * 100
        
        # 포지션 크기 = (리스크 금액 / 손절폭 %) * 레버리지
        position_value = risk_amount / (stop_loss_percent / 100)
        position_size = position_value / order.entry_price
        
        # 실제 거래 금액 (레버리지 포함)
        margin_required = position_value / order.leverage
        
        return {
            'available_balance': available,
            'risk_amount': risk_amount,
            'risk_percent': order.risk_percent,
            'leverage': order.leverage,
            'position_size': position_size,
            'position_value': position_value,
            'margin_required': margin_required,
            'stop_loss_percent': stop_loss_percent,
            'entry_price': order.entry_price,
            'stop_loss': order.stop_loss,
            'take_profit': order.take_profit
        }
    
    except Exception as e:
        logger.error(f"포지션 계산 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/futures/order")
async def create_futures_order(order: OrderRequest):
    """선물 주문 생성"""
    try:
        import ccxt
        import os
        
        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')
        
        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="API 키가 설정되지 않았습니다")
        
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # 마켓 정보 로드 (필수!)
        exchange.load_markets()
        
        # 포지션 계산
        calc = await calculate_position(order)
        position_size = calc['position_size']
        
        # 마켓 정보 가져오기
        market = exchange.market(order.symbol)
        min_amount = market['limits']['amount']['min']

        # 소수점 자리 조정
        amount_precision = market['precision']['amount']

        if amount_precision is not None:
            position_size = round(position_size, int(amount_precision))

        # 반올림 후 최소 수량 체크 (중요!)
        if position_size < min_amount:
            logger.warning(f"⚠️ 포지션 크기 {position_size}가 최소값 {min_amount}보다 작음. 최소값으로 조정.")
            position_size = min_amount

        # 현재 가격 조회 (notional value 체크용)
        ticker = exchange.fetch_ticker(order.symbol)
        current_price = ticker['last']

        # Notional value 체크 (최소 100 USDT)
        min_notional = market['limits']['cost']['min'] if market['limits']['cost']['min'] else 100
        notional_value = position_size * current_price

        if notional_value < min_notional:
            logger.warning(f"⚠️ Notional value ${notional_value:.2f}가 최소값 ${min_notional}보다 작음.")
            # 최소 notional을 만족하도록 포지션 크기 증가
            position_size = min_notional / current_price

            # 다시 정밀도 조정
            if amount_precision is not None:
                position_size = round(position_size, int(amount_precision))

            # 재계산된 notional value
            notional_value = position_size * current_price
            logger.info(f"✅ 포지션 크기를 {position_size}로 조정 (notional: ${notional_value:.2f})")

        # 가격 정밀도 조정 (바이낸스 규칙에 맞게)
        # 바이낸스는 tickSize 사용
        tick_size = market.get('info', {}).get('filters', [])
        price_precision = None
        
        for f in tick_size:
            if f.get('filterType') == 'PRICE_FILTER':
                tick = float(f.get('tickSize', 0))
                if tick > 0:
                    # tickSize로부터 소수점 자리 계산
                    # 0.01 -> 2, 0.001 -> 3, 0.0001 -> 4
                    import math
                    price_precision = abs(int(math.log10(tick)))
                    logger.info(f"📏 가격 tickSize: {tick}, precision: {price_precision}")
                break
        
        # precision이 없으면 기본값 사용
        if price_precision is None:
            price_precision = market['precision'].get('price', 2)
            if price_precision is not None:
                price_precision = int(price_precision)
            else:
                price_precision = 2
        
        # 가격 반올림
        stop_loss_price = round(order.stop_loss, price_precision)
        take_profit_price = round(order.take_profit, price_precision)
        
        logger.info(f"📊 주문 생성: {order.symbol} {order.side} {position_size}")
        logger.info(f"💰 진입: ${order.entry_price}, 손절: ${stop_loss_price} (prec: {price_precision}), 익절: ${take_profit_price}")
        logger.info(f"⚡ 레버리지: {order.leverage}x, 리스크: {order.risk_percent}%")
        
        # 1. 레버리지 설정
        logger.info(f"🔧 레버리지 설정: {order.leverage}x")
        exchange.fapiPrivatePostLeverage({
            'symbol': order.symbol,
            'leverage': order.leverage
        })
        
        # 2. 시장가 주문
        logger.info(f"📈 시장가 주문: {order.side} {position_size}")
        main_order = exchange.create_order(
            symbol=order.symbol,
            type='MARKET',
            side=order.side,
            amount=position_size
        )
        
        logger.info(f"✅ 진입 주문 완료: {main_order.get('id', 'N/A')}")
        
        # 3. 손절 주문 (Stop Market)
        stop_side = 'SELL' if order.side == 'BUY' else 'BUY'
        logger.info(f"🛑 손절 주문: {stop_side} @ ${stop_loss_price}")
        
        try:
            stop_order = exchange.create_order(
                symbol=order.symbol,
                type='STOP_MARKET',
                side=stop_side,
                amount=position_size,
                params={
                    'stopPrice': stop_loss_price,
                    'reduceOnly': True
                }
            )
            logger.info(f"✅ 손절 주문 완료: {stop_order.get('id', 'N/A')}")
        except Exception as e:
            logger.error(f"❌ 손절 주문 실패: {e}")
            stop_order = {'error': str(e)}
        
        # 4. 익절 주문 (Take Profit Market)
        logger.info(f"🎯 익절 주문: {stop_side} @ ${take_profit_price}")
        
        try:
            tp_order = exchange.create_order(
                symbol=order.symbol,
                type='TAKE_PROFIT_MARKET',
                side=stop_side,
                amount=position_size,
                params={
                    'stopPrice': take_profit_price,
                    'reduceOnly': True
                }
            )
            logger.info(f"✅ 익절 주문 완료: {tp_order.get('id', 'N/A')}")
        except Exception as e:
            logger.error(f"❌ 익절 주문 실패: {e}")
            tp_order = {'error': str(e)}
        
        logger.info(f"✅ 주문 완료: {main_order['id']}")
        
        return {
            'success': True,
            'main_order': main_order,
            'stop_order': stop_order,
            'tp_order': tp_order,
            'calculation': calc
        }
    
    except Exception as e:
        logger.error(f"❌ 주문 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/futures/close/{symbol}")
async def close_futures_position(symbol: str):
    """포지션 청산 (마켓)"""
    try:
        import ccxt
        import os
        
        api_key = os.getenv('BINANCE_LIVE_API_KEY') or config.get('binance.api_key', '')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET') or config.get('binance.api_secret', '')
        
        if not api_key or not api_secret:
            raise HTTPException(status_code=400, detail="API 키가 설정되지 않았습니다")
        
        exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # 마켓 정보 로드
        exchange.load_markets()
        
        # 현재 포지션 확인
        positions = exchange.fapiPrivateV2GetPositionRisk({'symbol': symbol})
        
        if not positions:
            raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다")
        
        pos = positions[0]
        amt = float(pos.get('positionAmt', 0))
        
        if amt == 0:
            raise HTTPException(status_code=400, detail="포지션이 없습니다")
        
        # 반대 방향으로 시장가 주문
        side = 'SELL' if amt > 0 else 'BUY'
        amount = abs(amt)
        
        logger.info(f"🛑 포지션 청산: {symbol} {side} {amount}")
        
        # 시장가 청산
        order = exchange.create_order(
            symbol=symbol,
            type='MARKET',
            side=side,
            amount=amount,
            params={'reduceOnly': True}
        )
        
        logger.info(f"✅ 청산 완료: {order['id']}")
        
        return {
            'success': True,
            'order': order
        }
    
    except Exception as e:
        logger.error(f"❌ 포지션 청산 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watched-settings")
async def update_watched_settings(
    symbols: Optional[List[str]] = None,
    timeframe: Optional[str] = None,
    interval: Optional[int] = None
):
    """감시 설정 업데이트"""
    if symbols is not None:
        app_state.watched_symbols = symbols
        logger.info(f"✅ 감시 심볼 업데이트: {symbols}")
    
    if timeframe is not None:
        app_state.timeframe = timeframe
        logger.info(f"✅ 타임프레임 업데이트: {timeframe}")
    
    if interval is not None:
        app_state.analysis_interval = interval
        logger.info(f"✅ 분석 주기 업데이트: {interval}초")
    
    return {
        "success": True,
        "watched_symbols": app_state.watched_symbols,
        "timeframe": app_state.timeframe,
        "interval": app_state.analysis_interval
    }


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
            # 클라이언트 메시지 대기
            data = await websocket.receive_text()
            
            # ping/pong
            if data == "ping":
                await websocket.send_text("pong")
    
    except WebSocketDisconnect:
        active_connections.remove(websocket)
        logger.info("WebSocket 연결 종료")


@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """실시간 로그 WebSocket"""
    await websocket.accept()
    logger.info("로그 WebSocket 연결")
    
    try:
        while True:
            # 큐에서 로그 가져오기
            try:
                log_entry = log_queue.get(timeout=1)
                await websocket.send_json(log_entry)
            except queue.Empty:
                # 큐가 비어있으면 대기
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"로그 전송 실패: {e}")
                break
    except WebSocketDisconnect:
        logger.info("로그 WebSocket 연결 해제")


async def broadcast_signals(signals: List):
    """모든 연결된 클라이언트에 신호 브로드캐스트"""
    if not active_connections:
        return
    
    # 신호 데이터 직렬화 (AI 분석 포함)
    signals_data = []
    for s in signals:
        signal_dict = {
            "strategy": s.strategy_name,
            "symbol": s.symbol,
            "signal_type": s.signal_type,
            "entry_price": s.entry_price,
            "stop_loss": s.stop_loss,
            "take_profit_1": s.take_profit_1,
            "confidence": s.confidence,
            "risk_reward": s.risk_reward,
            "reasons": s.reasons,
            "timeframe": getattr(s, 'timeframe', '15m')
        }
        
        # AI 분석 정보 추가
        if 'ai_analysis' in s.metadata:
            ai = s.metadata['ai_analysis']
            signal_dict['ai_analysis'] = {
                'decision': ai.get('decision'),
                'confidence': ai.get('confidence'),
                'reasoning': ai.get('reasoning'),
                'risk_assessment': ai.get('risk_assessment'),
                'market_context': ai.get('market_context'),
                'rejected': ai.get('rejected', False)
            }
        
        signals_data.append(signal_dict)
    
    message = json.dumps({
        "type": "signals",
        "data": signals_data,
        "timestamp": datetime.now().isoformat()
    })
    
    for connection in active_connections:
        try:
            await connection.send_text(message)
        except Exception as e:
            logger.error(f"WebSocket 전송 실패: {e}")


async def update_latest_candles(symbols: List[str], timeframe: str):
    """
    WebSocket으로 최신 캔들 업데이트
    
    Args:
        symbols: 심볼 리스트
        timeframe: 타임프레임
    
    Returns:
        업데이트된 심볼 수
    """
    try:
        from exchanges.binance_live import BinanceLive
        exchange = BinanceLive()
        
        updated_count = 0
        
        for symbol in symbols:
            try:
                # 1. DB에서 최신 캔들 시간 확인
                query = """
                    SELECT MAX(open_time) as last_time
                    FROM candles
                    WHERE symbol = %s AND tf = %s
                """
                
                result = pd.read_sql(query, db_engine, params=(symbol, timeframe))
                last_time = result['last_time'].iloc[0]
                
                if pd.isna(last_time):
                    logger.warning(f"⚠️  {symbol} 데이터 없음 - 초기 데이터 필요")
                    continue
                
                # 2. Binance 선물 API로 최신 캔들 가져오기
                logger.debug(f"📡 {symbol} 최신 캔들 가져오는 중...")

                import ccxt
                # 선물 마켓으로 설정 (run_ingest.py와 동일하게)
                binance = ccxt.binance({
                    'enableRateLimit': True,
                    'options': {'defaultType': 'future'}  # USDT-M 선물
                })

                # 심볼 형식 변환: BTCUSDT -> BTC/USDT:USDT
                ccxt_symbol = symbol
                if not '/' in symbol:
                    # BTCUSDT -> BTC/USDT:USDT 형식으로 변환
                    if symbol.endswith('USDT'):
                        base = symbol[:-4]
                        ccxt_symbol = f"{base}/USDT:USDT"

                # 최근 10개 캔들 (마지막 것이 현재 진행중인 캔들)
                ohlcv = binance.fetch_ohlcv(
                    ccxt_symbol,
                    timeframe=timeframe,
                    limit=10
                )

                # 3. DB에 없는 캔들만 삽입
                new_candles = []
                for candle in ohlcv:
                    candle_time = pd.Timestamp(candle[0], unit='ms', tz='UTC')

                    # 이미 있는 캔들은 건너뛰기
                    if candle_time <= last_time:
                        continue

                    new_candles.append({
                        'exchange': 'binance',
                        'market': 'usdtm',  # 선물 마켓 (run_ingest.py와 동일)
                        'open_time': candle_time,
                        'open': candle[1],
                        'high': candle[2],
                        'low': candle[3],
                        'close': candle[4],
                        'volume': candle[5],
                        'symbol': symbol,
                        'tf': timeframe
                    })
                
                # 4. DB에 삽입
                if new_candles:
                    df = pd.DataFrame(new_candles)
                    df.to_sql('candles', db_engine, if_exists='append', index=False)
                    logger.info(f"✅ {symbol}: {len(new_candles)}개 새 캔들 추가")
                    updated_count += 1
                else:
                    logger.debug(f"✓ {symbol}: 최신 상태")
                
            except Exception as e:
                logger.error(f"❌ {symbol} 캔들 업데이트 실패: {e}")
                continue
        
        return updated_count
    
    except Exception as e:
        logger.error(f"❌ 캔들 업데이트 전체 실패: {e}")
        return 0


# ============================================================
# 백그라운드 작업
# ============================================================

def save_signal_to_db(signal, symbol: str, timeframe: str) -> bool:
    """
    신호를 DB에 저장

    Args:
        signal: TradeSignal 객체
        symbol: 심볼
        timeframe: 타임프레임

    Returns:
        성공 여부
    """
    try:
        # signal_id 생성 (symbol_timestamp)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        signal_id = f"{symbol}_{timeframe}_{signal.signal_type}_{timestamp}"

        # AI 분석 정보 추출
        ai_analysis = signal.metadata.get('ai_analysis', {})

        # 신호 데이터 구성
        signal_data = {
            'signal_id': signal_id,
            'strategy_name': signal.strategy_name,
            'symbol': symbol,
            'timeframe': timeframe,
            'signal_type': signal.signal_type,
            'entry_price': signal.entry_price,
            'stop_loss': signal.stop_loss,
            'take_profit_1': signal.take_profit_1,
            'take_profit_2': signal.take_profit_2 if hasattr(signal, 'take_profit_2') else None,
            'confidence': signal.confidence,
            'risk_reward': signal.risk_reward,
            'ai_decision': ai_analysis.get('decision'),
            'ai_confidence': ai_analysis.get('confidence'),
            'ai_reasoning': ai_analysis.get('reasoning'),
            'ai_risk_assessment': ai_analysis.get('risk_assessment'),
            'ai_market_context': ai_analysis.get('market_context'),
            'reasons': signal.reasons,
            'metadata': signal.metadata
        }

        # DB에 저장
        success = signals_repo.save_signal(signal_data)

        if success:
            # 초기 결과 레코드 생성 (pending 상태)
            result_data = {
                'signal_id': signal_id,
                'status': 'pending',
                'exit_price': None,
                'exit_time': None,
                'pnl': None,
                'pnl_percent': None,
                'r_multiple': None,
                'max_favorable_excursion': None,
                'max_adverse_excursion': None,
                'duration_minutes': None,
                'is_simulation': True,
                'notes': 'Auto-generated signal'
            }
            signals_repo.save_result(result_data)

        return success

    except Exception as e:
        logger.error(f"❌ 신호 DB 저장 실패: {e}")
        return False


def send_telegram_notification(signal, symbol: str, timeframe: str) -> bool:
    """
    신호를 텔레그램으로 전송

    Args:
        signal: TradeSignal 객체
        symbol: 심볼
        timeframe: 타임프레임

    Returns:
        성공 여부
    """
    try:
        # config에서 텔레그램 설정 가져오기
        tg_config = config.get('telegram', {})
        bot_token = tg_config.get('bot_token')
        chat_id = tg_config.get('chat_id')

        if not bot_token or not chat_id:
            logger.warning("⚠️ 텔레그램 설정이 없습니다 (bot_token 또는 chat_id 누락)")
            return False

        parse_mode = tg_config.get('parse_mode', 'HTML')
        disable_preview = tg_config.get('disable_web_page_preview', True)

        # 메시지 포맷팅
        direction = signal.signal_type.upper()
        icon = "🟢" if direction == "BUY" else "🔴"

        # 숫자 포맷팅
        def fnum(x):
            try:
                return f"{float(x):,.6f}".rstrip("0").rstrip(".")
            except Exception:
                return str(x)

        entry = fnum(signal.entry_price)
        sl = fnum(signal.stop_loss)
        tp1 = fnum(signal.take_profit_1) if signal.take_profit_1 else "-"
        tp2 = fnum(signal.take_profit_2) if hasattr(signal, 'take_profit_2') and signal.take_profit_2 else "-"
        confidence = f"{signal.confidence * 100:.0f}%" if signal.confidence else "-"

        # AI 분석 정보
        ai_analysis = signal.metadata.get('ai_analysis', {})
        ai_decision = ai_analysis.get('decision', '-')
        ai_confidence = ai_analysis.get('confidence')
        ai_conf_str = f"{ai_confidence * 100:.0f}%" if ai_confidence else "-"

        # TradingView 링크
        tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"
        tv_link = html_module.escape(tv_link)

        # 매매실행 링크 (트레이딩 봇 서버 - 실시간 신호 페이지)
        trade_server = "http://kenbaeai.asuscomm.com:8888"
        trade_link = f"{trade_server}/"

        # 이유 (첫 번째만)
        reason = signal.reasons[0] if signal.reasons else "-"
        if len(reason) > 50:
            reason = reason[:47] + "..."

        # HTML 메시지 생성
        msg = (
            f"{icon} <b>{html_module.escape(symbol)}</b> <b>{direction}</b>  <code>{html_module.escape(timeframe)}</code>\n"
            f"<b>Strategy</b>: <code>{html_module.escape(signal.strategy_name)}</code>\n"
            f"<b>Confidence</b>: <code>{html_module.escape(confidence)}</code>\n"
            f"<b>Entry</b>: <code>{html_module.escape(entry)}</code>\n"
            f"<b>SL</b>: <code>{html_module.escape(sl)}</code>\n"
            f"<b>TP1</b>: <code>{html_module.escape(tp1)}</code>\n"
            f"<b>TP2</b>: <code>{html_module.escape(tp2)}</code>\n"
            f"<b>AI</b>: <code>{html_module.escape(ai_decision)}</code> ({html_module.escape(ai_conf_str)})\n"
            f"<b>Reason</b>: {html_module.escape(reason)}\n"
            f"<b>TV</b>: {tv_link}\n"
            f"<b>Trade</b>: {trade_link}\n"
            f"<i>Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST</i>"
        )

        # 텔레그램 API 호출
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        r = requests.post(url, json=payload, timeout=15)

        if r.ok:
            logger.info(f"📱 텔레그램 전송 완료: {symbol} {direction}")
            return True
        else:
            logger.error(f"❌ 텔레그램 전송 실패: {r.status_code} - {r.text}")
            return False

    except Exception as e:
        logger.error(f"❌ 텔레그램 전송 오류: {e}")
        return False


def send_server_startup_notification() -> bool:
    """
    서버 시작 시 텔레그램 알림 전송
    """
    try:
        # config에서 텔레그램 설정 가져오기
        tg_config = config.get('telegram', {})
        bot_token = tg_config.get('bot_token')
        chat_id = tg_config.get('chat_id')

        if not bot_token or not chat_id:
            logger.warning("⚠️ 텔레그램 설정이 없습니다")
            return False

        parse_mode = tg_config.get('parse_mode', 'HTML')
        disable_preview = tg_config.get('disable_web_page_preview', True)

        # 감시 심볼 목록
        symbols_str = ", ".join(app_state.watched_symbols[:5])
        if len(app_state.watched_symbols) > 5:
            symbols_str += f" 외 {len(app_state.watched_symbols) - 5}개"

        # 활성 전략 목록
        strategies_list = strategy_manager.list_strategies() if strategy_manager else []
        strategies_str = ", ".join(strategies_list[:5])
        if len(strategies_list) > 5:
            strategies_str += f" 외 {len(strategies_list) - 5}개"

        # 서버 링크
        trade_server = "http://kenbaeai.asuscomm.com:8888"

        # HTML 메시지 생성
        msg = (
            f"🚀 <b>Auto Trading Server Started</b>\n\n"
            f"<b>Time</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST\n"
            f"<b>Symbols</b>: <code>{html_module.escape(symbols_str)}</code>\n"
            f"<b>Strategies</b>: <code>{html_module.escape(strategies_str)}</code>\n"
            f"<b>Interval</b>: <code>{app_state.analysis_interval}s</code>\n\n"
            f"<b>Dashboard</b>: {trade_server}/\n"
            f"<b>Signals</b>: {trade_server}/signals"
        )

        # 텔레그램 API 호출
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        r = requests.post(url, json=payload, timeout=15)

        if r.ok:
            logger.info("📱 서버 시작 텔레그램 알림 전송 완료")
            return True
        else:
            logger.error(f"❌ 서버 시작 텔레그램 전송 실패: {r.status_code}")
            return False

    except Exception as e:
        logger.error(f"❌ 서버 시작 텔레그램 전송 오류: {e}")
        return False


async def background_analyzer():
    """백그라운드 분석 (주기적으로 신호 생성)"""
    logger.info("📊 백그라운드 분석 시작")

    while True:
        try:
            # 동적 설정 사용
            symbols = app_state.watched_symbols
            timeframe = app_state.timeframe

            logger.info(f"🔍 분석 시작: {symbols} ({timeframe})")

            # 1. 최신 캔들 업데이트
            logger.info("📡 최신 캔들 업데이트 중...")
            updated = await update_latest_candles(symbols, timeframe)
            logger.info(f"✅ {updated}/{len(symbols)} 심볼 업데이트 완료")

            # 2. 분석 시작
            all_signals = []
            saved_count = 0

            for symbol in symbols:
                # 데이터 로드
                query = """
                    SELECT open_time, open, high, low, close, volume, symbol, tf
                    FROM candles
                    WHERE symbol = %s AND tf = %s
                    ORDER BY open_time DESC
                    LIMIT 500
                """

                df = pd.read_sql(query, db_engine, params=(symbol, timeframe))

                if len(df) < 50:
                    logger.warning(f"⚠️  {symbol}: 데이터 부족 ({len(df)}개)")
                    continue

                df = df.sort_values('open_time').reset_index(drop=True)

                # 분석
                results = strategy_manager.analyze_all(symbol, timeframe, df)

                for strategy_name, signals in results.items():
                    for signal in signals:
                        # 모든 신호 추가 (거부된 것도 포함)
                        all_signals.append(signal)

                        # DB 저장 조건 확인
                        ai_analysis = signal.metadata.get('ai_analysis', {})
                        should_save = False

                        if ai_analysis:
                            # AI 검증이 있으면: approve + rejected=False 필요
                            if ai_analysis.get('decision') == 'approve' and not ai_analysis.get('rejected', False):
                                should_save = True
                        else:
                            # AI 검증이 없으면 (use_ai=False): confidence 기준으로 저장
                            # 각 전략의 min_confidence (0.5) 이상이면 저장
                            if signal.confidence >= 0.5:
                                should_save = True
                                logger.info(f"✅ {symbol} {signal.signal_type} 신호 저장 대상 (AI 검증 없음, confidence={signal.confidence:.2f})")

                        if should_save:
                            # 텔레그램 알림 먼저 전송 (DB 락 대기 중에도 알림 가능)
                            send_telegram_notification(signal, symbol, timeframe)
                            # DB 저장 (ALTER 중이면 대기)
                            if save_signal_to_db(signal, symbol, timeframe):
                                saved_count += 1

            # 상태 업데이트
            app_state.signals = all_signals
            app_state.last_update = datetime.now()

            # WebSocket 브로드캐스트
            await broadcast_signals(all_signals)

            logger.info(f"📊 분석 완료: {len(all_signals)}개 신호, {saved_count}개 DB 저장")

        except Exception as e:
            logger.error(f"백그라운드 분석 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 동적 주기 사용
        await asyncio.sleep(app_state.analysis_interval)


async def background_result_updater():
    """백그라운드 결과 업데이트 (시뮬레이션)"""
    logger.info("📊 백그라운드 결과 업데이트 시작")

    # 5분 대기 후 시작 (분석기가 먼저 시작되도록)
    await asyncio.sleep(300)

    while True:
        try:
            # pending 상태 신호 조회 (최근 72시간)
            pending_signals = signals_repo.get_pending_signals(hours=72)

            if not pending_signals:
                logger.debug("📋 업데이트할 pending 신호 없음")
                await asyncio.sleep(300)  # 5분
                continue

            logger.info(f"📋 {len(pending_signals)}개 pending 신호 확인 중...")
            updated_count = 0

            for signal in pending_signals:
                try:
                    signal_id = signal['signal_id']
                    symbol = signal['symbol']
                    signal_type = signal['signal_type']
                    entry_price = float(signal['entry_price'])
                    stop_loss = float(signal['stop_loss'])
                    take_profit_1 = signal.get('take_profit_1')
                    take_profit_2 = signal.get('take_profit_2')
                    created_at = signal['created_at']

                    # 최신 캔들 조회 (신호 생성 이후)
                    query = """
                        SELECT open_time, high, low, close
                        FROM candles
                        WHERE symbol = %s
                          AND tf = %s
                          AND open_time >= %s
                        ORDER BY open_time ASC
                    """

                    timeframe = signal['timeframe']
                    df = pd.read_sql(query, db_engine, params=(symbol, timeframe, created_at))

                    if len(df) == 0:
                        logger.debug(f"⏭️  {signal_id}: 신호 이후 캔들 없음")
                        continue

                    # TP/SL 체크
                    status = None
                    exit_price = None
                    exit_time = None
                    mfe = 0.0  # Max Favorable Excursion
                    mae = 0.0  # Max Adverse Excursion

                    for _, row in df.iterrows():
                        high = float(row['high'])
                        low = float(row['low'])
                        timestamp = row['open_time']

                        if signal_type == 'buy':
                            # MFE: 최대 유리한 움직임 (%)
                            current_mfe = ((high - entry_price) / entry_price) * 100
                            if current_mfe > mfe:
                                mfe = current_mfe

                            # MAE: 최대 불리한 움직임 (%)
                            current_mae = ((entry_price - low) / entry_price) * 100
                            if current_mae > mae:
                                mae = current_mae

                            # TP2 체크 (우선)
                            if take_profit_2 and high >= float(take_profit_2):
                                status = 'tp2_hit'
                                exit_price = float(take_profit_2)
                                exit_time = timestamp
                                break

                            # TP1 체크
                            if take_profit_1 and high >= float(take_profit_1):
                                status = 'tp1_hit'
                                exit_price = float(take_profit_1)
                                exit_time = timestamp
                                break

                            # SL 체크
                            if low <= stop_loss:
                                status = 'sl_hit'
                                exit_price = stop_loss
                                exit_time = timestamp
                                break

                        else:  # sell
                            # MFE: 최대 유리한 움직임 (%)
                            current_mfe = ((entry_price - low) / entry_price) * 100
                            if current_mfe > mfe:
                                mfe = current_mfe

                            # MAE: 최대 불리한 움직임 (%)
                            current_mae = ((high - entry_price) / entry_price) * 100
                            if current_mae > mae:
                                mae = current_mae

                            # TP2 체크 (우선)
                            if take_profit_2 and low <= float(take_profit_2):
                                status = 'tp2_hit'
                                exit_price = float(take_profit_2)
                                exit_time = timestamp
                                break

                            # TP1 체크
                            if take_profit_1 and low <= float(take_profit_1):
                                status = 'tp1_hit'
                                exit_price = float(take_profit_1)
                                exit_time = timestamp
                                break

                            # SL 체크
                            if high >= stop_loss:
                                status = 'sl_hit'
                                exit_price = stop_loss
                                exit_time = timestamp
                                break

                    # 결과가 확정되었으면 DB 업데이트
                    if status and exit_price:
                        # PnL 계산
                        if signal_type == 'buy':
                            pnl = exit_price - entry_price
                            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
                            risk = entry_price - stop_loss
                        else:  # sell
                            pnl = entry_price - exit_price
                            pnl_percent = ((entry_price - exit_price) / entry_price) * 100
                            risk = stop_loss - entry_price

                        # R-multiple 계산
                        r_multiple = pnl / risk if risk > 0 else 0

                        # Duration 계산 (분)
                        # timezone 정보 제거하여 호환성 보장
                        exit_time_naive = exit_time.replace(tzinfo=None) if hasattr(exit_time, 'tzinfo') and exit_time.tzinfo else exit_time
                        created_at_naive = created_at.replace(tzinfo=None) if hasattr(created_at, 'tzinfo') and created_at.tzinfo else created_at
                        duration_minutes = int((exit_time_naive - created_at_naive).total_seconds() / 60)

                        # 결과 업데이트
                        result_data = {
                            'signal_id': signal_id,
                            'status': status,
                            'exit_price': exit_price,
                            'exit_time': exit_time,
                            'pnl': pnl,
                            'pnl_percent': pnl_percent,
                            'r_multiple': r_multiple,
                            'max_favorable_excursion': mfe,
                            'max_adverse_excursion': mae,
                            'duration_minutes': duration_minutes,
                            'is_simulation': True,
                            'notes': f"Auto-updated by result updater"
                        }

                        if signals_repo.save_result(result_data):
                            updated_count += 1
                            logger.info(f"✅ {signal_id}: {status} @ ${exit_price:,.2f} (PnL: {pnl_percent:+.2f}%, R: {r_multiple:.2f})")

                except Exception as e:
                    logger.error(f"❌ 신호 업데이트 실패 ({signal.get('signal_id', 'unknown')}): {e}")
                    continue

            if updated_count > 0:
                logger.info(f"📊 결과 업데이트 완료: {updated_count}/{len(pending_signals)}개")

        except Exception as e:
            logger.error(f"❌ 백그라운드 결과 업데이트 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())

        # 5분마다 실행
        await asyncio.sleep(300)


# ============================================================
# 실행
# ============================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8888,  # 포트 변경
        reload=True,
        log_level="info"
    )
