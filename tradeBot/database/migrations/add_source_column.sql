-- positions 테이블에 source 컬럼 추가
-- 2024-01-22

-- source 컬럼 추가
ALTER TABLE tradebot_positions
ADD COLUMN IF NOT EXISTS source VARCHAR(20) DEFAULT 'binance';

-- 코멘트 추가
COMMENT ON COLUMN tradebot_positions.source IS '데이터 출처: binance, csv, manual';
