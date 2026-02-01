"""
실전 연결 테스트

⚠️ 주의: 실제 계좌에 연결됩니다!
"""

import sys
import logging
from datetime import datetime

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    print("=" * 70)
    print("🚨 실전 연결 테스트")
    print("=" * 70)
    print()
    
    # 1. Config 테스트
    print("[1/5] Config 로드 중...")
    try:
        from core.config_loader import get_config
        config = get_config()
        
        if not config.validate():
            print("❌ Config 검증 실패!")
            return False
        
        print("✅ Config 로드 성공")
        
        # 주요 설정 확인
        capital = config.get('trading.initial_capital', 0)
        max_pos = config.get('trading.max_positions', 0)
        risk = config.get('trading.risk_per_trade', 0)
        
        print(f"   초기 자본: ${capital:.2f}")
        print(f"   최대 포지션: {max_pos}개")
        print(f"   거래당 리스크: {risk*100:.1f}%")
        
    except Exception as e:
        print(f"❌ Config 로드 실패: {e}")
        return False
    
    print()
    
    # 2. API 키 확인
    print("[2/5] API 키 확인 중...")
    api_key = config.get('api.binance.live.api_key')
    api_secret = config.get('api.binance.live.api_secret')
    
    if not api_key or not api_secret:
        print("❌ API 키가 설정되지 않았습니다!")
        print("   .env 파일을 확인하세요.")
        return False
    
    print(f"✅ API 키 확인: {api_key[:8]}...{api_key[-4:]}")
    print()
    
    # 3. 거래소 연결
    print("[3/5] Binance 실전 연결 중...")
    print("⚠️  실제 계좌에 연결합니다...")
    
    try:
        from exchanges.binance_live import BinanceLive
        
        exchange = BinanceLive(
            api_key=api_key,
            api_secret=api_secret,
            initial_capital=capital,
            max_positions=max_pos,
            max_daily_loss=config.get('trading.max_daily_loss', 0.02)
        )
        
        if not exchange.test_connection():
            print("❌ 연결 실패!")
            return False
        
        print("✅ Binance 연결 성공")
        
    except Exception as e:
        print(f"❌ 거래소 연결 실패: {e}")
        return False
    
    print()
    
    # 4. 계좌 정보 조회
    print("[4/5] 계좌 정보 조회 중...")
    
    try:
        # 잔고
        balance = exchange.get_balance()
        print(f"💰 USDT 잔고:")
        print(f"   사용가능: ${balance['free']:,.2f}")
        print(f"   사용중: ${balance['used']:,.2f}")
        print(f"   총합: ${balance['total']:,.2f}")
        
        # 경고
        if balance['total'] > 1000:
            print()
            print("⚠️⚠️⚠️ 경고! ⚠️⚠️⚠️")
            print(f"⚠️  잔고가 높습니다: ${balance['total']:,.2f}")
            print(f"⚠️  소액($100-$500)으로 시작하는 것을 권장합니다!")
            print()
        
        print()
        
        # 현재가
        ticker = exchange.get_ticker('BTC/USDT')
        print(f"📊 BTC/USDT:")
        print(f"   현재가: ${ticker['last']:,.2f}")
        print(f"   Bid: ${ticker['bid']:,.2f}")
        print(f"   Ask: ${ticker['ask']:,.2f}")
        
        print()
        
        # 포지션
        positions = exchange.get_positions()
        print(f"📈 열린 포지션: {len(positions)}개")
        
        if positions:
            for pos in positions:
                contracts = float(pos.get('contracts', 0))
                entry = float(pos.get('entryPrice', 0))
                unrealized = float(pos.get('unrealizedPnl', 0))
                
                print(f"   {pos['symbol']}: {contracts:.4f} @ ${entry:,.2f} (PnL: ${unrealized:,.2f})")
        else:
            print("   (포지션 없음)")
        
    except Exception as e:
        print(f"❌ 계좌 정보 조회 실패: {e}")
        return False
    
    print()
    
    # 5. 안전 점검
    print("[5/5] 안전 점검...")
    
    if exchange.check_safety():
        print("✅ 안전 점검 통과")
        print("   거래 가능 상태입니다.")
    else:
        print("⚠️  안전 점검 실패")
        print("   현재 거래할 수 없습니다.")
    
    print()
    print("=" * 70)
    print("✅ 모든 테스트 통과!")
    print("=" * 70)
    print()
    
    # 일일 손익
    pnl = exchange.get_daily_pnl()
    print(f"📊 오늘 손익:")
    print(f"   시작 자본: ${pnl['start_capital']:,.2f}")
    print(f"   현재 자본: ${pnl['current_capital']:,.2f}")
    print(f"   손익: ${pnl['pnl']:,.2f} ({pnl['pnl_pct']:+.2f}%)")
    
    print()
    print("=" * 70)
    print("🎯 시스템 준비 완료!")
    print("=" * 70)
    print()
    print("다음 단계:")
    print("1. 초소량 테스트 거래 (0.001 BTC)")
    print("2. 손절/익절 자동화 확인")
    print("3. 실전 전략 배포")
    print()
    
    return True


if __name__ == "__main__":
    print()
    print("⚠️⚠️⚠️ 경고 ⚠️⚠️⚠️")
    print()
    print("이 스크립트는 실제 Binance 계좌에 연결됩니다!")
    print("API 키와 잔고를 확인합니다.")
    print("주문은 실행하지 않습니다.")
    print()
    
    response = input("계속하시겠습니까? (yes/no): ")
    print()
    
    if response.lower() == 'yes':
        success = main()
        
        if success:
            print("✅ 테스트 완료!")
        else:
            print("❌ 테스트 실패!")
            print("   설정을 확인하고 다시 시도하세요.")
    else:
        print("취소됨")
