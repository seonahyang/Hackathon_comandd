-- =====================================================================
--  제주 카공스팟 — Supabase 보안 설정 (RLS)
--  실행: Supabase 대시보드 > SQL Editor > 붙여넣고 Run
--        또는  python -m scripts.init_supabase
--
--  ⚠️ 반드시 서버를 한 번 띄운 뒤(테이블 생성 후) 실행하세요.
--     테이블은 SQLAlchemy 가 만듭니다 (app/main.py 의 create_all).
-- =====================================================================

-- ---------------------------------------------------------------------
--  왜 필요한가
--  Supabase는 anon key만 있으면 누구나 https://<ref>.supabase.co/rest/v1/<테이블>
--  로 DB를 직접 때릴 수 있게 열어둡니다. anon key는 프론트 코드에 그대로
--  박히므로 사실상 공개 값입니다.
--  → RLS를 켜지 않으면 users 테이블의 이메일이 인터넷에 그대로 공개됩니다.
--
--  우리 구조에서는 모든 데이터 접근이 FastAPI를 거칩니다. FastAPI는 postgres
--  롤로 접속하므로 RLS를 우회합니다(BYPASSRLS). 따라서 "정책 없이 RLS만 켜기"
--  = PostgREST 경유 접근 전면 차단 + 백엔드는 정상 동작, 이 조합이 정답입니다.
-- ---------------------------------------------------------------------

ALTER TABLE public.cafes           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reviews         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.point_ledger    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cagong_reports  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.route_cache     ENABLE ROW LEVEL SECURITY;

-- 정책을 하나도 만들지 않으면 anon/authenticated 는 SELECT 0건, INSERT 거부됩니다.

-- ---------------------------------------------------------------------
--  (선택) 프론트가 카페 목록만 supabase-js 로 직접 읽고 싶다면 아래 주석 해제.
--  카페는 공개 정보라 노출돼도 문제 없습니다. users/reviews 는 절대 열지 말 것.
-- ---------------------------------------------------------------------
-- DROP POLICY IF EXISTS "cafes are public" ON public.cafes;
-- CREATE POLICY "cafes are public"
--   ON public.cafes FOR SELECT
--   TO anon, authenticated
--   USING (true);

-- ---------------------------------------------------------------------
--  성능 — 지도 화면이 bbox로 카페를 훑기 때문에 Postgres에서는 인덱스가 중요.
--  SQLAlchemy가 만들어주는 것 외에 조회 패턴에 맞춘 복합 인덱스를 더 얹는다.
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_cafes_remote_reviews
  ON public.cafes (is_remote, review_count);

CREATE INDEX IF NOT EXISTS ix_reviews_cafe_user
  ON public.reviews (cafe_id, user_id);

CREATE INDEX IF NOT EXISTS ix_reports_cafe_field
  ON public.cagong_reports (cafe_id, field, applied);

-- 같은 유저가 같은 카페에 리뷰 2개를 못 쓰게 DB 레벨에서도 막는다.
-- (앱에서 이미 409로 막지만, 동시 요청이 겹치면 앱 체크만으로는 새어나간다)
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_once_per_cafe
  ON public.reviews (cafe_id, user_id);

-- ---------------------------------------------------------------------
--  확인용 — 실행 후 아래를 돌리면 6개 테이블 전부 rls=true 여야 합니다.
-- ---------------------------------------------------------------------
-- SELECT relname, relrowsecurity FROM pg_class
--  WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
--  ORDER BY relname;
