# 제주 카공스팟 — Backend (FastAPI + PostgreSQL/Supabase)

런케이션 관광객 대상 지도 기반 카페 추천 서비스의 API 서버.
핵심 3기능 + 데이터 파이프라인 3종이 구현돼 있고, 스모크 테스트 54개 항목 전부 통과 상태.

**데이터**: 제주 관광공사 음식점 719건 (카페 226 / 음식점 493) — 실데이터.
좌표 100%, 영업시간 파싱 성공률 99.3%, 브레이크타임 106건, 외곽지 296건.

---

## 1. 5분 안에 돌리기

```bash
cd C:\순아공부\Hackathon_comandd
python -m venv .venv
.venv\Scripts\activate          # (mac/linux: source .venv/bin/activate)
pip install -r requirements.txt

python -m scripts.check_supabase      # Supabase 연결 점검
python -m scripts.init_supabase      # 테이블 생성 + 컬럼 동기화 + RLS 잠금
python -m scripts.ingest_iddy --purge   # 제주 음식점 719건 적재 (실데이터)
uvicorn app.main:app --reload
```

> **`column ... does not exist` 에러가 나면** `python -m scripts.migrate` 를 돌리세요.
> SQLAlchemy의 `create_all()`은 없는 *테이블*만 만들고, 이미 있는 테이블에 새로
> 생긴 *컬럼*은 추가해주지 않습니다. `migrate`가 모델과 DB를 비교해 빠진 컬럼을
> ALTER TABLE로 채웁니다. 기존 데이터는 보존되고 여러 번 돌려도 안전합니다.
> (`init_supabase`가 이걸 자동으로 부르므로 보통은 따로 실행할 일이 없습니다.)

→ http://127.0.0.1:8000/docs (Swagger. 프론트팀한테 이 주소 던져주면 됨)

검증:
```bash
python -m scripts.smoke_test    # 54개 항목 전부 PASS 나오면 백엔드 완료
```

> `scripts/seed.py`는 손으로 만든 더미 32건입니다. 실데이터가 들어있으면 실행을
> 거부하도록 막아뒀습니다. 발표에는 쓰지 마세요.

---

## 2. 데이터 파이프라인 (설계 근거)

| 데이터 | 출처 | 상태 | 폴백 |
|---|---|---|---|
| 매장 목록·좌표 | 제주 관광공사 음식점 데이터 719건 | **적재 완료 (100%)** | 카카오 로컬 API |
| 영업시간 | 같은 데이터 (99.6% 채움) | **파싱 완료 (99.3%)** | `data/hours_override.csv` |
| 브레이크타임·라스트오더 | 같은 데이터 | 106건 / 389건 | — |
| 이동시간(차량) | 카카오모빌리티 자동차 길찾기 | 키 있으면 즉시 | 직선거리 ÷ 30km/h + 5분 |
| 이동시간(도보) | — | **제휴 계약 전용이라 사용 불가** | 직선거리 × 1.3 ÷ 4km/h |
| 카공 인프라 | 유저·점주 크라우드소싱 | **공공데이터에 없음 → 0건에서 시작** | 없음 |

**응답에 `hours_confidence`, `cagong_source`, `travel_source`를 같이 내려서
추정치인지 실측치인지 프론트가 구분해 표시할 수 있게 했다.** 심사에서 "데이터
어디서 났냐"는 반드시 나오는 질문인데, 이 필드들이 그대로 답이 된다.

### ⚠️ 카공 정보는 왜 0건인가 (심사 예상 질문 1순위)

관광공사 데이터 719건을 전수 조사한 결과:

| 항목 | 채움률 |
|---|---|
| 좌표 | 719/719 (100%) |
| 영업시간 | 716/719 (99.6%) |
| 주차 | 701/719 (97.5%) |
| **콘센트 언급** | **0/719** |
| **와이파이 언급** | **0/719** |
| **노트북 사용 언급** | **1/719** |
| 좌석수 | 1/719 |

**공공데이터에는 카공 정보가 존재하지 않는다.** 이건 우리 구현의 한계가 아니라
이 서비스가 필요한 이유다. 그래서 없는 값을 지어내지 않고 0건에서 시작한다.

시연은 라이브로 한다 — `/api/reports/wanted`로 제보 대상 카페를 띄우고, 서로 다른
두 계정이 제보하면 그 자리에서 `cagong=true` 필터에 매장이 나타난다. 한 사람 말만
믿지 않고 2인 합의를 요구하는 로직(`services/crowdsource.py`)이 여기서 보인다.
점주 인증 제보는 1건으로 즉시 확정된다.

