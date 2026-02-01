-- 신호 테이블에 포지션 매칭 필드 추가
-- 2024-01-22

-- matched_position_id 컬럼 추가
ALTER TABLE tradebot_signals
ADD COLUMN IF NOT EXISTS matched_position_id VARCHAR(50);

-- matched_at 컬럼 추가
ALTER TABLE tradebot_signals
ADD COLUMN IF NOT EXISTS matched_at TIMESTAMP;

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_tradebot_signals_matched_position_id
ON tradebot_signals(matched_position_id);

-- 코멘트 추가
COMMENT ON COLUMN tradebot_signals.matched_position_id IS '매칭된 실제 포지션 ID (tradebot_positions.position_id)';
COMMENT ON COLUMN tradebot_signals.matched_at IS '포지션 매칭된 시간';
