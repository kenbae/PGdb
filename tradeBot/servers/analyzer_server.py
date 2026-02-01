"""
분석 서버 (Analyzer Server)

역할:
- 정시(0/15/30/45분) 캔들 마감 후 분석
- DB에 신호 저장
- 텔레그램 알림 전송
- HTTP API로 수동 분석 요청 처리

포트: 8887 (HTTP API)
"""

import os
import sys
import asyncio
import logging
import math
import copy
from datetime import datetime, timedelta
import time
import io

# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == 'win32':
    # 콘솔 출력 인코딩을 UTF-8로 설정
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    # 환경 변수 설정
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# 프로젝트 경로 설정
_current_dir = os.path.dirname(os.path.abspath(__file__))
_tradebot_dir = os.path.dirname(_current_dir)  # tradeBot 폴더
_pgdb_dir = os.path.dirname(_tradebot_dir)  # PGdb 폴더
sys.path.insert(0, _tradebot_dir)
sys.path.insert(0, _pgdb_dir)

# .env 파일 로드
from dotenv import load_dotenv
load_dotenv()

import requests
import html as html_module
import pandas as pd
from sqlalchemy import create_engine
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import Optional
import uvicorn

from core.config_loader import get_config
from strategies import create_strategy_manager
from database.watched_symbols_repo import WatchedSymbolsRepo
from database.signals_repo import SignalsRepo
from database.strategy_settings_repo import StrategySettingsRepo
from database.events_repo import EventsRepo

# 로깅 설정 (UTF-8 인코딩 지원)
class UTF8StreamHandler(logging.StreamHandler):
    """UTF-8 인코딩을 지원하는 StreamHandler"""
    def __init__(self, stream=None):
        if stream is None:
            stream = sys.stdout
        # Windows에서 UTF-8 인코딩 보장
        if sys.platform == 'win32' and hasattr(stream, 'buffer'):
            stream = io.TextIOWrapper(stream.buffer, encoding='utf-8', errors='replace', line_buffering=True)
        super().__init__(stream)
    
    def emit(self, record):
        try:
            msg = self.format(record)
            stream = self.stream
            # 유니코드 문자열을 그대로 출력
            stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

# 기존 핸들러 제거 후 UTF-8 핸들러 추가
root_logger = logging.getLogger()
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

handler = UTF8StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s'))
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

logger = logging.getLogger("AnalyzerServer")

# 전역 변수
config = None
strategy_manager = None
exchange = None
db_engine = None
watched_symbols_repo = None
signals_repo = None
strategy_settings_repo = None
events_repo = None
strategy_settings_version = None


def _get_ai_decision(signal) -> str:
    """TradeSignal에서 AI decision 추출(없으면 'none')"""
    try:
        metadata = signal.metadata if getattr(signal, 'metadata', None) else {}
        ai_analysis = metadata.get('ai_analysis', {}) if isinstance(metadata, dict) else {}
        decision = ai_analysis.get('decision')
        if decision:
            return str(decision)
    except Exception:
        pass
    try:
        decision = getattr(signal, 'ai_decision', None)
        if decision:
            return str(decision)
    except Exception:
        pass
    return 'none'


def _signal_policy(signal, ai_min_confidence: float = 0.65, non_ai_conf_threshold: float = 0.5) -> dict:
    """
    저장/알림 정책을 한 곳으로 통일

    - AI approve: 저장 + 알림
    - AI reject: 저장 (알림 없음)
    - AI none: confidence >= threshold면 저장 + 알림
    """
    decision = _get_ai_decision(signal)

    ai_conf = None
    try:
        metadata = signal.metadata if getattr(signal, 'metadata', None) else {}
        ai_analysis = metadata.get('ai_analysis', {}) if isinstance(metadata, dict) else {}
        ai_conf = ai_analysis.get('confidence')
    except Exception:
        pass
    if ai_conf is None:
        ai_conf = getattr(signal, 'ai_confidence', None)

    should_save = False
    should_notify = False
    reason = 'skip'

    if decision == 'approve':
        if ai_conf is not None and ai_conf < ai_min_confidence:
            should_save = True
            should_notify = False
            reason = f"ai_approve_low_conf({ai_conf:.2f}<{ai_min_confidence})"
        else:
            should_save = True
            should_notify = True
            reason = "ai_approve"
    elif decision == 'reject':
        should_save = True
        should_notify = False
        reason = "ai_reject"
    else:
        conf = getattr(signal, 'confidence', None)
        if conf is not None and conf >= non_ai_conf_threshold:
            should_save = True
            should_notify = True
            reason = f"conf({conf:.2f}>={non_ai_conf_threshold})"
        else:
            reason = f"conf({conf})<{non_ai_conf_threshold}"

    return {
        "decision": decision,
        "ai_confidence": ai_conf,
        "should_save": should_save,
        "should_notify": should_notify,
        "reason": reason,
    }


