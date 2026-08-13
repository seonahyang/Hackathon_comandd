-- ============================================================================
--  카공 판정 방식 변경 마이그레이션
--  브랜드명 추측 폐기 → 리뷰 투표(가능/불가) 다수결 + 매장 넓이 투표
--
--  실행 위치: Supabase 대시보드 > SQL Editor > New query > 전체 붙여넣고 Run
--  안전성   : 전부 IF NOT EXISTS / IF EXISTS. 여러 번 돌려도 문제 없음.
--  소요     : 719건 기준 1초 미만
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. cafes — 리뷰 투표 집계 컬럼
-- ---------------------------------------------------------------------------
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS cagong_yes  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS cagong_no   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS cagong_ok   BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN cafes.cagong_yes IS '리뷰에서 카공 가능이라고 투표한 수';
COMMENT ON COLUMN cafes.cagong_no  IS '리뷰에서 카공 불가라고 투표한 수';
COMMENT ON COLUMN cafes.cagong_ok  IS 'cagong_yes > cagong_no 의 결과. 카공 필터의 유일한 기준';

-- ---------------------------------------------------------------------------
-- 2. cafes — 매장 넓이 투표
-- ---------------------------------------------------------------------------
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS size_small  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS size_medium INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS size_large  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE cafes ADD COLUMN IF NOT EXISTS size_label  VARCHAR(8);

COMMENT ON COLUMN cafes.size_label IS '넓이 투표 최빈값. small(협소)/medium(보통)/large(넓음). NULL=투표없음';

-- ---------------------------------------------------------------------------
-- 3. reviews — 투표 컬럼
-- ---------------------------------------------------------------------------
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS cagong_vote BOOLEAN;
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS size_vote   VARCHAR(8);

COMMENT ON COLUMN reviews.cagong_vote IS 'TRUE=카공 가능 / FALSE=카공 불가 / NULL=모르겠음(투표 안 함)';
COMMENT ON COLUMN reviews.size_vote   IS 'small / medium / large / NULL';

-- 오타·잘못된 값이 들어오지 못하게 DB 레벨에서도 막는다.
-- (Pydantic 이 이미 막지만, 스크립트나 SQL 로 직접 넣는 경로가 있어서 이중으로 둔다)
ALTER TABLE reviews DROP CONSTRAINT IF EXISTS reviews_size_vote_check;
ALTER TABLE reviews ADD  CONSTRAINT reviews_size_vote_check
    CHECK (size_vote IS NULL OR size_vote IN ('small', 'medium', 'large'));

ALTER TABLE cafes DROP CONSTRAINT IF EXISTS cafes_size_label_check;
ALTER TABLE cafes ADD  CONSTRAINT cafes_size_label_check
    CHECK (size_label IS NULL OR size_label IN ('small', 'medium', 'large'));

-- ---------------------------------------------------------------------------
-- 4. 기존 카공 컬럼을 NULL 허용으로 (모름 ≠ 아님)
--
--    이게 이번 변경의 핵심이다. 지금까지는 NOT NULL DEFAULT FALSE 라서
--    '모른다'를 표현할 방법이 아예 없었고, 그래서 코드가 추측값을 채웠다.
-- ---------------------------------------------------------------------------
ALTER TABLE cafes ALTER COLUMN laptop_ok  DROP NOT NULL;
ALTER TABLE cafes ALTER COLUMN has_power  DROP NOT NULL;
ALTER TABLE cafes ALTER COLUMN has_wifi   DROP NOT NULL;
ALTER TABLE cafes ALTER COLUMN quiet      DROP NOT NULL;
ALTER TABLE cafes ALTER COLUMN seat_count DROP NOT NULL;

ALTER TABLE cafes ALTER COLUMN laptop_ok  DROP DEFAULT;
ALTER TABLE cafes ALTER COLUMN has_power  DROP DEFAULT;
ALTER TABLE cafes ALTER COLUMN has_wifi   DROP DEFAULT;
ALTER TABLE cafes ALTER COLUMN quiet      DROP DEFAULT;
ALTER TABLE cafes ALTER COLUMN seat_count DROP DEFAULT;

ALTER TABLE cafes ALTER COLUMN cagong_source SET DEFAULT 'unknown';

