"""
포지션 종료 스크립트

모든 열린 포지션을 종료합니다
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)


def main():
    print("=" * 70)
    print("🚨 포지션 종료")
    print("=" * 70)
    print()
    
    # 초기화
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
        
    except Exception as e:
        print(f"❌ 초기화 실패: {e}")
        return False
    
    # 현재 포지션 확인
    print("📈 현재 포지션 확인 중...")
    print()
    
    try:
        positions = exchange.get_positions()
        
        if not positions:
            print("✅ 열린 포지션이 없습니다.")
            return True
        
        print(f"발견된 포지션: {len(positions)}개")
        print()
        
        for i, pos in enumerate(positions, 1):
            symbol = pos['symbol']
            contracts = float(pos.get('contracts', 0))
            entry = float(pos.get('entryPrice', 0))
            unrealized = float(pos.get('unrealizedPnl', 0))
            
            print(f"{i}. {symbol}")
            print(f"   수량: {contracts:.4f}")
            print(f"   진입가: ${entry:,.2f}")
            print(f"   미실현 손익: ${unrealized:,.2f}")
            print()
        
        # 확인
        print("⚠️  모든 포지션을 종료하시겠습니까?")
        response = input("계속하시겠습니까? (yes/no): ")
        print()
        
        if response.lower() != 'yes':
            print("취소됨")
            return False
        
        # 종료
        print("포지션 종료 중...")
        print()
        
        closed_count = 0
        for pos in positions:
            symbol = pos['symbol']
            
            if exchange.close_position(symbol):
                closed_count += 1
                print(f"✅ {symbol} 종료 완료")
            else:
                print(f"❌ {symbol} 종료 실패")
        
        print()
        print("=" * 70)
        print(f"✅ {closed_count}개 포지션 종료 완료")
        print("=" * 70)
        
        # 최종 손익
        pnl = exchange.get_daily_pnl()
        print()
        print(f"📊 오늘 손익:")
        print(f"   ${pnl['pnl']:,.2f} ({pnl['pnl_pct']:+.2f}%)")
        
        return True
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        return False


if __name__ == "__main__":
    print()
    print("⚠️  이 스크립트는 모든 포지션을 종료합니다!")
    print()
    
    response = input("시작하시겠습니까? (yes/no): ")
    print()
    
    if response.lower() == 'yes':
        success = main()
        
        if success:
            print("\n✅ 완료!")
        else:
            print("\n❌ 실패!")
    else:
        print("취소됨")
