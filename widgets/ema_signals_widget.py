"""
EMA Signals Widget
최근 EMA 크로스 신호 5개를 페이징하여 표시
"""

from widgets.base_widget import BaseWidget
from sqlalchemy import text
from datetime import datetime


class EmaSignalsWidget(BaseWidget):
    """EMA 크로스 신호 위젯"""
    
    @property
    def widget_id(self) -> str:
        return "ema_signals"
    
    @property
    def widget_name(self) -> str:
        return "EMA Signals"
    
    @property
    def widget_icon(self) -> str:
        return "📡"
    
    @property
    def widget_color(self) -> str:
        return "purple"
    
    @property
    def widget_size(self) -> str:
        return "large"
    
    @property
    def widget_category(self) -> str:
        return "signals"
    
    def get_data(self):
        """최근 EMA 신호 조회 + 스캐너 상태"""
        
        # 최근 20개 신호 조회 (페이징용)
        query = text("""
            SELECT 
                id,
                signal_id,
                symbol,
                timeframe,
                direction,
                signal_time,
                ema20,
                ema50,
                ema100,
                close_price,
                prob_long,
                prob_short,
                prob_samples,
                entry_price,
                stop_loss,
                take_profit_1,
                take_profit_2
            FROM ema_signals
            ORDER BY signal_time DESC
            LIMIT 20
        """)
        
        # 스캐너 최근 상태 조회
        status_query = text("""
            SELECT 
                timeframe,
                scan_time,
                total_symbols,
                signals_fired,
                cross_detected,
                scan_duration_sec
            FROM ema_scanner_status
            ORDER BY scan_time DESC
            LIMIT 1
        """)
        
        with self.engine.connect() as conn:
            results = conn.execute(query).fetchall()
            try:
                status_results = conn.execute(status_query).fetchall()
            except:
                status_results = []
        
        signals = []
        for row in results:
            # TradingView 링크 생성 (앱 열기)
            symbol_base = row[2].replace("/", "").replace(":USDT", "")
            # TradingView 앱 URL 스킴 (데스크톱/모바일 앱 열기)
            tv_app_link = f"tradingview://chart?symbol=BINANCE:{symbol_base}.P"
            # 웹 백업 링크 (앱이 없을 경우)
            tv_web_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol_base}.P"
            
            # 신호 시간 포맷 (UTC → KST)
            signal_time = row[5]
            if signal_time.tzinfo is not None:
                from datetime import timezone
                signal_time = signal_time.astimezone(timezone.utc).replace(tzinfo=None)
            
            # KST는 UTC+9
            from datetime import timedelta
            signal_time_kst = signal_time + timedelta(hours=9)
            time_str = signal_time_kst.strftime('%m-%d %H:%M')
            
            # 승률 색상 결정
            prob = row[10] if row[4] == 'GOLDEN' else row[11]  # direction에 따라
            prob_color = 'green' if prob and prob >= 60 else 'orange' if prob and prob >= 50 else 'red'
            
            signals.append({
                "id": row[0],
                "signal_id": row[1],
                "symbol": row[2],
                "timeframe": row[3],
                "direction": row[4],
                "signal_time": time_str,
                "ema20": float(row[6]) if row[6] else 0,
                "ema50": float(row[7]) if row[7] else 0,
                "ema100": float(row[8]) if row[8] else 0,
                "close_price": float(row[9]) if row[9] else 0,
                "prob_long": row[10],
                "prob_short": row[11],
                "prob_samples": row[12],
                "entry": float(row[13]) if row[13] else 0,
                "sl": float(row[14]) if row[14] else 0,
                "tp1": float(row[15]) if row[15] else 0,
                "tp2": float(row[16]) if row[16] else 0,
                "tv_app_link": tv_app_link,
                "tv_web_link": tv_web_link,
                "prob_color": prob_color,
                "prob_value": prob if prob else 0
            })
        
        # 스캐너 상태 파싱
        scanner_status = None
        if status_results:
            row = status_results[0]
            scan_time = row[1]
            if scan_time.tzinfo is not None:
                from datetime import timezone
                scan_time = scan_time.astimezone(timezone.utc).replace(tzinfo=None)
            
            # KST는 UTC+9
            from datetime import timedelta
            scan_time_kst = scan_time + timedelta(hours=9)
            
            # 마지막 스캔이 5분 이상 지났으면 "정지" 상태
            time_diff = datetime.utcnow() - scan_time
            is_active = time_diff.total_seconds() < 300  # 5분
            
            scanner_status = {
                "timeframe": row[0],
                "scan_time": scan_time_kst.strftime('%m-%d %H:%M:%S'),
                "total_symbols": row[2],
                "signals_fired": row[3],
                "cross_detected": row[4],
                "scan_duration": float(row[5]) if row[5] else 0,
                "is_active": is_active,
                "time_ago_sec": int(time_diff.total_seconds())
            }
        
        return {
            "signals": signals,
            "total": len(signals),
            "scanner_status": scanner_status,
            "widget_type": "ema_signals"
        }
