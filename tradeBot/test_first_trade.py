"""
첫 번째 테스트 거래

⚠️ 초소량 거래 (0.001 BTC ≈ $43)
⚠️ 실제 돈이 사용됩니다!
"""

import sys
import logging
from datetime import datetime
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    print("=" * 70)
    print("🚨 첫 번째 테스트 거래")
    print("=" * 70)
    print()
    
    # Config & Exchange
    print("[1/3] 준비 중...")
    
    try:
        from core.config_loader import get_config
        from exchanges.binance_live import BinanceLive
        
        config = get_config()
        
        exchange = BinanceLive(
            initial_capital=config.get('trading.initial_capital'),
            max_positions=config.get('trading.max_positions'),
            max_daily_loss=config.get('trading.max_daily_loss')
        )
        
        if not exchange.test_connection():
            print("❌ 연결 실패!")
            return False
        
        print("✅ 준비 완료")
        
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return False
    
    print()
    
    # 현재 상태 확인
    print("[2/3] 현재 상태 확인...")
    
    # 잔고
    balance = exchange.get_balance()
    print(f"💰 USDT 잔고: ${balance['free']:,.2f}")
    
    if balance['free'] < 50:
        print("❌ 잔고 부족! (최소 $50 필요)")
        return False
    
    # 현재가
    ticker = exchange.get_ticker('BTC/USDT')
    current_price = ticker['last']
    print(f"📊 BTC 현재가: ${current_price:,.2f}")
    
    # 거래 금액
    amount = 0.001  # 0.001 BTC
    value = amount * current_price
    print(f"💵 거래 금액: 0.001 BTC ≈ ${value:.2f}")
    
    # 레버리지
    leverage = 1
    print(f"⚡ 레버리지: {leverage}x")
    
    # 손절/익절 계산
    atr_multiplier = 2.5
    stop_loss = current_price * (1 - 0.02)  # 2% 손절
    take_profit_1 = current_price * (1 + 0.03)  # 3% 익절
    take_profit_2 = current_price * (1 + 0.05)  # 5% 익절
    
    print(f"🛡️ 손절가: ${stop_loss:,.2f} (-2%)")
    print(f"🎯 익절가 1: ${take_profit_1:,.2f} (+3%)")
    print(f"🎯 익절가 2: ${take_profit_2:,.2f} (+5%)")
    
    # 예상 손익
    max_loss = (current_price - stop_loss) * amount
    max_profit_1 = (take_profit_1 - current_price) * amount
    
    print()
    print(f"📊 예상 손익:")
    print(f"   최대 손실: -${max_loss:.2f}")
    print(f"   1차 익절: +${max_profit_1:.2f}")
    print(f"   리스크/보상: 1:{max_profit_1/max_loss:.2f}")
    
    print()
    
    # 안전 점검
    if not exchange.check_safety():
        print("❌ 안전 점검 실패!")
        print("   현재 거래할 수 없습니다.")
        return False
    
    print("✅ 안전 점검 통과")
    print()
    
    # 최종 확인
    print("=" * 70)
    print("⚠️⚠️⚠️ 최종 확인 ⚠️⚠️⚠️")
    print("=" * 70)
    print()
    print(f"심볼: BTC/USDT")
    print(f"방향: 매수 (LONG)")
    print(f"수량: {amount} BTC (≈ ${value:.2f})")
    print(f"레버리지: {leverage}x")
    print(f"손절: ${stop_loss:,.2f} (최대 손실: ${max_loss:.2f})")
    print(f"익절: ${take_profit_1:,.2f} (목표 수익: ${max_profit_1:.2f})")
    print()
    print("⚠️  실제 돈이 사용됩니다!")
    print()
    
    response = input("거래를 실행하시겠습니까? (yes/no): ")
    print()
    
    if response.lower() != 'yes':
        print("취소됨")
        return False
    
    # 거래 실행
    print("[3/3] 거래 실행 중...")
    print()
    
    try:
        # 1. 레버리지 설정
        print("1. 레버리지 설정...")
        if exchange.set_leverage('BTC/USDT', leverage):
            print(f"   ✅ {leverage}x 레버리지 설정 완료")
        else:
            print(f"   ⚠️  레버리지 설정 실패 (이미 설정되어 있을 수 있음)")
        
        time.sleep(1)
        
        # 2. 시장가 매수
        print()
        print("2. 시장가 매수 실행...")
        order = exchange.create_market_order(
            symbol='BTC/USDT',
            side='buy',
            amount=amount
        )
        
        if not order:
            print("   ❌ 주문 실패!")
            return False
        
        order_id = order['id']
        filled_price = order.get('price', current_price)
        
        print(f"   ✅ 주문 성공!")
        print(f"   주문 ID: {order_id}")
        print(f"   체결가: ${filled_price:,.2f}")
        
        time.sleep(2)
        
        # 3. 손절 설정
        print()
        print("3. 손절 주문 설정...")
        sl_order = exchange.set_stop_loss(
            symbol='BTC/USDT',
            side='sell',
            amount=amount,
            stop_price=stop_loss
        )
        
        if sl_order:
            print(f"   ✅ 손절 설정: ${stop_loss:,.2f}")
        else:
            print(f"   ⚠️  손절 설정 실패!")
        
        time.sleep(1)
        
        # 4. 익절 설정
        print()
        print("4. 익절 주문 설정...")
        tp_order = exchange.set_take_profit(
            symbol='BTC/USDT',
            side='sell',
            amount=amount / 2,  # 절반만
            take_profit_price=take_profit_1
        )
        
        if tp_order:
            print(f"   ✅ 익절 설정: ${take_profit_1:,.2f}")
        else:
            print(f"   ⚠️  익절 설정 실패!")
        
        print()
        print("=" * 70)
        print("✅ 거래 완료!")
        print("=" * 70)
        print()
        
        # 5. 포지션 확인
        print("📈 현재 포지션:")
        positions = exchange.get_positions()
        
        for pos in positions:
            if pos['symbol'] == 'BTCUSDT':
                contracts = float(pos.get('contracts', 0))
                entry = float(pos.get('entryPrice', 0))
                unrealized = float(pos.get('unrealizedPnl', 0))
                
                print(f"   심볼: {pos['symbol']}")
                print(f"   수량: {contracts:.4f} BTC")
                print(f"   진입가: ${entry:,.2f}")
                print(f"   미실현 손익: ${unrealized:,.2f}")
                
                # 현재가 대비
                pnl_pct = ((current_price - entry) / entry) * 100
                print(f"   수익률: {pnl_pct:+.2f}%")
        
        print()
        print("=" * 70)
        print("🎯 다음 단계")
        print("=" * 70)
        print()
        print("1. 포지션 모니터링")
        print("   - Binance 앱/웹에서 확인")
        print("   - 손절/익절 자동 실행 확인")
        print()
        print("2. 수동 종료 (필요시)")
        print("   - python close_position.py")
        print()
        print("3. 다음 거래 대기")
        print("   - ICT + AI 전략 신호 대기")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ 거래 실행 실패: {e}")
        print()
        print("긴급 조치:")
        print("1. Binance 웹/앱에서 수동 확인")
        print("2. 필요시 수동으로 포지션 종료")
        return False


if __name__ == "__main__":
    print()
    print("=" * 70)
    print("⚠️⚠️⚠️ 중요! ⚠️⚠️⚠️")
    print("=" * 70)
    print()
    print("이 스크립트는 실제 거래를 실행합니다!")
    print()
    print("거래 내용:")
    print("- 심볼: BTC/USDT")
    print("- 수량: 0.001 BTC (≈ $43)")
    print("- 레버리지: 1x")
    print("- 손절: -2%")
    print("- 익절: +3%")
    print()
    print("확인 사항:")
    print("✅ API 키 권한 확인 (거래 활성화)")
    print("✅ 잔고 충분 (최소 $50)")
    print("✅ 손실 감수 준비")
    print()
    
    response = input("시작하시겠습니까? (yes/no): ")
    print()
    
    if response.lower() == 'yes':
        success = main()
        
        if success:
            print("✅ 첫 거래 완료!")
            print()
            print("🎉 축하합니다!")
            print("실전 자동매매의 첫 걸음을 시작하셨습니다.")
        else:
            print("❌ 거래 실패!")
    else:
        print("취소됨")
