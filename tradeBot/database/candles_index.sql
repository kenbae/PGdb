-- candles 테이블에 UNIQUE 제약 조건 추가
-- 이 쿼리를 실행하면 ON CONFLICT 사용 가능

-- 기존 중복 데이터 확인 (실행 전 확인용)
-- SELECT symbol, tf, open_time, COUNT(*)
-- FROM candles
-- GROUP BY symbol, tf, open_time
-- HAVING COUNT(*) > 1;

-- 중복 데이터가 있다면 먼저 정리 필요
-- DELETE FROM candles a USING candles b
-- WHERE a.ctid < b.ctid
-- AND a.symbol = b.symbol
-- AND a.tf = b.tf
-- AND a.open_time = b.open_time;

-- UNIQUE 제약 조건 추가
ALTER TABLE candles
ADD CONSTRAINT candles_symbol_tf_open_time_unique
UNIQUE (symbol, tf, open_time);

-- 또는 UNIQUE 인덱스 생성 (이미 인덱스가 있다면 이 방법 사용)
-- CREATE UNIQUE INDEX IF NOT EXISTS idx_candles_symbol_tf_open_time
-- ON candles (symbol, tf, open_time);
