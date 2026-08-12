-- =====================================================================
--  테스트 계정 정리 — Supabase SQL Editor 에 붙여넣고 실행
--
--  스모크 테스트(scripts/smoke_test.py)와 더미 시드(scripts/seed.py)가
--  만든 가짜 유저를 지웁니다. 실제 소셜 로그인 계정은 건드리지 않습니다.
--
--  판별 기준: supabase_uid IS NULL
--    구글·카카오로 로그인한 유저는 JWT의 sub 값이 supabase_uid 에 반드시 들어갑니다.
--    테스트 계정은 POST /api/users 나 개발용 가짜 로그인으로 만들어져 이 값이 없습니다.
--    닉네임·이메일로 거르면 '스모크테스터2' 같은 걸 놓치므로 이 기준이 안전합니다.
-- =====================================================================


-- ── STEP 1. 뭐가 지워질지 먼저 봅니다 (실행해도 아무것도 안 지워짐) ──────
SELECT
  id, nickname, email, provider, point_balance,
  CASE WHEN supabase_uid IS NULL THEN '❌ 삭제 대상 (테스트 계정)'
       ELSE '✅ 보존 (실제 로그인 계정)' END AS 판정
FROM public.users
ORDER BY id;


-- ── STEP 2. 위 목록을 확인했으면 여기부터 블록 전체를 실행합니다 ─────────
-- 삭제 순서가 중요합니다. 참조하는 쪽(point_ledger)부터 지워야
-- ForeignKeyViolation 이 안 납니다.

BEGIN;

-- 2-1. 적립 내역
DELETE FROM public.point_ledger
 WHERE user_id IN (SELECT id FROM public.users WHERE supabase_uid IS NULL);

-- 2-2. 크라우드소싱 제보
DELETE FROM public.cagong_reports
 WHERE user_id IN (SELECT id FROM public.users WHERE supabase_uid IS NULL);

-- 2-3. 리뷰
DELETE FROM public.reviews
 WHERE user_id IN (SELECT id FROM public.users WHERE supabase_uid IS NULL);

-- 2-4. 유저
DELETE FROM public.users WHERE supabase_uid IS NULL;

-- 2-5. 카페의 리뷰 집계를 실제 리뷰 기준으로 다시 계산합니다.
--      이걸 빼먹으면 리뷰는 0건인데 카드에 '리뷰 12개'가 그대로 남고,
--      적립금 계산(희소성 보너스)도 틀어집니다.
UPDATE public.cafes c
   SET review_count = COALESCE(r.cnt, 0),
       rating_avg   = COALESCE(ROUND(r.avg_rating::numeric, 2), 0)
  FROM (
        SELECT cafe_id, COUNT(*) AS cnt, AVG(rating) AS avg_rating
          FROM public.reviews GROUP BY cafe_id
       ) r
 WHERE c.id = r.cafe_id;

-- NOT IN 대신 NOT EXISTS 를 씁니다. NOT IN 은 하위 쿼리에 NULL 이 하나라도
-- 섞이면 전체가 참이 아니게 되어 한 행도 갱신되지 않습니다(조용히 실패).
UPDATE public.cafes c
   SET review_count = 0, rating_avg = 0
 WHERE NOT EXISTS (SELECT 1 FROM public.reviews r WHERE r.cafe_id = c.id);

COMMIT;


-- ── STEP 3. 결과 확인 ───────────────────────────────────────────────────
SELECT 'users' AS 테이블, COUNT(*) AS 건수 FROM public.users
UNION ALL SELECT 'reviews',      COUNT(*) FROM public.reviews
UNION ALL SELECT 'point_ledger', COUNT(*) FROM public.point_ledger
UNION ALL SELECT 'cagong_reports', COUNT(*) FROM public.cagong_reports
UNION ALL SELECT 'cafes',        COUNT(*) FROM public.cafes;

-- 남은 유저 (있다면 실제 로그인 계정이어야 합니다)
SELECT id, nickname, email, provider, point_balance FROM public.users ORDER BY id;
