#!/usr/bin/env python3
"""
통합 파이프라인 실행 스크립트
ingest -> indicators -> signals 순서로 실행
"""

import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def run_step(step_name: str, module_name: str, symbols_filter=None):
    """
    개별 파이프라인 단계 실행
    
    Args:
        step_name: 단계 이름 (표시용)
        module_name: 실행할 모듈 이름
        symbols_filter: 심볼 필터 (선택)
    
    Returns:
        bool: 성공 여부
    """
    try:
        logger.info(f"{'='*60}")
        logger.info(f"{step_name} 시작")
        logger.info(f"{'='*60}")
        
        if module_name == "run_ingest":
            # Ingest는 아직 개선 전이라고 가정
            import run_ingest
            run_ingest.main()  # 또는 적절한 함수명
            
        elif module_name == "run_indicators":
            from run_indicators import run_indicators
            run_indicators(symbols_filter=symbols_filter)
            
        elif module_name == "run_signals":
            from run_signals import run_signals
            run_signals(symbols_filter=symbols_filter)
        
        logger.info(f"✓ {step_name} 완료")
        return True
        
    except Exception as e:
        logger.error(f"✗ {step_name} 실패: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='암호화폐 트레이딩 파이프라인 통합 실행',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
실행 순서:
  1. run_ingest.py    - 데이터 수집
  2. run_indicators.py - 기술적 지표 계산
  3. run_signals.py   - 매매 신호 생성

사용 예시:
  # 전체 파이프라인 실행
  python run_pipeline.py
  
  # 특정 심볼만 처리
  python run_pipeline.py --symbol BTCUSDT ETHUSDT
  
  # 특정 단계만 실행
  python run_pipeline.py --steps indicators signals
  
  # 에러 발생 시 중단하지 않고 계속 진행
  python run_pipeline.py --no-fail-fast
        """
    )
    
    parser.add_argument(
        '--symbol', '-s',
        nargs='+',
        help='처리할 심볼 (예: BTCUSDT). 여러 개 지정 가능',
        metavar='SYMBOL'
    )
    
    parser.add_argument(
        '--steps',
        nargs='+',
        choices=['ingest', 'indicators', 'signals'],
        default=['ingest', 'indicators', 'signals'],
        help='실행할 단계 선택 (기본값: 모든 단계)',
        metavar='STEP'
    )
    
    parser.add_argument(
        '--no-fail-fast',
        action='store_true',
        help='한 단계가 실패해도 다음 단계 계속 실행'
    )
    
    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='설정 파일 경로 (기본값: config.yaml)',
        metavar='PATH'
    )
    
    args = parser.parse_args()
    
    # 심볼 필터 처리
    symbols = None
    if args.symbol:
        symbols = args.symbol[0] if len(args.symbol) == 1 else args.symbol
        logger.info(f"지정된 심볼: {symbols}")
    
    # 시작 시간 기록
    start_time = datetime.now()
    logger.info(f"{'='*60}")
    logger.info(f"파이프라인 시작: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"실행 단계: {', '.join(args.steps)}")
    logger.info(f"{'='*60}")
    
    # 파이프라인 단계 정의
    pipeline_steps = []
    if 'ingest' in args.steps:
        pipeline_steps.append(('데이터 수집', 'run_ingest', None))  # ingest는 심볼 필터 없음
    if 'indicators' in args.steps:
        pipeline_steps.append(('지표 계산', 'run_indicators', symbols))
    if 'signals' in args.steps:
        pipeline_steps.append(('신호 생성', 'run_signals', symbols))
    
    # 파이프라인 실행
    results = {}
    for i, (step_name, module_name, step_symbols) in enumerate(pipeline_steps, 1):
        logger.info(f"\n[{i}/{len(pipeline_steps)}] {step_name}")
        success = run_step(step_name, module_name, step_symbols)
        results[step_name] = success
        
        if not success and not args.no_fail_fast:
            logger.error(f"파이프라인 중단: {step_name} 단계 실패")
            sys.exit(1)
    
    # 종료 시간 및 결과 요약
    end_time = datetime.now()
    duration = end_time - start_time
    
    logger.info(f"\n{'='*60}")
    logger.info(f"파이프라인 완료: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"소요 시간: {duration}")
    logger.info(f"{'='*60}")
    
    # 결과 요약
    logger.info("\n실행 결과:")
    for step_name, success in results.items():
        status = "✓ 성공" if success else "✗ 실패"
        logger.info(f"  {step_name}: {status}")
    
    # 실패한 단계가 있으면 종료 코드 1
    if not all(results.values()):
        logger.warning("\n일부 단계가 실패했습니다.")
        sys.exit(1)
    else:
        logger.info("\n모든 단계가 성공적으로 완료되었습니다.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중단되었습니다.")
        sys.exit(130)
    except Exception as e:
        logger.error(f"\n예상치 못한 오류: {e}")
        sys.exit(1)
