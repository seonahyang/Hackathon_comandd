# 프론트팀 전달용 API 가이드 (소은·유경님)

서버: `http://127.0.0.1:8000`
Swagger(직접 눌러보며 테스트 가능): `http://127.0.0.1:8000/docs`

> **핵심: 필터 3개는 전부 `GET /api/cafes` 하나로 끝납니다.** 화면 상단 칩 버튼 = 쿼리 파라미터 토글.

---

## 1. 지도 카페 목록 — `GET /api/cafes`

| 파라미터 | 예시 | 설명 |
|---|---|---|
| `lat`, `lng` | `33.4996`, `126.5312` | 유저 현재 위치. 넣으면 거리·이동시간 계산됨 |
| `radius_km` | `10` | 반경 필터 |
| `sw_lat` `sw_lng` `ne_lat` `ne_lng` | | 지도 화면 영역(bbox)으로 조회 |
| **`place_type`** | `cafe`(기본) \| `restaurant` \| `all` | 카페만/음식점만/전체. 데이터는 총 719건(카페 226) |
| **`cagong`** | `true` | ★칩버튼 "카공 가능". **현재 0건** — 아래 주의 참고 |
| **`stay_hours`** | `2` | ★칩버튼 "여유롭게 2시간" |
| `travel_mode` | `car` \| `walk` | 이동수단. 기본 `car` |
| `precise` | `true` | 가까운 순 상위 N개는 **카카오 길찾기 실제 호출**로 소요시간 재계산 |
| `precise_top` | `10` | 정밀 계산 개수 (최대 30, 쿼터 보호) |
| `travel_min` | `15` | 이동시간 고정(분). 넣으면 계산 안 함 |
| `open_now` | `true` | 지금 영업중만 |
| `remote_only` | `true` | ★칩버튼 "숨은 로컬 매장" (적립금 높은 곳) |
| `hours_verified` | `true` | 영업시간이 검증된(high) 매장만 |
| `q` | `스타벅스` | 가게명 검색 |
| `sort` | `distance` \| `reward` \| `review` \| `cagong` | 정렬 |
| `now` | `18:30` | **데모용 시간 조작.** 발표 때 저녁 상황 시연할 때 사용 |