def _reload_strategy_manager() -> dict:
    """DB 기반 StrategyManager 재생성 (config.yaml strategies 사용 안 함)"""
    global strategy_manager, strategy_settings_repo, strategy_settings_version

    raw_cfg = config.config if config and hasattr(config, 'config') else {}
    strategy_manager = create_strategy_manager(strategy_settings_repo=strategy_settings_repo, config=raw_cfg)
    strategy_settings_version = strategy_settings_repo.get_version() if strategy_settings_repo else None

    return {
        "strategies": strategy_manager.list_strategies() if strategy_manager else [],
        "version": strategy_settings_version
    }


class AnalyzerState:
    """분석 서버 상태"""
    def __init__(self):
        self.watched_symbols = []
        self.symbol_timeframes = {}  # {symbol: timeframe} 심볼별 타임프레임
        self.symbol_strategies = {}  # {symbol: [strategy1, strategy2, ...]} 심볼별 전략
        self.timeframe = '15m'  # 기본값 (fallback)
        self.last_analysis_time = None
        self.analysis_count = 0


state = AnalyzerState()


def init_components():
    """컴포넌트 초기화"""
    global config, strategy_manager, exchange, db_engine, watched_symbols_repo, signals_repo
    global strategy_settings_repo, events_repo, strategy_settings_version

    logger.info("🚀 분석 서버 초기화 중...")

    # Config 로드
    config = get_config()
    logger.info("✅ Config 로드")

    # DB 연결
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    db_engine = create_engine(db_url)
    logger.info("✅ DB 연결")

    # Repository 초기화
    watched_symbols_repo = WatchedSymbolsRepo(db_engine)
    signals_repo = SignalsRepo(db_engine)
    strategy_settings_repo = StrategySettingsRepo(db_engine)
    events_repo = EventsRepo(db_engine)
    logger.info("✅ Repository 초기화")

    # 거래소 초기화 (캔들 업데이트용)
    try:
        from exchanges.binance_live import BinanceLive

        api_key = os.getenv('BINANCE_LIVE_API_KEY')
        api_secret = os.getenv('BINANCE_LIVE_API_SECRET')

        if api_key and api_secret:
            exchange = BinanceLive(api_key=api_key, api_secret=api_secret)
            logger.info("✅ 바이낸스 거래소 연결")
        else:
            logger.warning("⚠️ 바이낸스 API 키 없음")
    except Exception as e:
        logger.error(f"❌ 거래소 초기화 실패: {e}")

    # 감시 심볼 로드 (timeframe + strategies 포함)
    watched_data = watched_symbols_repo.get_all_with_strategies(enabled_only=True)
    if watched_data:
        state.watched_symbols = [d['symbol'] for d in watched_data]
        state.symbol_timeframes = {d['symbol']: d.get('timeframe', '15m') for d in watched_data}
        # 심볼별 전략 파싱 (쉼표로 구분된 문자열 -> 리스트)
        state.symbol_strategies = {}
        for d in watched_data:
            strategies_str = d.get('strategies', '')
            if strategies_str:
                # 쉼표로 구분된 전략 목록을 리스트로 변환
                strategy_list = [s.strip() for s in strategies_str.split(',') if s.strip()]
                state.symbol_strategies[d['symbol']] = strategy_list
            else:
                state.symbol_strategies[d['symbol']] = None  # None이면 모든 전략 사용
        logger.info(f"✅ 감시 심볼 로드: {state.watched_symbols}")
        logger.info(f"✅ 심볼별 타임프레임: {state.symbol_timeframes}")
        logger.info(f"✅ 심볼별 전략: {state.symbol_strategies}")
    else:
        state.watched_symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
        state.symbol_timeframes = {'BTCUSDT': '15m', 'ETHUSDT': '15m', 'SOLUSDT': '15m'}
        state.symbol_strategies = {}
        logger.info(f"⚠️ 기본값 사용: {state.watched_symbols}")

    # 전략 초기화
    reload_result = _reload_strategy_manager()
    logger.info(f"✅ 전략 초기화: {reload_result.get('strategies')} (override_version={reload_result.get('version')})")

    logger.info("🎉 분석 서버 준비 완료!")


