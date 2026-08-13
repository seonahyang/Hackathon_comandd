-- ============================================================================
--  지역 활성도 지수 컬럼 추가
--  적립금 차등 기준을 '거리 + 리뷰수' 에서 '지역 활성도 지수' 로 교체한다.
--
--  실행: Supabase 대시보드 > SQL Editor > New query > 전체 붙여넣고 Run
--  안전: IF NOT EXISTS 라 여러 번 돌려도 문제 없음
-- ============================================================================

BEGIN;

ALTER TABLE cafes ADD COLUMN IF NOT EXISTS region_state VARCHAR(8) NOT NULL DEFAULT '보통';
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS region_index DOUBLE PRECISION;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS region_rank  INTEGER;

COMMENT ON COLUMN cafes.region_state IS '과밀 / 보통 / 침체. data/region_index.csv 기준';
COMMENT ON COLUMN cafes.region_index IS '지역 활성도 종합지수 0.010(침체)~0.920(과밀). 내비검색+방문객+소비액';
COMMENT ON COLUMN cafes.region_rank  IS '42개 읍면동 중 활성도 순위 (1=가장 과밀)';

ALTER TABLE cafes DROP CONSTRAINT IF EXISTS cafes_region_state_check;
ALTER TABLE cafes ADD  CONSTRAINT cafes_region_state_check
    CHECK (region_state IN ('과밀', '보통', '침체', '미분류'));

-- 지도 쿼리에서 소외 상권 필터가 자주 걸린다
CREATE INDEX IF NOT EXISTS ix_cafes_region_state ON cafes (region_state);
CREATE INDEX IF NOT EXISTS ix_cafes_region_index ON cafes (region_index);

COMMIT;

-- ============================================================================
--  다음 순서
--    1) 이 SQL 실행
--    2) python -m scripts.ingest_region_index --dry-run   (매칭률 확인)
--    3) python -m scripts.ingest_region_index             (실제 반영)
-- ============================================================================

-- 검증 — 2번까지 끝낸 뒤 실행하세요
SELECT region_state,
       COUNT(*)                        AS 매장수,
       ROUND(MIN(region_index)::numeric, 3) AS 최저지수,
       ROUND(MAX(region_index)::numeric, 3) AS 최고지수
FROM cafes
GROUP BY region_state
ORDER BY 매장수 DESC;