### 확인된 사실 (조사 결과)
- **도보 길찾기 API는 카카오 제휴 파트너 전용**이다. 사전 계약이 필요해서 해커톤 기간에는 발급이 불가능하다. 그래서 도보는 추정식으로 대체했고, 계수는 `.env`에서 조정 가능하다.
- **비짓제주 상세 페이지는 JavaScript 렌더링**이라 일반 HTML 크롤링으로는 영업시간을 못 긁는다. 대신 공식 Open API(`api.visitjeju.net/vsjApi/contents/searchList`)가 살아있는 걸 확인했다 — 이쪽이 정답 경로다. 단, 키가 **담당자 수동 승인** 방식이라 당일 발급이 안 될 수 있어 CSV 경로를 같이 만들어뒀다.
- visitjeju.net의 `robots.txt`는 크롤링을 허용하되 `Crawl-delay: 5`를 요구한다. 공식 API가 있으므로 크롤러 대신 API 클라이언트로 구현했다.

---

## 3. 핵심 3기능이 어디에 구현돼 있는지

| 기능 | 로직 파일 | API |
|---|---|---|
| ① 외곽지 리뷰 적립금 | `app/core/rewards.py` | `POST /api/reviews` |
| ② '여유롭게 2시간' 필터 | `app/core/hours.py` + `app/services/travel.py` | `GET /api/cafes?stay_hours=2` |
| ③ '카공 가능' 퀵필터 | `app/core/cagong.py` + `app/services/crowdsource.py` | `GET /api/cafes?cagong=true`, `POST /api/reports` |

### ① 적립금 정책 (심사에서 반드시 물어봄 — 이 표 외우기)

```
기본 적립            500P
+ 외곽지 보너스      핫스팟 15km↑ 2,000P / 10km↑ 1,500P / 5km↑ 800P
+ 소외매장 보너스    리뷰 0개 1,200P / 5개 미만 700P / 15개 미만 300P
+ 상세 리뷰(40자↑)   300P
1회 상한             4,000P

[별도] 카공 정보 제보  100P (+ 빈 항목 최초 입력 200P)
```

- '핫스팟'은 실제 과밀 지점 8곳(제주시청·애월한담·협재·성산·올레시장·중문·함덕·공항) 좌표로 정의 — `app/core/geo.py`
- **실데이터 실측**: 제주시청 앞 남매네왕갈치(0.05km) 1,700P vs 표선 당케올레국수(17.4km) 3,700P → **2.2배**. 동선 분산의 정량 근거.
- 리뷰가 쌓이면 보너스가 자동 감소 (3,700P → 3,200P 확인). **소외가 해소되면 인센티브도 자동 회수**되는 자정 구조.
- 719건 중 **외곽지(핫스팟 5km 밖)가 296건, 41.2%**. 분산시킬 여지가 실제로 존재한다는 근거.

### ② 2시간 필터 판정식

```
도착시각 = 현재시각 + 이동시간
실질마감 = 영업종료 - 라스트오더 버퍼
체류구간 = [영업시작, 브레이크시작] + [브레이크종료, 실질마감]
통과     = 체류구간 중 한 곳에 (도착시각 + 2h)가 통째로 들어감
           AND 오늘 휴무 아님
```

**브레이크 타임을 따로 본다.** 영업시간만 보면 "10:00~20:30이니까 15시에 가서
2시간 OK"인데, 15:00~17:00 브레이크에 걸리면 실제로는 못 앉는다. 제주 음식점의
15%(106건)가 브레이크를 운영한다. 추천을 믿고 갔다가 문이 닫혀 있는 게 이
서비스에서 가장 치명적인 실패라 여기서 걸러낸다.

실측 예시:

| 매장 | 영업 | 브레이크 | 14시 도착 판정 |
|---|---|---|---|
| 해녀의 부엌 | 10:00~18:00 | 14:30~17:00 | ❌ 2시간 확보 불가 |
| 대기정 | 10:00~20:30 | 15:00~17:00 | ⭕ 브레이크 후 17:00부터 |
| 바다는안보여요 | 10:00~22:00 | 19:00~20:00 | ⭕ 7시간 15분 여유 |

이동시간은 2단계로 계산한다.

- **목록 조회**: 직선거리 추정 (지도 한 번 움직일 때마다 200개 카페 × 길찾기 호출은 쿼터·지연 모두 감당 불가)
- **`precise=true` 또는 상세 조회**: 가까운 순 상위 N개만 카카오 길찾기 실제 호출, 좌표 100m 단위로 반올림해 DB 캐싱

