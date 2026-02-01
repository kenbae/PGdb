"""
EMA Scanner DB Integration
ema_scanner.py에서 신호 발생 시 PostgreSQL에 저장
"""

import os
from datetime import datetime
from typing import Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """PostgreSQL 연결"""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        database=os.getenv("DB_NAME", "trading"),
        user=os.getenv("DB_USER", "user"),
        password=os.getenv("DB_PASSWORD", "password")
    )


def save_ema_signal(
    symbol: str,
    timeframe: str,
    direction: str,  # 'GOLDEN' or 'DEAD'
    signal_time: datetime,
    bar_open_time: datetime,
    ema_values: Dict[str, float],
    price_info: Dict[str, float],
    probabilities: Optional[Dict[str, Any]] = None,
    trade_plan: Optional[Dict[str, float]] = None
) -> bool:
    """
    EMA 신호를 DB에 저장
    
    Args:
        symbol: 심볼 (예: BTC/USDT:USDT)
        timeframe: 타임프레임 (예: 15m)
        direction: GOLDEN 또는 DEAD
        signal_time: 신호 발생 시각
        bar_open_time: 봉 시작 시각
        ema_values: {"ema20": x, "ema50": y, "ema100": z, "ema20_prev": a, "ema50_prev": b}
        price_info: {"close": x, "atr": y, "vwap": z}
        probabilities: {"p_long": 65, "p_short": 35, "samples": 42}
        trade_plan: {"entry": x, "sl": y, "tp1": z, "tp2": w}
    
    Returns:
        성공 여부
    """
    try:
        # 신호 ID 생성 (중복 방지)
        signal_id = f"{symbol.replace('/', '').replace(':', '')}_{timeframe}_{direction}_{int(bar_open_time.timestamp())}"
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # INSERT ... ON CONFLICT DO NOTHING (중복 방지)
        query = """
            INSERT INTO ema_signals (
                signal_id, symbol, timeframe, direction,
                signal_time, bar_open_time,
                ema20, ema50, ema100, ema20_prev, ema50_prev,
                close_price, atr, vwap,
                prob_long, prob_short, prob_samples,
                entry_price, stop_loss, take_profit_1, take_profit_2
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (signal_id) DO NOTHING
            RETURNING id
        """
        
        values = (
            signal_id,
            symbol,
            timeframe,
            direction,
            signal_time,
            bar_open_time,
            ema_values.get("ema20"),
            ema_values.get("ema50"),
            ema_values.get("ema100"),
            ema_values.get("ema20_prev"),
            ema_values.get("ema50_prev"),
            price_info.get("close"),
            price_info.get("atr"),
            price_info.get("vwap"),
            probabilities.get("p_long") if probabilities else None,
            probabilities.get("p_short") if probabilities else None,
            probabilities.get("samples") if probabilities else None,
            trade_plan.get("entry") if trade_plan else None,
            trade_plan.get("sl") if trade_plan else None,
            trade_plan.get("tp1") if trade_plan else None,
            trade_plan.get("tp2") if trade_plan else None,
        )
        
        cur.execute(query, values)
        result = cur.fetchone()
        
        conn.commit()
        cur.close()
        conn.close()
        
        # 중복이면 None 반환, 새로 삽입되면 ID 반환
        return result is not None
        
    except Exception as e:
        print(f"[ERROR] save_ema_signal failed: {type(e).__name__}: {e}")
        return False


def get_recent_signals(limit: int = 20) -> list:
    """최근 신호 조회"""
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        query = """
            SELECT *
            FROM ema_signals
            ORDER BY signal_time DESC
            LIMIT %s
        """
        
        cur.execute(query, (limit,))
        results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        return [dict(row) for row in results]
        
    except Exception as e:
        print(f"[ERROR] get_recent_signals failed: {type(e).__name__}: {e}")
        return []


# ============================================================
# ema_scanner.py에 추가할 코드 예시
# ============================================================
"""
ema_scanner.py의 scan_once() 함수에서 신호 발생 시:

# GOLDEN CROSS 발생
if golden:
    # ... 기존 텔레그램 전송 코드 ...
    
    # DB 저장 추가
    from ema_db_helper import save_ema_signal
    
    saved = save_ema_signal(
        symbol=sym,
        timeframe=timeframe,
        direction='GOLDEN',
        signal_time=datetime.now(tz=KST),
        bar_open_time=datetime.fromtimestamp(bar_open_ms / 1000, tz=KST),
        ema_values={
            "ema20": m['ema20'],
            "ema50": m['ema50'],
            "ema100": m['ema100'],
            "ema20_prev": m['ema20_prev'],
            "ema50_prev": m['ema50_prev']
        },
        price_info={
            "close": float(df['close'].iloc[-1]),
            "atr": float(atr(df, 14).iloc[-1]),
            "vwap": vwap_rolling(df, 50)
        },
        probabilities={
            "p_long": prob_data.get('p_long'),
            "p_short": prob_data.get('p_short'),
            "samples": prob_data.get('samples')
        },
        trade_plan={
            "entry": trade_plan.get('entry'),
            "sl": trade_plan.get('sl'),
            "tp1": trade_plan.get('tp1'),
            "tp2": trade_plan.get('tp2')
        }
    )
    
    if saved:
        log("debug", f"{sym} 신호 DB 저장 완료", args)
    else:
        log("debug", f"{sym} 신호 DB 저장 실패 (중복 또는 에러)", args)

# DEAD CROSS도 동일하게
"""