def send_telegram_notification(signal, symbol: str, timeframe: str) -> bool:
    """신호를 텔레그램으로 전송"""
    try:
        tg_config = config.get('telegram', {})
        bot_token = tg_config.get('bot_token')
        chat_id = tg_config.get('chat_id')

        if not bot_token or not chat_id:
            logger.warning("⚠️ 텔레그램 설정 누락")
            return False

        parse_mode = tg_config.get('parse_mode', 'HTML')
        disable_preview = tg_config.get('disable_web_page_preview', True)

        # 메시지 포맷팅
        direction = signal.signal_type.upper()
        icon = "🟢" if direction == "BUY" else "🔴"

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
        metadata = signal.metadata if signal.metadata else {}
        ai_analysis = metadata.get('ai_analysis', {})
        ai_decision = ai_analysis.get('decision', '-')
        ai_confidence = ai_analysis.get('confidence')
        ai_conf_str = f"{ai_confidence * 100:.0f}%" if ai_confidence else "-"

        # 링크
        tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"
        tv_link = html_module.escape(tv_link)
        trade_server = "http://kenbaeai.asuscomm.com:8888"
        trade_link = f"{trade_server}/"

        # 이유
        reason = signal.reasons[0] if signal.reasons else "-"
        if len(reason) > 50:
            reason = reason[:47] + "..."

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
            logger.error(f"❌ 텔레그램 전송 실패: {r.status_code}")
            return False

    except Exception as e:
        logger.error(f"❌ 텔레그램 전송 오류: {e}")
        return False


def send_server_startup_notification() -> bool:
    """서버 시작 알림"""
    try:
        tg_config = config.get('telegram', {})
        bot_token = tg_config.get('bot_token')
        chat_id = tg_config.get('chat_id')

        if not bot_token or not chat_id:
            return False

        parse_mode = tg_config.get('parse_mode', 'HTML')
        disable_preview = tg_config.get('disable_web_page_preview', True)

        symbols_str = ", ".join(state.watched_symbols[:5])
        if len(state.watched_symbols) > 5:
            symbols_str += f" 외 {len(state.watched_symbols) - 5}개"

        strategies_list = strategy_manager.list_strategies() if strategy_manager else []
        strategies_str = ", ".join(strategies_list[:5])
        if len(strategies_list) > 5:
            strategies_str += f" 외 {len(strategies_list) - 5}개"

        trade_server = "http://kenbaeai.asuscomm.com:8888"

        msg = (
            f"🚀 <b>Analyzer Server Started</b>\n\n"
            f"<b>Time</b>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST\n"
            f"<b>Symbols</b>: <code>{html_module.escape(symbols_str)}</code>\n"
            f"<b>Strategies</b>: <code>{html_module.escape(strategies_str)}</code>\n"
            f"<b>Mode</b>: 정시 분석 (0/15/30/45분)\n\n"
            f"<b>Dashboard</b>: {trade_server}/\n"
            f"<b>Signals</b>: {trade_server}/signals"
        )

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        r = requests.post(url, json=payload, timeout=15)

        if r.ok:
            logger.info("📱 서버 시작 알림 전송 완료")
            return True
        else:
            logger.error(f"❌ 알림 전송 실패: {r.status_code}")
            return False

    except Exception as e:
        logger.error(f"❌ 알림 전송 오류: {e}")
        return False


