-- candles 테이블 가격 컬럼 정밀도 수정
-- 저가 코인 (PUMP, SHIB 등)의 소수점 정밀도 문제 해결
--
-- 실행 방법:
-- psql -U [user] -d [database] -f candles_precision_fix.sql
-- 또는 DBeaver/pgAdmin에서 직접 실행

-- 현재 컬럼 타입 확인 (참고용)
-- SELECT column_name, data_type, numeric_precision, numeric_scale
-- FROM information_schema.columns
-- WHERE table_name = 'candles' AND column_name IN ('open', 'high', 'low', 'close');

-- 가격 컬럼 정밀도를 DECIMAL(20, 8)로 변경
-- 4개 컬럼을 한 번에 변경하여 테이블 리라이트 1회만 발생 (훨씬 빠름)
ALTER TABLE candles
    ALTER COLUMN open TYPE DECIMAL(20, 8),
    ALTER COLUMN high TYPE DECIMAL(20, 8),
    ALTER COLUMN low TYPE DECIMAL(20, 8),
    ALTER COLUMN close TYPE DECIMAL(20, 8);

-- 변경 확인
SELECT column_name, data_type, numeric_precision, numeric_scale
FROM information_schema.columns
WHERE table_name = 'candles' AND column_name IN ('open', 'high', 'low', 'close');