영업시간은 `app/services/hours_parser.py`가 자유 텍스트를 구조화한다.
공공데이터 원문이 상당히 지저분해서 — `<br>` 태그, 글머리표, 요일별 분기가 섞여 있다 —
아래를 전부 처리한다.

```
"- 월요일~금요일 10:00~21:00- 토요일 10:00~20:00"     → 10:00~21:00 (medium)
"[목요일~일요일]<br>10:00~18:00 <br>[월요일]..."      → 10:00~18:00 (medium)
"11:00~02:00"                                      → 11:00~26:00 (새벽 정규화)
"오전 9시 ~ 오후 6시 / 매주 월요일 휴무"                 → 09:00~18:00, 휴무=월
```

**719건 중 714건 파싱 성공(99.3%)**, 실패 5건은 원문이 `"오더 14:30"`처럼 애초에
영업시간이 아닌 경우다. 요일별로 시간이 다르면 첫 구간(대개 평일)을 쓰되
`hours_confidence`를 낮춰서 프론트가 "요일별 상이" 뱃지를 띄울 수 있게 한다.
**파싱 실패 시 조용히 기본값을 쓰지 않는다** — 틀린 값을 자신있게 보여주는 것보다
모른다고 표시하는 게 낫다.

> **알려진 공백**: 이 데이터에는 휴무일 정보가 없다(719건 전부 공란). 휴무일 판정
> 로직은 구현돼 있고 동작하지만, 채울 데이터가 없어 현재는 전 매장 무휴로 처리된다.
> 카공 정보와 마찬가지로 크라우드소싱 대상이다.

데모용으로 `?now=18:30` 파라미터로 현재 시각을 조작할 수 있다 —
**발표가 오후 2시라도 "저녁 7시엔 이렇게 걸러집니다" 시연 가능.**

### ③ 카공 판정 + 크라우드소싱

`laptop_ok AND has_power AND has_wifi` 3개 모두 true일 때만 필터 통과.
**공공데이터에 이 3개가 전부 없으므로 현재 통과 매장은 0건이다** (위 표 참고).

정보 수집은 제보 기반이고, 한 명 말만 믿지 않는다.

```
점주 제보(is_owner)   → 1건으로 즉시 확정, cagong_source='owner'
일반 유저 제보        → 같은 항목 2건 이상 + 과반 동의 시 반영, 'user'
합의 미달             → 기존 값 유지, "1명이 더 확인하면 반영됩니다"
좌석수                → 중앙값 사용 (극단값 방어)
```

제보에도 적립금을 준다(100P + 최초 입력 200P). 리뷰보다 작지만
지도를 열 때마다 한 항목씩 채우게 만드는 게 리텐션의 핵심이라 별도 보상 라인을 뒀다.

#### 발표용 라이브 시연 대본 (2분)

```bash
# 1. "카공 가능한 곳을 찾아봅니다"  → 0건
GET /api/cafes?cagong=true

# 2. "정보가 없어서입니다. 공공데이터엔 콘센트 정보가 아예 없거든요"
GET /api/reports/wanted?limit=5          # 제보가 필요한 카페 목록

# 3. "그래서 이용자가 채웁니다. 한 명 말만 믿진 않습니다"
POST /api/reports  {cafe_id, field:"has_power", value_bool:true}   # 유저A
  → applied=false, "1명이 더 확인하면 반영됩니다"
POST /api/reports  {같은 내용}                                      # 유저B
  → applied=true,  "제보 반영 완료"

# 4. "이제 나타납니다"
GET /api/cafes?cagong=true               # → 1건
```

`?place_type=cafe`(기본)로 카페만, `all`로 음식점까지 볼 수 있다.

---

## 4. 선아님이 지금부터 해야 할 일

### [완료됨] 카카오 REST 키
`.env`에 이미 들어가 있습니다. 다만 **제 실행 환경에서는 카카오 도메인이 차단돼 검증을 못 했습니다.**
선아님 PC에서 이걸 먼저 돌려서 키가 살아있는지 확인하세요:

```bash
python -m scripts.check_keys
```

카카오 로컬 검색 + 길찾기 둘 다 OK 나오면 바로 다음 단계로.
401이 나오면 REST API 키가 아니라 JavaScript 키를 넣었을 확률이 높습니다.