async def update_latest_candles(symbols: list, symbol_timeframes: dict = None) -> int:
    """최신 캔들 업데이트 (ccxt 사용) - 심볼별 타임프레임 지원"""
    import ccxt
    from sqlalchemy import text

    updated = 0
    symbol_timeframes = symbol_timeframes or {}

    try:
        # 바이낸스 선물 연결
        binance = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        for symbol in symbols:
            try:
                # 심볼별 타임프레임 가져오기 (없으면 기본값 15m)
                timeframe = symbol_timeframes.get(symbol, '15m')

                # DB에서 최신 캔들 시간 확인
                query = text("""
                    SELECT MAX(open_time) as last_time
                    FROM candles
                    WHERE symbol = :symbol AND tf = :tf
                """)

                with db_engine.connect() as conn:
                    result = conn.execute(query, {"symbol": symbol, "tf": timeframe})
                    row = result.fetchone()
                    last_time = row[0] if row else None

                # 심볼 형식 변환: BTCUSDT -> BTC/USDT:USDT
                ccxt_symbol = symbol
                if '/' not in symbol:
                    if symbol.endswith('USDT'):
                        base = symbol[:-4]
                        ccxt_symbol = f"{base}/USDT:USDT"

                # 최근 10개 캔들 가져오기
                ohlcv = binance.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=10)

                if not ohlcv:
                    continue

                # 새 캔들 필터링 및 저장
                new_count = 0
                for candle in ohlcv:
                    candle_time = pd.Timestamp(candle[0], unit='ms', tz='UTC')

                    # 이미 존재하는지 확인
                    check_query = text("""
                        SELECT 1 FROM candles
                        WHERE symbol = :symbol AND tf = :tf AND open_time = :open_time
                        LIMIT 1
                    """)

                    with db_engine.connect() as conn:
                        exists = conn.execute(check_query, {
                            "symbol": symbol,
                            "tf": timeframe,
                            "open_time": candle_time
                        }).fetchone()

                        if exists:
                            # 업데이트
                            update_query = text("""
                                UPDATE candles
                                SET open = :open, high = :high, low = :low, close = :close, volume = :volume
                                WHERE symbol = :symbol AND tf = :tf AND open_time = :open_time
                            """)
                            conn.execute(update_query, {
                                "symbol": symbol,
                                "tf": timeframe,
                                "open_time": candle_time,
                                "open": candle[1],
                                "high": candle[2],
                                "low": candle[3],
                                "close": candle[4],
                                "volume": candle[5]
                            })
                        else:
                            # 새로 삽입
                            insert_query = text("""
                                INSERT INTO candles (exchange, market, symbol, tf, open_time, open, high, low, close, volume)
                                VALUES ('binance', 'usdtm', :symbol, :tf, :open_time, :open, :high, :low, :close, :volume)
                            """)
                            conn.execute(insert_query, {
                                "symbol": symbol,
                                "tf": timeframe,
                                "open_time": candle_time,
                                "open": candle[1],
                                "high": candle[2],
                                "low": candle[3],
                                "close": candle[4],
                                "volume": candle[5]
                            })

                        conn.commit()

                    if last_time is None or candle_time > last_time:
                        new_count += 1

                if new_count > 0:
                    logger.info(f"✅ {symbol}: {new_count}개 새 캔들 추가")
                    updated += 1
                else:
                    logger.debug(f"✓ {symbol}: 최신 상태")

            except Exception as e:
                logger.error(f"❌ {symbol} 캔들 업데이트 실패: {e}")

    except Exception as e:
        logger.error(f"❌ 캔들 업데이트 전체 실패: {e}")

    return updated