-- ---------------------------------------------------------------------------
-- 5. ★ 추측으로 채워진 기존 값 제거 (가장 중요)
--
--    이전 guess_cagong() 은 판단 불가일 때조차 has_wifi = TRUE 를 넣었다.
--    그 결과 전 매장이 '와이파이 있음'으로 저장돼 있다. 근거가 0건인 값이라
--    비운다. 점주 인증(owner)과 유저 제보(user)로 채워진 값은 건드리지 않는다.
-- ---------------------------------------------------------------------------
UPDATE cafes
SET laptop_ok     = NULL,
    has_power     = NULL,
    has_wifi      = NULL,
    quiet         = NULL,
    seat_count    = NULL,
    cagong_source = 'unknown'
WHERE cagong_source IS NULL
   OR cagong_source IN ('estimated', 'unknown');

-- ---------------------------------------------------------------------------
-- 6. 기존 리뷰가 있다면 투표 집계를 백필
--    (지금은 리뷰가 0건이라 아무 일도 안 일어나지만, 재실행 안전용으로 둔다)
-- ---------------------------------------------------------------------------
UPDATE cafes c
SET cagong_yes  = COALESCE(v.yes, 0),
    cagong_no   = COALESCE(v.no, 0),
    size_small  = COALESCE(v.s_small, 0),
    size_medium = COALESCE(v.s_medium, 0),
    size_large  = COALESCE(v.s_large, 0)
FROM (
    SELECT cafe_id,
           COUNT(*) FILTER (WHERE cagong_vote IS TRUE)     AS yes,
           COUNT(*) FILTER (WHERE cagong_vote IS FALSE)    AS no,
           COUNT(*) FILTER (WHERE size_vote = 'small')     AS s_small,
           COUNT(*) FILTER (WHERE size_vote = 'medium')    AS s_medium,
           COUNT(*) FILTER (WHERE size_vote = 'large')     AS s_large
    FROM reviews
    GROUP BY cafe_id
) v
WHERE c.id = v.cafe_id;

-- 판정 결과 반영
UPDATE cafes SET cagong_ok = (cagong_yes > cagong_no);

UPDATE cafes
SET size_label = CASE
        WHEN size_small = 0 AND size_medium = 0 AND size_large = 0 THEN NULL
        WHEN size_large  > size_medium AND size_large  > size_small  THEN 'large'
        WHEN size_small  > size_medium AND size_small  > size_large  THEN 'small'
        ELSE 'medium'   -- 동률은 가운데로 수렴. 헛걸음 위험이 가장 작다.
    END;

UPDATE cafes SET cagong_source = 'review'
WHERE (cagong_yes + cagong_no) > 0 AND cagong_source <> 'owner';

-- ---------------------------------------------------------------------------
-- 7. 인덱스 — 지도 이동마다 카공 필터가 걸리므로 필수
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_cafes_cagong_ok  ON cafes (cagong_ok);
CREATE INDEX IF NOT EXISTS ix_cafes_size_label ON cafes (size_label);
CREATE INDEX IF NOT EXISTS ix_reviews_cafe_vote ON reviews (cafe_id, cagong_vote);

COMMIT;

-- ============================================================================
--  검증 — 아래를 따로 실행해서 결과를 확인하세요
-- ============================================================================

-- (1) 추측값이 전부 비워졌는지. 세 컬럼 모두 0 이 나와야 정상.
SELECT
    COUNT(*) FILTER (WHERE has_wifi  IS NOT NULL) AS wifi_남은값,
    COUNT(*) FILTER (WHERE has_power IS NOT NULL) AS power_남은값,
    COUNT(*) FILTER (WHERE laptop_ok IS NOT NULL) AS laptop_남은값,
    COUNT(*)                                      AS 전체매장
FROM cafes;

-- (2) 판정 상태 분포
SELECT cagong_source, COUNT(*) FROM cafes GROUP BY cagong_source ORDER BY 2 DESC;

-- (3) 새 컬럼이 다 붙었는지
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('cafes', 'reviews')
  AND column_name IN ('cagong_yes','cagong_no','cagong_ok','size_small',
                      'size_medium','size_large','size_label',
                      'cagong_vote','size_vote','has_wifi','laptop_ok')
ORDER BY table_name, column_name;
