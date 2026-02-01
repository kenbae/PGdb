"""
결과 업데이트 백그라운드 작업 테스트

백그라운드 작업을 직접 실행하여 pending 신호 업데이트 확인
"""

import sys
import os
from pathlib import Path

# UTF-8 출력 설정 (Windows)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
from dotenv import load_dotenv
from sqlalchemy import create_engine
from core.config_loader import get_config
from database.signals_repo import SignalsRepo
import pandas as pd
from datetime import datetime

# .env 로드
load_dotenv()

async def test_result_updater():
    """결과 업데이트 테스트"""
    print("📊 결과 업데이트 테스트 시작\n")

    # Config 로드
    config = get_config()

    # DB 연결
    db_url = f"postgresql://{config.get('db.user')}:{config.get('db.password')}@{config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}"
    db_engine = create_engine(db_url)

    print(f"✅ DB 연결: {config.get('db.host')}:{config.get('db.port')}/{config.get('db.name')}\n")

    # Repository 초기화
    signals_repo = SignalsRepo(db_engine)

    # 1. pending 신호 조회
    print("1️⃣ pending 신호 조회 중...\n")
    pending_signals = signals_repo.get_pending_signals(hours=72)

    if not pending_signals:
        print("📭 업데이트할 pending 신호 없음\n")
        print("💡 신호가 없으면 먼저 api_server.py를 실행하여 신호를 생성하세요.")
        return

    print(f"📋 {len(pending_signals)}개 pending 신호 발견:\n")
    for signal in pending_signals[:5]:  # 처음 5개만 출력
        print(f"  - {signal['signal_id']}")
        print(f"    심볼: {signal['symbol']}, 타입: {signal['signal_type']}")
        print(f"    진입가: ${signal['entry_price']:,.2f}, 손절: ${signal['stop_loss']:,.2f}")
        print(f"    생성: {signal['created_at']}")
        print()

    # 2. 각 신호 업데이트 시도
    print("\n2️⃣ 신호 업데이트 시도...\n")
    updated_count = 0

    for i, signal in enumerate(pending_signals[:3], 1):  # 처음 3개만 테스트
        try:
            signal_id = signal['signal_id']
            symbol = signal['symbol']
            signal_type = signal['signal_type']
            entry_price = float(signal['entry_price'])
            stop_loss = float(signal['stop_loss'])
            take_profit_1 = signal.get('take_profit_1')
            take_profit_2 = signal.get('take_profit_2')
            created_at = signal['created_at']

            print(f"[{i}] {signal_id}")
            print(f"    캔들 데이터 조회 중...")

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
                print(f"    ⏭️  신호 이후 캔들 없음\n")
                continue

            print(f"    ✅ {len(df)}개 캔들 조회")

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
                    # MFE, MAE 계산
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
                    # MFE, MAE 계산
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
                duration_minutes = int((exit_time - created_at).total_seconds() / 60)

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
                    'notes': 'Test update by test_result_updater.py'
                }

                if signals_repo.save_result(result_data):
                    updated_count += 1
                    print(f"    ✅ 업데이트 성공:")
                    print(f"       상태: {status}")
                    print(f"       종료가: ${exit_price:,.2f}")
                    print(f"       PnL: {pnl_percent:+.2f}%")
                    print(f"       R-Multiple: {r_multiple:.2f}")
                    print(f"       MFE: {mfe:.2f}%, MAE: {mae:.2f}%")
                    print(f"       지속시간: {duration_minutes}분")
                else:
                    print(f"    ❌ 업데이트 실패")
            else:
                print(f"    ⏳ 아직 TP/SL 미달성 (MFE: {mfe:.2f}%, MAE: {mae:.2f}%)")

            print()

        except Exception as e:
            print(f"    ❌ 오류: {e}\n")
            continue

    # 3. 결과 요약
    print(f"\n3️⃣ 결과 요약\n")
    print(f"  - 전체 pending 신호: {len(pending_signals)}개")
    print(f"  - 테스트한 신호: {min(3, len(pending_signals))}개")
    print(f"  - 업데이트된 신호: {updated_count}개")
    print()

    # 4. 통계 조회
    print("4️⃣ 전체 통계\n")
    stats = signals_repo.get_statistics(days=30)

    print(f"  - 전체 신호: {stats.get('total_signals', 0)}개")
    print(f"  - 승리: {stats.get('wins', 0)}개")
    print(f"  - 손실: {stats.get('losses', 0)}개")
    print(f"  - 승률: {stats.get('win_rate', 0) * 100:.1f}%")
    print(f"  - 평균 PnL: {stats.get('avg_pnl_percent', 0):.2f}%")
    print(f"  - 평균 R-Multiple: {stats.get('avg_r_multiple', 0):.2f}")
    print()

    db_engine.dispose()
    print("✅ 테스트 완료!")


if __name__ == "__main__":
    asyncio.run(test_result_updater())