def save_signal_to_db(signal, symbol: str, timeframe: str) -> bool:
    """신호를 DB에 저장"""
    try:
        # signal_id 생성
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        signal_id = f"{symbol}_{timeframe}_{signal.signal_type}_{timestamp}"

        # AI 분석 정보 추출 (metadata가 None일 수 있음)
        metadata = signal.metadata if signal.metadata else {}
        ai_analysis = metadata.get('ai_analysis', {})

        signal_data = {
            'signal_id': signal_id,
            'symbol': symbol,
            'timeframe': timeframe,
            'strategy_name': signal.strategy_name,
            'signal_type': signal.signal_type,
            'entry_price': signal.entry_price,
            'stop_loss': signal.stop_loss,
            'take_profit_1': signal.take_profit_1,
            'take_profit_2': getattr(signal, 'take_profit_2', None),
            'confidence': signal.confidence,
            'risk_reward': signal.risk_reward,
            'ai_decision': ai_analysis.get('decision'),
            'ai_confidence': ai_analysis.get('confidence'),
            'ai_reasoning': ai_analysis.get('reasoning'),
            'ai_risk_assessment': ai_analysis.get('risk_assessment'),
            'ai_market_context': ai_analysis.get('market_context'),
            'reasons': signal.reasons,
            'metadata': metadata
        }

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

            # 이벤트 로그에도 저장 (1~4단계 공통)
            if events_repo:
                event_id = f"evt_signal_{signal_id}"
                events_repo.save_event(
                    event_id=event_id,
                    event_type='signal',
                    timestamp=datetime.now(),
                    symbol=symbol,
                    timeframe=timeframe,
                    data={
                        'signal_id': signal_id,
                        'strategy_name': signal.strategy_name,
                        'signal_type': signal.signal_type,
                        'entry_price': signal.entry_price,
                        'stop_loss': signal.stop_loss,
                        'take_profit_1': signal.take_profit_1,
                        'take_profit_2': getattr(signal, 'take_profit_2', None),
                        'confidence': signal.confidence,
                        'risk_reward': signal.risk_reward,
                        'ai_decision': ai_analysis.get('decision'),
                        'ai_confidence': ai_analysis.get('confidence'),
                        'reasons': signal.reasons,
                        'metadata': metadata
                    }
                )

            logger.info(f"💾 신호 저장: {symbol} {signal.signal_type} @ {signal.entry_price}")

        return success

    except Exception as e:
        logger.error(f"❌ 신호 저장 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def run_analysis():
    """분석 실행 - 심볼별 타임프레임 및 전략 사용"""
    # 전략 설정 변경 감지 시 자동 리로드
    try:
        if strategy_settings_repo:
            current_ver = strategy_settings_repo.get_version()
            if current_ver and current_ver != strategy_settings_version:
                logger.info(f"🔄 전략 설정 변경 감지 → 리로드 (old={strategy_settings_version}, new={current_ver})")
                _reload_strategy_manager()
    except Exception as e:
        logger.warning(f"⚠️ 전략 설정 자동 리로드 실패: {e}")

    symbols = state.watched_symbols
    symbol_timeframes = state.symbol_timeframes
    symbol_strategies = state.symbol_strategies

    # 타임프레임 요약 로그
    tf_summary = {}
    for s, tf in symbol_timeframes.items():
        if tf not in tf_summary:
            tf_summary[tf] = []
        tf_summary[tf].append(s)
    tf_info = ", ".join([f"{tf}: {len(syms)}개" for tf, syms in tf_summary.items()])
    logger.info(f"🔍 분석 시작: {len(symbols)}개 심볼 ({tf_info})")

    # 1. 최신 캔들 업데이트 (심볼별 타임프레임 전달)
    logger.info("📡 캔들 업데이트 중...")
    updated = await update_latest_candles(symbols, symbol_timeframes)
    logger.info(f"✅ {updated}/{len(symbols)} 심볼 업데이트")

    # 2. 분석
    all_signals = []
    saved_count = 0

    for symbol in symbols:
        try:
            # 심볼별 타임프레임 가져오기
            timeframe = symbol_timeframes.get(symbol, '15m')
            # 심볼별 전략 필터 가져오기 (None이면 모든 전략 사용)
            strategy_filter = symbol_strategies.get(symbol)

            query = """
                SELECT open_time, open, high, low, close, volume, symbol, tf
                FROM candles
                WHERE symbol = %s AND tf = %s
                ORDER BY open_time DESC
                LIMIT 500
            """

            df = pd.read_sql(query, db_engine, params=(symbol, timeframe))

            if len(df) < 50:
                logger.warning(f"⚠️ {symbol}: 데이터 부족 ({len(df)}개)")
                continue

            df = df.sort_values('open_time').reset_index(drop=True)

            # 전략 분석 (심볼별 전략 필터 적용)
            if strategy_filter:
                logger.info(f"📋 {symbol} 전략 필터 적용: {strategy_filter}")
            results = strategy_manager.analyze_all(symbol, timeframe, df, strategy_filter=strategy_filter)

            for strategy_name, signals in results.items():
                for signal in signals:
                    all_signals.append(signal)

                    policy = _signal_policy(
                        signal,
                        ai_min_confidence=float(config.get('ai.min_confidence', 0.65)),
                        non_ai_conf_threshold=0.5
                    )
                    if policy["should_save"]:
                        # 텔레그램 알림 (승인된 신호만)
                        if policy["should_notify"]:
                            logger.info(f"📱 [스케줄러] 텔레그램 알림 전송: {symbol} {signal.signal_type}")
                            send_telegram_notification(signal, symbol, timeframe)
                        # DB 저장 (ALTER 중이면 대기)
                        logger.info(f"📝 [스케줄러] DB 저장 시도: {symbol} {signal.signal_type} ({policy['reason']})")
                        if save_signal_to_db(signal, symbol, timeframe):
                            saved_count += 1
                        else:
                            logger.warning(f"⚠️ [스케줄러] DB 저장 실패: {symbol} {signal.signal_type}")
                    else:
                        logger.info(f"⏸️ {symbol} {signal.signal_type} 저장 스킵 ({policy['reason']})")

        except Exception as e:
            logger.error(f"❌ {symbol} 분석 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())

    state.last_analysis_time = datetime.now()
    state.analysis_count += 1

    logger.info(f"📊 [스케줄러] 분석 완료: {len(all_signals)}개 신호 발견, {saved_count}개 저장/알림")


def get_next_quarter_time() -> datetime:
    """다음 정시(0/15/30/45분) 시간 계산"""
    now = datetime.now()
    minute = now.minute

    # 다음 정시 분 계산
    if minute < 15:
        next_minute = 15
    elif minute < 30:
        next_minute = 30
    elif minute < 45:
        next_minute = 45
    else:
        next_minute = 0

    if next_minute == 0:
        # 다음 시간
        next_time = now.replace(minute=0, second=30, microsecond=0) + timedelta(hours=1)
    else:
        next_time = now.replace(minute=next_minute, second=30, microsecond=0)

    return next_time


async def scheduler():
    """정시 스케줄러 (0/15/30/45분 + 30초)"""
    logger.info("⏰ 정시 스케줄러 시작")

    while True:
        try:
            # 다음 정시 계산
            next_time = get_next_quarter_time()
            wait_seconds = (next_time - datetime.now()).total_seconds()

            if wait_seconds > 0:
                logger.info(f"⏳ 다음 분석: {next_time.strftime('%H:%M:%S')} (대기 {wait_seconds:.0f}초)")
                await asyncio.sleep(wait_seconds)

            # 분석 실행
            await run_analysis()

            # 다음 사이클 전 1초 대기 (중복 방지)
            await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"❌ 스케줄러 오류: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await asyncio.sleep(60)  # 오류 시 1분 대기


# ============================================================
# FastAPI 앱
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 생명주기"""
    # 초기화
    init_components()
    send_server_startup_notification()

    # 스케줄러를 백그라운드 태스크로 실행
    scheduler_task = asyncio.create_task(scheduler())

    yield

    # 종료
    scheduler_task.cancel()
    logger.info("👋 분석 서버 종료")


app = FastAPI(title="Analyzer Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """헬스 체크"""
    next_analysis = get_next_quarter_time()
    return {
        "status": "ok",
        "last_analysis": state.last_analysis_time.isoformat() if state.last_analysis_time else None,
        "next_analysis": next_analysis.isoformat(),
        "next_analysis_seconds": max(0, int((next_analysis - datetime.now()).total_seconds())),
        "analysis_count": state.analysis_count,
        "watched_symbols": state.watched_symbols
    }


@app.post("/api/strategies/reload")
async def reload_strategies():
    """전략 설정(DB override 포함) 강제 리로드"""
    try:
        if not config:
            raise HTTPException(status_code=500, detail="Config가 초기화되지 않았습니다")
        result = _reload_strategy_manager()
        logger.info(f"🔄 전략 리로드 완료: {result.get('strategies')} (ver={result.get('version')})")
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 전략 리로드 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analyze/{symbol}")
async def analyze_symbol_api(
    symbol: str,
    timeframe: str = "15m",
    strategies: Optional[str] = None,
    include_rejected: bool = False,
    save_to_db: bool = False
):
    """심볼 분석 API"""
    try:
        if not strategy_manager:
            raise HTTPException(status_code=500, detail="전략 매니저가 초기화되지 않았습니다")

        # DB에서 캔들 데이터 로드
        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %(symbol)s AND tf = %(tf)s
            ORDER BY open_time DESC
            LIMIT 500
        """
        df = pd.read_sql(query, db_engine, params={"symbol": symbol, "tf": timeframe})

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
                # AI 분석 정보 (metadata가 None일 수 있음)
                metadata = signal.metadata if signal.metadata else {}
                ai_analysis = metadata.get('ai_analysis', {})
                ai_decision = ai_analysis.get('decision', 'none')

                # include_rejected가 False면 reject된 신호 제외
                if not include_rejected and ai_decision == 'reject':
                    continue

                # DB 저장 옵션
                if save_to_db:
                    policy = _signal_policy(
                        signal,
                        ai_min_confidence=float(config.get('ai.min_confidence', 0.65)),
                        non_ai_conf_threshold=0.5
                    )
                    if policy["should_save"]:
                        # 텔레그램 알림 (승인/조건 통과만)
                        if policy["should_notify"]:
                            logger.info(f"📱 텔레그램 알림 시도: {symbol} {signal.signal_type}")
                            send_telegram_notification(signal, symbol, timeframe)

                        logger.info(f"📝 DB 저장 시도: {symbol} {signal.signal_type} ({policy['reason']})")
                        save_result = save_signal_to_db(signal, symbol, timeframe)
                        logger.info(f"📝 DB 저장 결과: {save_result}")
                        if not save_result:
                            logger.warning(f"⚠️ DB 저장 실패: {symbol}")
                    else:
                        logger.info(f"⏸️ 저장 스킵: {symbol} {signal.signal_type} ({policy['reason']})")

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

        logger.info(f"📊 {symbol} 분석 완료: {len(signals_data)}개 신호")

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "signals": signals_data,
            "count": len(signals_data)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"분석 실패: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze/now")
async def analyze_all_now():
    """즉시 전체 분석 실행"""
    try:
        # 분석 전에 최신 심볼 정보 로드 (timeframe + strategies 포함)
        watched_data = watched_symbols_repo.get_all_with_strategies(enabled_only=True)
        if watched_data:
            state.watched_symbols = [d['symbol'] for d in watched_data]
            state.symbol_timeframes = {d['symbol']: d.get('timeframe', '15m') for d in watched_data}
            # 심볼별 전략 파싱
            state.symbol_strategies = {}
            for d in watched_data:
                strategies_str = d.get('strategies', '')
                if strategies_str:
                    strategy_list = [s.strip() for s in strategies_str.split(',') if s.strip()]
                    state.symbol_strategies[d['symbol']] = strategy_list
                else:
                    state.symbol_strategies[d['symbol']] = None
            logger.info(f"📋 심볼 정보 갱신: {len(state.watched_symbols)}개, TF: {state.symbol_timeframes}")
            logger.info(f"📋 심볼별 전략: {state.symbol_strategies}")

        await run_analysis()
        return {
            "status": "ok",
            "message": "분석 완료",
            "analysis_count": state.analysis_count,
            "symbol_timeframes": state.symbol_timeframes,
            "symbol_strategies": state.symbol_strategies
        }
    except Exception as e:
        logger.error(f"분석 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/reload-symbols")
async def reload_symbols():
    """감시 심볼 정보 새로고침 (DB에서 최신 정보 로드)"""
    try:
        watched_data = watched_symbols_repo.get_all_with_strategies(enabled_only=True)
        if watched_data:
            state.watched_symbols = [d['symbol'] for d in watched_data]
            state.symbol_timeframes = {d['symbol']: d.get('timeframe', '15m') for d in watched_data}
            # 심볼별 전략 파싱
            state.symbol_strategies = {}
            for d in watched_data:
                strategies_str = d.get('strategies', '')
                if strategies_str:
                    strategy_list = [s.strip() for s in strategies_str.split(',') if s.strip()]
                    state.symbol_strategies[d['symbol']] = strategy_list
                else:
                    state.symbol_strategies[d['symbol']] = None
            logger.info(f"✅ 심볼 정보 갱신: {state.symbol_timeframes}")
            logger.info(f"✅ 심볼별 전략: {state.symbol_strategies}")
            return {
                "status": "ok",
                "symbols": state.watched_symbols,
                "symbol_timeframes": state.symbol_timeframes,
                "symbol_strategies": state.symbol_strategies
            }
        else:
            return {
                "status": "ok",
                "symbols": [],
                "symbol_timeframes": {},
                "symbol_strategies": {},
                "message": "등록된 심볼 없음"
            }
    except Exception as e:
        logger.error(f"심볼 갱신 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 메인
# ============================================================

if __name__ == "__main__":
    import signal

    # Windows Ctrl+C 핸들러
    def signal_handler(signum, frame):
        logger.info("👋 종료 신호 수신, 서버를 종료합니다...")
        os._exit(0)  # Python 3.14 asyncio 호환성 문제 우회

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("🚀 분석 서버 시작 (포트 8887)")
    logger.info("   종료: Ctrl+C")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8887,
        log_level="info"
    )