### 호출 예시
```js
// 칩 3개 다 켠 상태
const params = new URLSearchParams({
  lat: 33.4996, lng: 126.5312, radius_km: 40,
  cagong: true, stay_hours: 2, sort: "reward",
});
const res = await fetch(`http://127.0.0.1:8000/api/cafes?${params}`);
const { total, items } = await res.json();
```

> ### ⚠️ `cagong=true`는 지금 0건입니다
> 공공데이터에 콘센트·와이파이·노트북 정보가 **아예 없어서**(719건 전수 조사 결과
> 0건) 초기값이 전부 false입니다. 버그가 아닙니다.
>
> 제보가 2건 쌓이면 그때부터 결과가 나옵니다(`POST /api/reports`).
> **빈 결과 화면을 반드시 만들어주세요** — "아직 확인된 카공 스팟이 없어요.
> 첫 제보자가 되어보세요 (+300P)" 같은 CTA로 `/api/reports/wanted`를 띄우면
> 그대로 핵심 기능 시연이 됩니다.

### 실제 응답 (한 건)
```json
{
  "total": 19,
  "now": "2026-08-12 15:00",
  "items": [{
    "id": 16,
    "name": "당케올레국수",
    "place_type": "restaurant",
    "lat": 33.326, "lng": 126.838,
    "region": "서귀포시", "district": "표선면",
    "open_time": "10:00", "close_time": "20:00", "closed_days": "",
    "break_start": "15:00", "break_end": "17:00",
    "hours_source": "visitjeju", "hours_confidence": "high",
    "hours_text": "10:00~20:00",
    "parking": true, "has_toilet": true,
    "summary": "표선해수욕장 근처에 자리한 …",
    "laptop_ok": false, "has_power": false, "has_wifi": false, "quiet": false,
    "cagong_ok": false, "cagong_score": 15, "cagong_source": "estimated",
    "review_count": 0, "rating_avg": 0.0,
    "is_remote": true, "dist_to_hotspot_km": 17.4,
    "distance_km": 34.402, "travel_min": 74, "travel_source": "estimated",
    "reward": { "point": 3700, "multiplier": 7.4, "is_boosted": true, "badge": "7.4x 적립" },
    "stay": {
      "stay_ok": true, "open_now": true, "reason": "ok",
      "label": "브레이크 후 17:00부터 2시간 가능", "minutes_left": 196,
      "arrival_at": "16:14", "last_call_at": "19:30",
      "break_time": "15:00~17:00", "sit_from": "17:00"
    }
  }]
}
```

### UI에 바로 꽂아 쓰라고 만든 필드
- `reward.badge` → 마커 위 뱃지 텍스트 (`"6.4x 적립"`). `is_boosted=false`면 badge는 `null`이니 숨기면 됩니다.
- `reward.point` → 카드에 "리뷰 쓰면 3,200P"
- `stay.label` → `"3시간 16분 여유"` 또는 `"25분 뒤 마감 (2시간 부족)"` 그대로 출력
- `stay.arrival_at` / `last_call_at` → "16:14 도착 → 19:30 라스트오더"
- `stay.break_time` → `null`이 아니면 "브레이크 15:00~17:00" 주황 뱃지
- `stay.sit_from` → 값이 있으면 "17:00부터 입장 가능" (브레이크 끝나고 앉는 케이스)
- `stay.reason` → `"break_time"`이면 "브레이크 타임이라 2시간이 안 나와요" 문구
- `place_type` → `"restaurant"`면 마커 아이콘 구분 (카페/식당)
- `parking` / `has_toilet` → 상세 화면 아이콘. `null`은 "정보 없음"이므로 아이콘 숨김
- `summary` → 관광공사 소개문. 상세 화면 설명란에 그대로 넣으면 됩니다
- `cagong_score` (0~100) → 카공 적합도 별점/게이지
- `cagong_source` → `"estimated"`면 "추정 정보" 회색 뱃지, `"owner"`면 "점주 인증" 파란 뱃지
- `hours_confidence` → `"low"`면 "영업시간 부정확" 경고 표시. `"high"`면 그냥 시간만 보여주면 됨
- `travel_source` → `"kakao"`면 "실제 도로 기준", `"estimated"`면 "예상" (작게)
- `is_remote` → 마커 색 구분 (외곽=초록, 도심=회색 추천)

> **데이터 출처를 화면에 노출하는 게 이 서비스의 차별점입니다.** 심사위원이
> "이 정보 어디서 났냐"고 물으면 UI가 이미 답을 하고 있는 상태가 됩니다.

> `stay_hours`를 넣으면 **조건 미달 매장은 응답에서 아예 빠집니다.** 지도 마커를 그냥 다시 그리면 필터가 걸린 것처럼 보입니다.

---

## 2. 카페 상세 — `GET /api/cafes/{id}?lat=&lng=&stay_hours=2`
목록과 동일한 객체 1개.

## 3. 리뷰 목록 — `GET /api/reviews/cafe/{cafe_id}`

## 4. 리뷰 작성 + 적립 — `POST /api/reviews`
```js
await fetch("http://127.0.0.1:8000/api/reviews", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    cafe_id: 16, user_id: 1, rating: 5,
    content: "콘센트 자리마다 있고 조용해서 4시간 작업했어요",
    tags: ["콘센트많음", "조용함"],
  }),
});
```
응답:
```json
{
  "earned_point": 3500,
  "point_balance": 3500,
  "headline": "소외 상권 보너스 +3,000P! 총 3,500P 적립",
  "breakdown": [
    { "label": "기본 리뷰 적립", "point": 500 },
    { "label": "핫스팟에서 10km 이상 떨어진 외곽 매장", "point": 1500 },
    { "label": "리뷰가 아직 하나도 없는 첫 리뷰", "point": 1200 },
    { "label": "상세 리뷰 작성", "point": 300 }
  ],
  "cafe_review_count": 1,
  "cafe_rating_avg": 5.0
}
```
→ **`headline`은 적립 완료 토스트/모달에, `breakdown`은 항목별 리스트에 그대로 뿌리면 됩니다.** 이 화면이 발표의 하이라이트입니다.

에러: 같은 유저가 같은 매장에 두 번 쓰면 `409`.

## 5. 유저 — `POST /api/users` `{ "nickname": "선아", "email": "..." }`
로그인 대신 씁니다. 앱 첫 진입 때 한 번 호출하고 `id`를 저장해두세요.

## 6. 내 포인트 — `GET /api/users/{id}/points`
`point_balance`, `review_count`, `remote_review_count`, `history[]` (적립 내역).

## 7. 카공 정보 제보 (크라우드소싱) — `POST /api/reports`

상세 화면의 "콘센트 있나요?" 같은 제보 버튼용. **한 명 말만 믿지 않습니다.**

```js
await fetch("http://127.0.0.1:8000/api/reports", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    cafe_id: 16, user_id: 1,
    field: "has_power",     // laptop_ok | has_power | has_wifi | quiet | seat_count
    value_bool: true,       // seat_count 일 때는 value_int 사용
    is_owner: false,        // 점주 인증이면 true → 1건으로 즉시 확정
  }),
});
```
응답:
```json
{
  "applied": false,
  "status_message": "제보 접수 — 1명이 더 확인하면 반영됩니다",
  "earned_point": 300,
  "point_balance": 3800,
  "breakdown": [
    { "label": "콘센트 정보 제보", "point": 100 },
    { "label": "빈 정보를 처음 채운 보상", "point": 200 }
  ]
}
```
→ `status_message`를 토스트에 그대로 띄우면 됩니다. 2명째 제보가 들어오면
`applied: true`, `"제보 반영 완료 — 콘센트 정보가 업데이트됐습니다"`로 바뀝니다.

**관련 엔드포인트**

- `GET /api/reports/cafe/{cafe_id}` — 항목별 제보 현황 + `coverage_percent` (진행률 바용)
- `GET /api/reports/wanted?limit=10` — **제보가 필요한 매장 미션 카드용.** 정보가 비어있는 외곽 매장을 우선 노출합니다. 홈 화면에 "이 카페 정보 채우고 300P 받기" 카드로 쓰면 리텐션이 붙습니다.
- `GET /api/reports/stats` — 제보 집계 (발표용)

> 관리자용 강제 수정은 `PATCH /api/cafes/{id}/flags` 가 따로 있습니다. 유저 화면에는 `POST /api/reports`를 쓰세요.

## 8. 발표용 통계 (PPT 그래프에 그대로 쓰세요)
- `GET /api/stats/summary` → 전체 카페 수, 외곽 비율, 카공 가능 수, 발행 포인트 총액
- `GET /api/stats/dispersion` → 읍면동별 카페수·리뷰수·평균 적립금 (막대그래프)
- `GET /api/stats/underrated?limit=10` → 소외 매장 TOP 10 (적립금 순)
- `GET /api/meta/hotspots` → 과밀 핫스팟 8곳 좌표 (지도에 빨간 원 오버레이 하면 "우리가 분산시키려는 지점" 시각화)

---

## 데모 시나리오 (발표 리허설 그대로)

1. **지도 진입** — `GET /api/cafes?lat=33.4996&lng=126.5312&radius_km=40`
   "제주시청 기준 40km 내 카페 전부입니다."
2. **"카공 가능" 칩 탭** — `&cagong=true`
   "콘센트·와이파이·노트북 허용 검증된 곳만 남습니다. 32 → 23곳."
3. **"여유롭게 2시간" 칩 탭** — `&stay_hours=2&now=18:30`
   "저녁 6시 반. 이동시간까지 계산해서 2시간 못 앉는 곳은 사라집니다. 23 → 9곳.
    16시 마감 브런치집은 빠지고, 심야 코워킹카페는 남습니다."
4. **적립순 정렬** — `&sort=reward`
   "1위가 표선 로컬커피, 6.4배 적립. 노형동 스타벅스는 500P인데 여긴 3,200P입니다."
5. **리뷰 작성** — `POST /api/reviews`
   "3,500P 적립. 왜 이만큼인지 항목별로 다 보여줍니다. 이게 관광객을 외곽으로 밀어내는 장치입니다."
6. **한 번 더 조회** — 같은 카페 적립금이 3,200P → 2,700P
   "리뷰가 쌓이면 보너스가 줄어듭니다. 소외가 해소되면 인센티브도 자동 회수되는 구조입니다."
7. **제보** — `POST /api/reports`
   "콘센트 정보를 제보하면 300P. 단, 한 명 말은 안 믿습니다. 2명이 확인해야 반영되고,
    점주가 인증하면 즉시 확정됩니다. 데이터를 유저가 직접 키우는 구조입니다."
8. **통계** — `GET /api/stats/summary`
   "외곽 매장 비율 56%, 이 매장들에 리뷰를 유도하는 게 이 서비스의 목표입니다."

### 예상 질문 대비

| 질문 | 답변 |
|---|---|
| "데이터 몇 건인가요?" | 제주 관광공사 음식점 719건(카페 226/음식점 493). 좌표 100%, 영업시간 99.6% 채워진 실데이터입니다. |
| "영업시간 데이터 어디서 났나요?" | 관광공사 데이터 원문을 자체 파서로 구조화했습니다. `<br>` 태그와 요일별 분기가 섞인 비정형 텍스트라 714건(99.3%) 파싱에 성공했고, 실패분은 `hours_confidence="low"`로 표시해 UI에서 구분합니다. 값을 지어내지 않습니다. |
| **"카공 필터가 왜 0건이죠?"** | **719건 전수 조사 결과 공공데이터에 콘센트·와이파이 언급이 0건입니다. 이게 이 서비스가 필요한 이유고, 없는 값을 추정으로 채우지 않았습니다. 지금 이 자리에서 제보 2건 넣어 채워보겠습니다.** (라이브 시연) |
| "콘센트 정보는 어떻게 모으나요?" | 크라우드소싱입니다. 한 명 말은 안 믿고 2인 합의를 요구하며, 점주 인증 제보는 즉시 확정합니다. 좌석수는 중앙값을 써서 극단값을 막습니다. 출처는 `cagong_source`로 항상 노출됩니다. |
| "브레이크 타임도 보나요?" | 봅니다. 영업시간만 보면 통과하는데 브레이크에 걸려 못 앉는 경우가 106건 있습니다. 추천 믿고 갔다가 헛걸음하는 게 가장 치명적이라 체류 구간을 나눠서 계산합니다. |
| "휴무일은요?" | 이 데이터에 휴무일이 없습니다(719건 전부 공란). 판정 로직은 구현돼 있고, 채우는 건 크라우드소싱 대상입니다. 모르는 걸 아는 척하지 않는 게 저희 원칙입니다. |
| "이동시간이 정확한가요?" | 카카오모빌리티 실시간 교통 반영입니다. 다만 지도 목록은 직선거리 추정으로 먼저 거르고, 상위 N개만 실제 호출합니다 — 쿼터와 응답속도 때문입니다. `travel_source`로 구분됩니다. |
| "도보는요?" | 카카오 도보 길찾기는 제휴 계약 전용이라 발급이 안 됩니다. 직선거리 × 1.3 ÷ 4km/h로 추정합니다. |