### [완료됨] 매장 데이터 719건
제주 관광공사 음식점 데이터(`data/iddy_fnb.xlsx`)를 적재해뒀습니다.

```bash
python -m scripts.ingest_iddy --dry-run   # DB 안 건드리고 통계만 확인
python -m scripts.ingest_iddy --purge     # 기존 삭제 후 재적재
python -m scripts.ingest_iddy             # 추가/갱신 (여러 번 돌려도 안전)
```

카카오 로컬 API로 더 긁고 싶으면 `python -m scripts.ingest_kakao --fast`도 됩니다.
자연키가 달라서 섞여도 중복되지 않습니다.

### [대부분 완료] 영업시간
관광공사 데이터로 **719건 중 714건(99.3%)이 이미 채워졌습니다.** 아래는 남은 5건과
정확도를 더 높이고 싶을 때만 하면 됩니다.

```bash
python -m scripts.enrich_hours --report   # 현재 신뢰도 분포 확인
```

1. **비짓제주 API 신청** (선택 — 이미 영업시간이 있으므로 우선순위 낮음)
   https://www.visitjeju.net/kr/visitjejuapi → 이메일 인증 → 신청
   키 오면 `.env`의 `VISITJEJU_API_KEY`에 넣고:
   ```bash
   python -m scripts.enrich_hours --dump   # ⚠️ 먼저 이걸로 응답 필드명 확인
   python -m scripts.enrich_hours --api
   ```
   > `--dump`를 먼저 돌려야 하는 이유: 키 없이는 응답 필드명을 검증할 수 없어서
   > `app/services/visitjeju.py`의 `*_KEYS` 목록을 추측으로 넣어놨습니다.
   > 실제 응답 보고 맞춰주세요. 5분이면 됩니다.

2. **CSV 수동 입력** — 발표에 쓸 카페 5~10곳만 카카오맵 보면서 검증하는 게
   가성비가 제일 좋습니다. **휴무일은 이 데이터에 아예 없으니** 시연할 매장만이라도
   여기서 채워두면 "오늘 휴무" 판정까지 보여줄 수 있습니다.
   ```bash
   python -m scripts.enrich_hours --csv data/hours_override.csv
   ```

### [필수 · 20분] Supabase 연결 + 소셜 로그인

`.env`의 `DATABASE_URL`은 이미 Supabase Session pooler 주소로 바뀌어 있습니다.
남은 건 **anon key 하나**입니다.

```bash
# 1) 대시보드 > Project Settings > API Keys > anon / public 복사
#    → .env 의 SUPABASE_ANON_KEY= 뒤에 붙여넣기

# 2) 연결 점검 (막히면 어디가 문제인지 짚어줍니다)
python -m scripts.check_supabase

# 3) 테이블 생성 + RLS 잠금 + 인덱스
python -m scripts.init_supabase

# 4) 데이터
python -m scripts.seed
python -m scripts.enrich_hours --csv data/hours_override.csv
python -m scripts.smoke_test
```

**⚠️ 반드시 Session pooler 주소를 쓸 것.** `db.<ref>.supabase.co` 직접 연결은
IPv6 전용이라 국내 대부분의 망에서 DNS 조회부터 실패합니다.
정확한 주소: 대시보드 상단 **[Connect] → Session pooler → URI**

**⚠️ RLS를 반드시 켤 것.** anon key는 프론트 코드에 그대로 박히는 공개값이고,
RLS가 꺼져 있으면 그 키만으로 `users` 테이블의 이메일을 외부에서 그대로 읽을 수
있습니다. `scripts/init_supabase.py`가 이걸 잠급니다. 심사에서 물어보기 좋은 지점.

구글/카카오 Provider 설정과 프론트 연동 코드는 **[`FRONTEND_AUTH.md`](FRONTEND_AUTH.md)**
에 정리돼 있습니다. 소은·유경님께 이 파일을 그대로 넘기면 됩니다.

> 인터넷이 아예 막힌 최악의 상황에서는 `.env`의 SQLite 폴백 줄을 되살리면
> 오프라인으로도 데모가 돌아갑니다.

### [필수 · 5분] 프론트팀에 넘기기
소은·유경님에게 전달할 것:
1. `http://127.0.0.1:8000/docs` (Swagger 주소)
2. `API_GUIDE.md` (프론트 전용 요약)
3. `FRONTEND_AUTH.md` (구글·카카오 로그인 연동 — 코드 그대로 복붙 가능)
4. "필터는 전부 `GET /api/cafes` 쿼리 파라미터 하나로 끝난다"
5. "로그인 필요한 건 리뷰·제보뿐. 나머지는 토큰 없이 된다"

