# -*- coding: utf-8 -*-
"""
Flask API for Strategy Backtester

전략 선택 가능한 백테스트 API
- EMA Cross, ICT, RSI, Bollinger, Keltner ICT Turtle, Pattern RAG 전략 지원
- 백테스트 결과를 오토봇 분석 대상 심볼 DB에 저장 가능
"""

import sys
import os

# 경로 설정 (strategy_backtester import 전에 필요)
_pgdb_dir = os.path.dirname(os.path.abspath(__file__))
if _pgdb_dir not in sys.path:
    sys.path.insert(0, _pgdb_dir)

from flask import Flask, jsonify, request, Response, send_file
from flask_cors import CORS
import json
import logging
from datetime import datetime
from strategy_backtester import StrategyBacktester
import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
import requests
from sqlalchemy import create_engine

# tradeBot DB override (전략 설정 단일 소스)
from database.strategy_settings_repo import StrategySettingsRepo

app = Flask(__name__)
CORS(app)

# 로깅
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config 파일 경로
CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.yaml')


def load_config():
    """config.yaml 로드 (매번 최신 설정 가져오기)"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


# 초기 Config 로드
config = load_config()

# 백테스터 초기화
backtester = StrategyBacktester()


def get_sqlalchemy_engine():
    """StrategySettingsRepo용 SQLAlchemy 엔진 생성"""
    db = config.get('db', {}) or {}
    db_url = (
        f"postgresql://{db.get('user')}:{db.get('password')}"
        f"@{db.get('host', 'localhost')}:{db.get('port', 5432)}/{db.get('name', 'marketdb')}"
    )
    return create_engine(db_url)


def merge_strategy_overrides(base_strategies: dict, overrides: list) -> dict:
    """config.yaml strategies + DB override merge (DB 우선)"""
    merged = dict(base_strategies or {})
    for row in overrides or []:
        name = row.get('strategy_name')
        if not name:
            continue
        merged.setdefault(name, {})
        if row.get('enabled') is not None:
            merged[name]['enabled'] = bool(row.get('enabled'))
        cfg = row.get('config') or {}
        if isinstance(cfg, dict) and cfg:
            cfg = dict(cfg)
            cfg.pop('enabled', None)
            merged[name].update(cfg)
    return merged


def get_db_connection():
    """DB 연결"""
    db_config = config.get('db', {})
    return psycopg2.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 5432),
        database=db_config.get('name', 'marketdb'),
        user=db_config.get('user', 'trader'),
        password=db_config.get('password', ''),
        client_encoding='utf8'
    )


def migrate_watch_symbols_table():
    """
    watch_symbols → watched_symbols 마이그레이션
    기존 watch_symbols 테이블 데이터를 watched_symbols로 이동 후 삭제
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. watch_symbols 테이블 존재 확인
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'watch_symbols'
                )
            """)
            watch_exists = cur.fetchone()[0]

            if not watch_exists:
                conn.close()
                return  # 마이그레이션 필요 없음

            # 2. watched_symbols 테이블에 필요한 컬럼 추가
            try:
                cur.execute("""
                    ALTER TABLE watched_symbols
                    ADD COLUMN IF NOT EXISTS strategies VARCHAR(200);

                    ALTER TABLE watched_symbols
                    ADD COLUMN IF NOT EXISTS notes TEXT;
                """)
            except Exception:
                pass

            # 3. watch_symbols → watched_symbols 데이터 이동
            cur.execute("""
                INSERT INTO watched_symbols (symbol, timeframe, enabled, strategies, notes)
                SELECT
                    symbol,
                    '15m' as timeframe,
                    COALESCE(active, true) as enabled,
                    strategies,
                    notes
                FROM watch_symbols
                WHERE active = true
                ON CONFLICT (symbol) DO UPDATE SET
                    strategies = COALESCE(EXCLUDED.strategies, watched_symbols.strategies),
                    notes = COALESCE(EXCLUDED.notes, watched_symbols.notes),
                    enabled = true,
                    updated_at = NOW()
            """)

            # 4. 기존 watch_symbols 테이블 삭제
            cur.execute("DROP TABLE IF EXISTS watch_symbols")

            conn.commit()
            logger.info("✅ watch_symbols → watched_symbols 마이그레이션 완료 및 기존 테이블 삭제")

        conn.close()

    except Exception as e:
        logger.error(f"마이그레이션 오류: {e}")


@app.route('/')
def index():
    """메인 페이지"""
    return send_file('backtest_web.html')


@app.route('/api/symbols')
def get_symbols():
    """심볼 목록 (거래량 순)"""
    try:
        conn = get_db_connection()
        
        # 최근 24시간 평균 거래량 기준 정렬
        query = """
            SELECT DISTINCT ON (symbol) 
                symbol, 
                AVG(volume) OVER (PARTITION BY symbol) as avg_volume
            FROM candles
            WHERE tf = '1h'
              AND open_time >= NOW() - INTERVAL '7 days'
            ORDER BY symbol, avg_volume DESC
        """
        
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            symbols = cur.fetchall()
        
        conn.close()
        
        # 거래량 순 정렬
        symbols_sorted = sorted(symbols, key=lambda x: x['avg_volume'] or 0, reverse=True)
        
        return jsonify({
            'symbols': [
                {
                    'symbol': s['symbol'],
                    'volume': float(s['avg_volume']) if s['avg_volume'] else 0
                }
                for s in symbols_sorted
            ]
        })
    
    except Exception as e:
        logger.error(f"심볼 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/symbols/binance')
def get_binance_symbols():
    """바이낸스 선물 24시간 거래량 상위 심볼"""
    try:
        # 바이낸스 선물 24시간 티커 정보
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        tickers = response.json()

        # USDT 페어만 필터 & 거래량 순 정렬
        usdt_pairs = [
            {
                'symbol': t['symbol'],
                'volume': float(t['quoteVolume']),  # USDT 거래량
                'price': float(t['lastPrice']),
                'priceChangePercent': float(t['priceChangePercent'])
            }
            for t in tickers
            if t['symbol'].endswith('USDT') and float(t['quoteVolume']) > 0
        ]

        # 거래량 순 정렬
        usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)

        return jsonify({
            'symbols': usdt_pairs[:200],  # 상위 200개
            'updated_at': datetime.now().isoformat()
        })

    except requests.RequestException as e:
        logger.error(f"바이낸스 API 오류: {e}")
        return jsonify({'error': f'바이낸스 API 오류: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"심볼 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies')
def get_strategies():
    """
    tradeBot 스타일 전략 목록
    - enabled/config 포함
    - (호환) description도 같이 제공
    """
    try:
        # 사용 가능한 전략(설명)
        strategies = backtester.get_available_strategies()

        # config.yaml 기본 + DB override merge
        cfg = load_config()
        base = cfg.get('strategies', {}) or {}
        engine = get_sqlalchemy_engine()
        repo = StrategySettingsRepo(engine)
        overrides = repo.get_all()
        engine.dispose()

        merged_configs = merge_strategy_overrides(base, overrides)

        return jsonify({
            'strategies': [
                {
                    'name': name,
                    'description': desc,
                    'enabled': bool((merged_configs.get(name) or {}).get('enabled', True)),
                    'config': (merged_configs.get(name) or {})
                }
                for name, desc in strategies.items()
            ]
        })
    except Exception as e:
        logger.error(f"전략 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/<strategy_name>/enable', methods=['POST'])
def enable_strategy(strategy_name):
    """tradeBot 스타일: 전략 활성화"""
    try:
        engine = get_sqlalchemy_engine()
        repo = StrategySettingsRepo(engine)
        ok = repo.patch(strategy_name, enabled=True, config_patch=None)
        engine.dispose()
        if not ok:
            return jsonify({'error': 'DB 저장 실패'}), 500

        global backtester
        backtester = StrategyBacktester()
        return jsonify({'success': True, 'strategy': strategy_name, 'enabled': True})
    except Exception as e:
        logger.error(f"전략 enable 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/<strategy_name>/disable', methods=['POST'])
def disable_strategy(strategy_name):
    """tradeBot 스타일: 전략 비활성화"""
    try:
        engine = get_sqlalchemy_engine()
        repo = StrategySettingsRepo(engine)
        ok = repo.patch(strategy_name, enabled=False, config_patch=None)
        engine.dispose()
        if not ok:
            return jsonify({'error': 'DB 저장 실패'}), 500

        global backtester
        backtester = StrategyBacktester()
        return jsonify({'success': True, 'strategy': strategy_name, 'enabled': False})
    except Exception as e:
        logger.error(f"전략 disable 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/<strategy_name>', methods=['PUT'])
def update_strategy_tradebot_style(strategy_name):
    """tradeBot 스타일: enabled/config 업데이트"""
    try:
        payload = request.json or {}
        enabled = payload.get('enabled') if isinstance(payload, dict) else None
        config_patch = payload.get('config') if isinstance(payload, dict) else None
        if not isinstance(config_patch, dict):
            config_patch = {}
        # enabled는 별도 컬럼
        if 'enabled' in config_patch:
            config_patch = dict(config_patch)
            config_patch.pop('enabled', None)

        engine = get_sqlalchemy_engine()
        repo = StrategySettingsRepo(engine)
        ok = repo.patch(strategy_name, enabled=enabled, config_patch=config_patch)
        saved = repo.get(strategy_name)
        engine.dispose()
        if not ok:
            return jsonify({'error': 'DB 저장 실패'}), 500

        global backtester
        backtester = StrategyBacktester()

        return jsonify({'success': True, 'strategy': strategy_name, 'saved': saved})
    except Exception as e:
        logger.error(f"전략 update 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/configs')
def get_strategy_configs():
    """전략별 현재 설정 조회"""
    try:
        cfg = load_config()
        base = cfg.get('strategies', {}) or {}

        engine = get_sqlalchemy_engine()
        repo = StrategySettingsRepo(engine)
        overrides = repo.get_all()
        engine.dispose()

        return jsonify({'configs': merge_strategy_overrides(base, overrides)})
    except Exception as e:
        logger.error(f"전략 설정 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/schema')
def get_strategy_schemas():
    """전략별 설정 스키마 조회 (웹 UI 동적 생성용)"""
    try:
        from strategies import (
            EMACrossStrategy, ICTStrategy, RSIStrategy,
            BollingerStrategy, KeltnerICTTurtleStrategy,
            PatternRAGStrategy, BBAdaptiveRSIStrategy
        )

        schemas = {
            'ema_cross': EMACrossStrategy.get_config_schema(),
            'ict': ICTStrategy.get_config_schema(),
            'rsi': RSIStrategy.get_config_schema(),
            'bollinger': BollingerStrategy.get_config_schema(),
            'keltner_ict_turtle': KeltnerICTTurtleStrategy.get_config_schema(),
            'pattern_rag': PatternRAGStrategy.get_config_schema(),
            'bb_adaptive_rsi': BBAdaptiveRSIStrategy.get_config_schema(),
        }

        return jsonify({'schemas': schemas})

    except Exception as e:
        logger.error(f"전략 스키마 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/strategies/<strategy_name>/config', methods=['GET', 'POST'])
def strategy_config(strategy_name):
    """개별 전략 설정 조회/저장"""
    try:
        if request.method == 'GET':
            cfg = load_config()
            base = (cfg.get('strategies', {}) or {}).get(strategy_name, {}) or {}

            engine = get_sqlalchemy_engine()
            repo = StrategySettingsRepo(engine)
            row = repo.get(strategy_name) or {}
            engine.dispose()

            merged = dict(base)
            # DB enabled 우선
            if row.get('enabled') is not None:
                merged['enabled'] = bool(row.get('enabled'))
            # DB config merge
            db_cfg = row.get('config') or {}
            if isinstance(db_cfg, dict) and db_cfg:
                db_cfg = dict(db_cfg)
                db_cfg.pop('enabled', None)
                merged.update(db_cfg)

            return jsonify({'config': merged})

        else:  # POST - 설정 저장
            new_config = request.json or {}
            enabled = new_config.get('enabled') if isinstance(new_config, dict) else None
            config_patch = dict(new_config) if isinstance(new_config, dict) else {}
            config_patch.pop('enabled', None)

            engine = get_sqlalchemy_engine()
            repo = StrategySettingsRepo(engine)
            ok = repo.patch(strategy_name, enabled=enabled, config_patch=config_patch)
            saved = repo.get(strategy_name)
            engine.dispose()

            if not ok:
                return jsonify({'error': 'DB 저장 실패'}), 500

            # backtester 인스턴스 갱신 (DB override 포함)
            global backtester
            backtester = StrategyBacktester()

            logger.info(f"전략 {strategy_name} 설정(DB) 저장됨: {new_config}")

            # UI 호환: 저장된 config를 다시 반환 (base + DB merge)
            cfg = load_config()
            base = (cfg.get('strategies', {}) or {}).get(strategy_name, {}) or {}
            merged = dict(base)
            if saved and saved.get('enabled') is not None:
                merged['enabled'] = bool(saved.get('enabled'))
            db_cfg = (saved or {}).get('config') or {}
            if isinstance(db_cfg, dict) and db_cfg:
                db_cfg = dict(db_cfg)
                db_cfg.pop('enabled', None)
                merged.update(db_cfg)

            return jsonify({'success': True, 'config': merged})

    except Exception as e:
        logger.error(f"전략 설정 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/symbol_stats/<symbol>')
def get_symbol_stats(symbol):
    """심볼별 년도/타임프레임별 캔들 수 통계"""
    try:
        conn = get_db_connection()

        query = """
            SELECT
                EXTRACT(YEAR FROM open_time)::INTEGER as year,
                tf,
                COUNT(*) as count
            FROM candles
            WHERE symbol = %s
              AND open_time >= '2020-01-01' AND open_time < '2027-01-01'
            GROUP BY year, tf
            ORDER BY year DESC,
                CASE tf
                    WHEN '1m' THEN 1 WHEN '5m' THEN 2 WHEN '15m' THEN 3
                    WHEN '30m' THEN 4 WHEN '1h' THEN 5 WHEN '4h' THEN 6
                    WHEN '1d' THEN 7 ELSE 99
                END
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (symbol,))
            raw_data = cur.fetchall()

        conn.close()

        # 년도별로 그룹화
        years_data = {}
        for row in raw_data:
            year = row['year']
            if year not in years_data:
                years_data[year] = {'year': year, 'tf_data': {}, 'total': 0}
            years_data[year]['tf_data'][row['tf']] = row['count']
            years_data[year]['total'] += row['count']

        return jsonify({
            'symbol': symbol,
            'years': list(years_data.values())
        })

    except Exception as e:
        logger.error(f"심볼 통계 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/pattern_rag/learn', methods=['POST'])
def pattern_rag_learn():
    """
    Pattern RAG 학습 API (스트리밍)

    Request body:
    {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframes": ["1h", "4h"],
        "start_date": "2024-01-01",
        "end_date": "2025-01-01"
    }
    """
    # request context 밖에서 사용하기 위해 미리 데이터 추출
    data = request.json
    symbols = data.get('symbols', [])
    timeframes = data.get('timeframes', ['1h'])
    start_date_str = data.get('start_date', '2024-01-01')
    end_date_str = data.get('end_date', '2025-01-01')

    def generate():
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')

            total = len(symbols) * len(timeframes)
            current = 0
            total_patterns = 0

            # PatternRAG 임포트
            try:
                from strategies.pattern_rag_strategy import PatternRAGStrategy
                from tradeBot.ai.pattern_rag import PatternRAGSignalGenerator
            except ImportError:
                import sys
                import os
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'tradeBot'))
                from ai.pattern_rag import PatternRAGSignalGenerator

            # RAG 생성기 초기화 (DB 또는 기본값)
            from strategies.strategy_defaults import STRATEGY_DEFAULTS
            rag_config = config.get('strategies', {}).get('pattern_rag') or STRATEGY_DEFAULTS.get('pattern_rag', {})
            rag_generator = PatternRAGSignalGenerator(
                ollama_host=rag_config.get('ollama_url', 'http://localhost:11434'),
                ollama_model=rag_config.get('ollama_model', 'qwen2.5:7b-instruct'),
                top_k=rag_config.get('top_k', 10)
            )

            for symbol in symbols:
                for tf in timeframes:
                    current += 1

                    # 진행 상황 전송
                    yield f"data: {json.dumps({'type': 'progress', 'current': current, 'total': total, 'symbol': symbol, 'timeframe': tf})}\n\n"

                    try:
                        # 데이터 로드
                        df = backtester.load_data(symbol, tf, start_date, end_date)

                        if df.empty or len(df) < 100:
                            logger.warning(f"[{symbol} {tf}] 데이터 부족: {len(df)}개")
                            yield f"data: {json.dumps({'type': 'result', 'symbol': symbol, 'timeframe': tf, 'patterns': 0, 'error': '데이터 부족'})}\n\n"
                            continue

                        # 학습 실행
                        saved = rag_generator.learn_from_data(df, symbol, tf)
                        total_patterns += saved

                        logger.info(f"[{symbol} {tf}] 학습 완료: {saved}개 패턴")
                        yield f"data: {json.dumps({'type': 'result', 'symbol': symbol, 'timeframe': tf, 'patterns': saved})}\n\n"

                    except Exception as e:
                        logger.error(f"[{symbol} {tf}] 학습 오류: {e}")
                        yield f"data: {json.dumps({'type': 'result', 'symbol': symbol, 'timeframe': tf, 'patterns': 0, 'error': str(e)})}\n\n"

            # 완료
            yield f"data: {json.dumps({'type': 'complete', 'total_patterns': total_patterns})}\n\n"

        except Exception as e:
            logger.error(f"Pattern RAG 학습 오류: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/pattern_rag/stats')
def pattern_rag_stats():
    """Pattern RAG 패턴 DB 통계 조회"""
    try:
        # PatternDatabase 임포트
        try:
            from tradeBot.ai.pattern_rag import PatternDatabase
        except ImportError:
            import sys
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'tradeBot'))
            from ai.pattern_rag import PatternDatabase

        db = PatternDatabase()
        stats = db.get_stats()

        return jsonify({
            'success': True,
            'stats': stats
        })

    except Exception as e:
        logger.error(f"Pattern RAG 통계 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/watch_symbols')
def get_watch_symbols():
    """오토봇 분석 대상 심볼 목록 조회 (watched_symbols 테이블 사용)"""
    try:
        conn = get_db_connection()

        # 테이블 존재 확인
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'watched_symbols'
                )
            """)
            exists = cur.fetchone()[0]

        if not exists:
            conn.close()
            return jsonify({'symbols': []})

        query = """
            SELECT symbol, created_at, notes, strategies
            FROM watched_symbols
            WHERE enabled = true
            ORDER BY created_at DESC
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query)
            symbols = cur.fetchall()

        conn.close()

        return jsonify({
            'symbols': [
                {
                    'symbol': s['symbol'],
                    'added_at': s['created_at'].isoformat() if s['created_at'] else None,
                    'source': 'backtest',
                    'notes': s.get('notes', ''),
                    'strategies': s.get('strategies', '')
                }
                for s in symbols
            ]
        })

    except Exception as e:
        logger.error(f"감시 심볼 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/watch_symbols', methods=['POST'])
def add_watch_symbols():
    """
    백테스트 결과 심볼을 오토봇 분석 대상에 추가 (watched_symbols 테이블 사용)

    Request body:
    {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "strategies": ["ema_cross", "ict"],
        "source": "backtest",
        "notes": "백테스트 상위 심볼"
    }
    """
    try:
        data = request.json
        symbols = data.get('symbols', [])
        source = data.get('source', 'backtest')
        notes = data.get('notes', '')

        if not symbols:
            return jsonify({'error': 'symbols 필수'}), 400

        conn = get_db_connection()

        # 전략 정보 (선택적) - 문자열 또는 리스트 모두 지원
        strategies_raw = data.get('strategies', [])
        if isinstance(strategies_raw, str):
            # 문자열로 받은 경우 (쉼표 구분)
            strategies_list = [s.strip() for s in strategies_raw.split(',') if s.strip()]
        else:
            # 리스트로 받은 경우
            strategies_list = strategies_raw if strategies_raw else []
        strategies_str = ','.join(strategies_list) if strategies_list else None

        # watched_symbols 테이블에 필요한 컬럼 추가 (기존 테이블에 없을 경우)
        add_columns = """
            ALTER TABLE watched_symbols
            ADD COLUMN IF NOT EXISTS strategies VARCHAR(200);

            ALTER TABLE watched_symbols
            ADD COLUMN IF NOT EXISTS notes TEXT;
        """

        with conn.cursor() as cur:
            # 컬럼 추가 (오류 무시)
            try:
                cur.execute(add_columns)
            except Exception:
                pass

            added = 0
            updated = 0
            new_strategies = set(strategies_list) if strategies_list else set()

            for symbol in symbols:
                # 기존 심볼 확인
                cur.execute("SELECT strategies FROM watched_symbols WHERE symbol = %s", (symbol,))
                existing = cur.fetchone()

                if existing is not None:
                    # 기존 심볼이 있음 - 전략 병합
                    existing_strategies = set()
                    if existing[0]:
                        existing_strategies = set(s.strip() for s in existing[0].split(',') if s.strip())

                    merged_strategies = existing_strategies | new_strategies
                    merged_str = ','.join(sorted(merged_strategies)) if merged_strategies else None

                    logger.info(f"📝 {symbol}: 기존 전략 {existing_strategies} + 새 전략 {new_strategies} = {merged_strategies}")

                    cur.execute("""
                        UPDATE watched_symbols
                        SET strategies = %s, notes = %s, enabled = true, updated_at = NOW()
                        WHERE symbol = %s
                    """, (merged_str, notes, symbol))
                    updated += 1
                else:
                    # 새로 추가
                    strategies_str = ','.join(sorted(new_strategies)) if new_strategies else None
                    cur.execute("""
                        INSERT INTO watched_symbols (symbol, timeframe, enabled, strategies, notes)
                        VALUES (%s, '15m', true, %s, %s)
                    """, (symbol, strategies_str, notes))
                    added += 1

            conn.commit()

        conn.close()

        logger.info(f"✅ 심볼 처리 완료: 추가 {added}개, 업데이트(전략 병합) {updated}개 - {symbols}")

        return jsonify({
            'success': True,
            'added': added,
            'updated': updated,
            'symbols': symbols
        })

    except Exception as e:
        logger.error(f"심볼 추가 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/watch_symbols/<symbol>', methods=['DELETE'])
def remove_watch_symbol(symbol):
    """분석 대상에서 심볼 제거 (watched_symbols 테이블 사용)"""
    try:
        conn = get_db_connection()

        query = """
            UPDATE watched_symbols
            SET enabled = false
            WHERE symbol = %s
        """

        with conn.cursor() as cur:
            cur.execute(query, (symbol,))
            conn.commit()

        conn.close()

        return jsonify({'success': True, 'symbol': symbol})

    except Exception as e:
        logger.error(f"심볼 제거 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/candles')
def get_candles():
    """캔들 데이터 조회 (차트용)"""
    try:
        symbol = request.args.get('symbol')
        tf = request.args.get('tf', '1h')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')

        if not symbol or not start_date or not end_date:
            return jsonify({'error': 'symbol, start_date, end_date 필수'}), 400

        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')

        conn = get_db_connection()

        query = """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = %s AND tf = %s
              AND open_time >= %s AND open_time <= %s
            ORDER BY open_time ASC
        """

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (symbol, tf, start_dt, end_dt))
            data = cur.fetchall()

        conn.close()

        candles = []
        for row in data:
            candles.append({
                'time': row['open_time'].isoformat() if hasattr(row['open_time'], 'isoformat') else str(row['open_time']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume'])
            })

        return jsonify({
            'success': True,
            'candles': candles,
            'count': len(candles)
        })

    except Exception as e:
        logger.error(f"캔들 조회 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    """백테스트 실행 (스트리밍)"""
    try:
        data = request.json
        symbols = data.get('symbols', [])
        timeframes = data.get('timeframes', ['1h'])
        strategies = data.get('strategies', ['keltner_ict_turtle'])  # 전략 선택
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d')
        initial_capital = data.get('initial_capital', 10000)

        # 옵션
        risk_pct = float(data.get('risk_pct', 2.0))
        leverage = float(data.get('leverage', 1.0))

        logger.info(f"백테스트 시작: {len(symbols)}개 심볼, {len(timeframes)}개 TF, {len(strategies)}개 전략")

        def generate():
            """결과 스트리밍"""
            total = len(symbols) * len(timeframes) * len(strategies)
            current = 0

            for symbol in symbols:
                for tf in timeframes:
                    for strategy in strategies:
                        current += 1

                        # 진행 상황 전송
                        yield f"data: {json.dumps({'type': 'progress', 'current': current, 'total': total, 'symbol': symbol, 'tf': tf, 'strategy': strategy})}\n\n"

                        try:
                            # EMA Cross 전략 청산 규칙 설정 추출
                            ema_cross_exit_config = None
                            if strategy == 'ema_cross':
                                strategy_config = data.get('strategy_configs', {}).get('ema_cross', {})
                                ema_cross_exit_config = {
                                    'tp1_partial_exit_pct': strategy_config.get('tp1_partial_exit_pct', 50.0),
                                    'use_trailing_stop': strategy_config.get('use_trailing_stop', True),
                                    'exit_on_ema50_touch': strategy_config.get('exit_on_ema50_touch', True)
                                }
                            
                            # 백테스트 실행
                            result = backtester.backtest(
                                symbol=symbol,
                                tf=tf,
                                start_date=start_date,
                                end_date=end_date,
                                strategy=strategy,
                                initial_capital=initial_capital,
                                risk_pct=risk_pct,
                                leverage=leverage,
                                ema_cross_exit_config=ema_cross_exit_config
                            )

                            if result:
                                # 거래 정보 직렬화
                                trades_data = []
                                if hasattr(result, 'trades') and result.trades:
                                    for trade in result.trades:
                                        trades_data.append({
                                            'entry_time': trade.entry_time.isoformat() if hasattr(trade.entry_time, 'isoformat') else str(trade.entry_time),
                                            'entry_price': trade.entry_price,
                                            'exit_time': trade.exit_time.isoformat() if trade.exit_time and hasattr(trade.exit_time, 'isoformat') else (str(trade.exit_time) if trade.exit_time else None),
                                            'exit_price': trade.exit_price,
                                            'size': trade.size,
                                            'direction': trade.direction,
                                            'stop_loss': trade.stop_loss,
                                            'take_profit_1': trade.take_profit_1,
                                            'take_profit_2': trade.take_profit_2,
                                            'exit_reason': trade.exit_reason,
                                            'pnl': trade.pnl,
                                            'pnl_pct': trade.pnl_pct
                                        })
                                
                                # 결과 전송
                                result_data = {
                                    'symbol': result.symbol,
                                    'strategy': result.strategy,
                                    'tf': result.timeframe,
                                    'start_date': result.start_date.strftime('%Y-%m-%d') if hasattr(result.start_date, 'strftime') else str(result.start_date)[:10],
                                    'end_date': result.end_date.strftime('%Y-%m-%d') if hasattr(result.end_date, 'strftime') else str(result.end_date)[:10],
                                    'initial_capital': result.initial_capital,
                                    'final_capital': result.final_capital,
                                    'total_pnl': result.total_pnl,
                                    'total_pnl_pct': result.total_pnl_pct,
                                    'total_trades': result.total_trades,
                                    'winning_trades': result.winning_trades,
                                    'losing_trades': result.losing_trades,
                                    'win_rate': result.win_rate,
                                    'max_drawdown': result.max_drawdown,
                                    'max_drawdown_pct': result.max_drawdown_pct,
                                    'sharpe_ratio': result.sharpe_ratio,
                                    'profit_factor': result.profit_factor,
                                    'avg_win': result.avg_win,
                                    'avg_loss': result.avg_loss,
                                    'max_win': result.max_win,
                                    'max_loss': result.max_loss,
                                    'trades': trades_data  # 거래 정보 추가
                                }

                                yield f"data: {json.dumps({'type': 'result', 'result': result_data})}\n\n"

                        except Exception as e:
                            logger.error(f"{symbol} {tf} {strategy} 백테스트 오류: {e}")
                            yield f"data: {json.dumps({'type': 'error', 'symbol': symbol, 'tf': tf, 'strategy': strategy, 'error': str(e)})}\n\n"
                            continue

            # 완료
            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        logger.error(f"백테스트 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/compare', methods=['POST'])
def compare_strategies():
    """
    여러 전략 비교 백테스트 (동기 방식)

    Request body:
    {
        "symbol": "BTCUSDT",
        "tf": "1h",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "strategies": ["ema_cross", "ict", "rsi", "bollinger", "keltner_ict_turtle"]
    }
    """
    try:
        data = request.json
        symbol = data.get('symbol')
        tf = data.get('tf', '1h')
        start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d')
        end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d')
        strategies = data.get('strategies', list(backtester.get_available_strategies().keys()))
        initial_capital = data.get('initial_capital', 10000)

        results = {}

        for strategy in strategies:
            try:
                result = backtester.backtest(
                    symbol=symbol,
                    tf=tf,
                    start_date=start_date,
                    end_date=end_date,
                    strategy=strategy,
                    initial_capital=initial_capital
                )

                if result:
                    results[strategy] = {
                        'total_pnl_pct': result.total_pnl_pct,
                        'win_rate': result.win_rate,
                        'total_trades': result.total_trades,
                        'sharpe_ratio': result.sharpe_ratio,
                        'profit_factor': result.profit_factor,
                        'max_drawdown_pct': result.max_drawdown_pct
                    }

            except Exception as e:
                logger.error(f"{strategy} 비교 오류: {e}")
                results[strategy] = {'error': str(e)}

        # 수익률 순 정렬
        sorted_results = dict(sorted(
            results.items(),
            key=lambda x: x[1].get('total_pnl_pct', -999),
            reverse=True
        ))

        return jsonify({
            'symbol': symbol,
            'tf': tf,
            'period': f"{data.get('start_date')} ~ {data.get('end_date')}",
            'results': sorted_results
        })

    except Exception as e:
        logger.error(f"전략 비교 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest/add_to_watch', methods=['POST'])
def add_backtest_results_to_watch():
    """
    백테스트 결과 중 조건 충족 심볼을 분석 대상에 추가

    Request body:
    {
        "results": [
            {"symbol": "BTCUSDT", "strategy": "ict", "total_pnl_pct": 15.5, ...},
            {"symbol": "ETHUSDT", "strategy": "ict", "total_pnl_pct": 10.2, ...}
        ],
        "min_pnl_pct": 5.0,
        "min_win_rate": 50.0,
        "min_profit_factor": 1.0,
        "notes": "백테스트 상위 심볼"
    }
    """
    try:
        data = request.json
        results = data.get('results', [])
        min_pnl_pct = data.get('min_pnl_pct', 0)
        min_win_rate = data.get('min_win_rate', 0)
        min_profit_factor = data.get('min_profit_factor', 0)
        notes = data.get('notes', '백테스트 기반 선정')

        # 필터링 및 전략 정보 수집
        symbol_strategies = {}  # symbol -> set of strategies
        for r in results:
            if (r.get('total_pnl_pct', 0) >= min_pnl_pct and
                r.get('win_rate', 0) >= min_win_rate and
                r.get('profit_factor', 0) >= min_profit_factor):
                symbol = r.get('symbol')
                strategy = r.get('strategy', '')
                if symbol not in symbol_strategies:
                    symbol_strategies[symbol] = set()
                if strategy:
                    symbol_strategies[symbol].add(strategy)

        filtered_symbols = list(symbol_strategies.keys())

        if not filtered_symbols:
            return jsonify({
                'success': True,
                'added': 0,
                'symbols': [],
                'message': '조건을 충족하는 심볼이 없습니다'
            })

        # DB에 추가 (watched_symbols 테이블 사용)
        conn = get_db_connection()

        # watched_symbols 테이블에 필요한 컬럼 추가 (기존 테이블에 없을 경우)
        add_columns = """
            ALTER TABLE watched_symbols
            ADD COLUMN IF NOT EXISTS strategies VARCHAR(200);

            ALTER TABLE watched_symbols
            ADD COLUMN IF NOT EXISTS notes TEXT;
        """

        with conn.cursor() as cur:
            # 컬럼 추가 (오류 무시)
            try:
                cur.execute(add_columns)
                conn.commit()
            except Exception:
                conn.rollback()

            for symbol in filtered_symbols:
                new_strategies = symbol_strategies.get(symbol, set())
                new_notes = f"{notes} (PnL>={min_pnl_pct}%, WR>={min_win_rate}%, PF>={min_profit_factor})"

                # 기존 심볼의 전략 조회
                cur.execute("SELECT strategies FROM watched_symbols WHERE symbol = %s", (symbol,))
                existing = cur.fetchone()

                if existing is not None:
                    # 기존 심볼이 있음 - 전략 병합
                    existing_strategies = set()
                    if existing[0]:
                        existing_strategies = set(s.strip() for s in existing[0].split(',') if s.strip())
                    merged_strategies = existing_strategies | new_strategies
                    strategies_str = ','.join(sorted(merged_strategies))

                    logger.info(f"📝 {symbol}: 기존 전략 {existing_strategies} + 새 전략 {new_strategies} = {merged_strategies}")

                    # 업데이트
                    cur.execute("""
                        UPDATE watched_symbols
                        SET strategies = %s, notes = %s, enabled = true, updated_at = NOW()
                        WHERE symbol = %s
                    """, (strategies_str, new_notes, symbol))
                else:
                    # 새로 추가
                    strategies_str = ','.join(sorted(new_strategies))
                    logger.info(f"➕ {symbol}: 새로 추가 - 전략 {new_strategies}")
                    cur.execute("""
                        INSERT INTO watched_symbols (symbol, timeframe, enabled, strategies, notes)
                        VALUES (%s, '15m', true, %s, %s)
                    """, (symbol, strategies_str, new_notes))

            conn.commit()

        conn.close()

        logger.info(f"✅ {len(filtered_symbols)}개 심볼을 분석 대상에 추가: {filtered_symbols}")

        return jsonify({
            'success': True,
            'added': len(filtered_symbols),
            'symbols': filtered_symbols
        })

    except Exception as e:
        logger.error(f"심볼 추가 오류: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/download/<symbol>/<tf>')
def download_result(symbol, tf):
    """결과 다운로드 (JSON) - 미구현"""
    return jsonify({
        'error': 'Not implemented',
        'symbol': symbol,
        'tf': tf
    }), 501


if __name__ == '__main__':
    # 앱 시작 시 마이그레이션 실행
    migrate_watch_symbols_table()
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)