---

## 5. 보안 주의

`.env.example`에 실제 키가 들어가 있었습니다. 이 파일은 **git에 커밋되는 템플릿**이라
키가 그대로 공개됩니다. 지금은 키를 `.env`(gitignore 등록됨)로 옮겨뒀습니다.

**이미 GitHub에 push했다면** developers.kakao.com에서 REST API 키를 **재발급**하세요.
(내 애플리케이션 → 앱 키 → 코드 재발급)

---

## 6. 파일 구조

```
Hackathon_comandd/
├── app/
│   ├── main.py           FastAPI 진입점, CORS, 라우터 등록
│   ├── config.py         .env 설정
│   ├── database.py       SQLAlchemy 엔진 (SQLite/Postgres 자동 분기)
│   ├── models.py         Cafe / User / Review / PointLedger / CagongReport / RouteCache
│   ├── schemas.py        Pydantic 응답 스키마
│   ├── core/
│   │   ├── geo.py        핫스팟 좌표, 거리 계산, 외곽지 판정
│   │   ├── rewards.py    ★기능1 적립금 엔진
│   │   ├── hours.py      ★기능2 2시간 체류 판정
│   │   └── cagong.py     ★기능3 카공 환경 판정
│   ├── services/
│   │   ├── travel.py         카카오모빌리티 길찾기 + 캐싱 + 도보 추정
│   │   ├── hours_parser.py   자유텍스트 영업시간 → 구조화
│   │   ├── visitjeju.py      비짓제주 Open API 클라이언트
│   │   └── crowdsource.py    ★기능3 제보 투표 집계
│   └── routers/
│       ├── cafes.py      검색/상세
│       ├── reviews.py    리뷰 작성 + 적립
│       ├── reports.py    카공 정보 제보
│       ├── users.py      유저/포인트
│       └── stats.py      발표용 집계
├── scripts/
│   ├── check_keys.py     API 키 살아있는지 확인 ← 제일 먼저 실행
│   ├── ingest_kakao.py   카카오 로컬 API 카페 수집
│   ├── enrich_hours.py   영업시간 채우기 (API/CSV)
│   ├── seed.py           더미 시드
│   └── smoke_test.py     전체 API 검증 (43항목)
├── data/
│   └── hours_override.csv  수동 영업시간 입력표
├── requirements.txt
├── .env / .env.example
├── README.md
└── API_GUIDE.md          프론트팀 전달용
```

## 7. 트러블슈팅

| 증상 | 해결 |
|---|---|
| `sqlite3.OperationalError: disk I/O error` | 네트워크 드라이브/OneDrive 동기화 폴더에서 실행 중. `DATABASE_URL=sqlite:///C:/temp/jeju.db` |
| `no such column: cafes.hours_text` | 모델이 바뀌었는데 기존 DB에 컬럼이 없음. `jeju_cagong.db` 삭제 후 재시드 (해커톤 스코프라 마이그레이션 없음) |
| 카카오 401 | REST API 키가 아니라 JavaScript 키일 확률 99% |
| 카카오 429 | 쿼터 초과. `--fast`로 실행하거나 `USE_KAKAO_NAVI=false` |
| 길찾기만 401 | 카카오모빌리티 길찾기는 앱에서 별도 활성화가 필요할 수 있음. 안 되면 `USE_KAKAO_NAVI=false`로 두고 추정치 사용 (데모 지장 없음) |
| 비짓제주 403 `apiKey is invalid` | 아직 승인 안 됨. CSV 경로 사용 |
| 프론트 CORS 에러 | `.env`의 `CORS_ORIGINS=*` 확인 후 서버 재시작 |
| Supabase 연결 timeout | Session pooler 주소(포트 5432/6543) 사용 |

## 8. 의도적으로 안 만든 것 (물어보면 이렇게 답하기)

- **로그인/JWT** — `POST /api/users`로 유저 생성만. 하루짜리 해커톤에서 인증은 핵심 검증 대상이 아님.
- **실제 방문 인증(GPS/영수증)** — 적립금 어뷰징 방지는 실서비스 이슈. 현재는 매장당 1인 1리뷰(409) + 항목당 1인 1제보로 최소 방어.
- **요일별 상세 영업시간** — 단일 open/close + 휴무요일로 단순화. 판정 로직은 요일별 확장 가능한 구조.
- **이미지 업로드** — `thumbnail_url` 컬럼만 열어둠.
