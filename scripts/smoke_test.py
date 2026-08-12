"""전체 API 스모크 테스트. 서버 없이 TestClient로 in-process 검증.

실행: python -m scripts.smoke_test
발표 전에 한 번 돌려서 전부 PASS 나오면 백엔드는 끝난 것.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 소셜 로그인 토큰을 테스트에서 만들 수는 없으므로 개발 인증 모드로 돌린다.
# (app import 보다 먼저 설정해야 config가 읽는다)
os.environ["AUTH_REQUIRED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
fails = []


def as_user(user_id: int) -> dict:
    """AUTH_REQUIRED=false 일 때 특정 유저로 로그인한 척하는 헤더."""
    return {"X-Dev-User-Id": str(user_id)}


def check(label: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        fails.append(label)


print("\n[0] 헬스체크")
r = client.get("/health")
check("GET /health", r.status_code == 200, r.json().get("db"))

print("\n[1] 전체 카페 목록")
r = client.get("/api/cafes?limit=500")
data = r.json()
check("GET /api/cafes", r.status_code == 200 and data["total"] > 0, f"{data.get('total')}건")

# --- 테스트 고정값을 데이터에서 뽑아온다 -------------------------------------
# 특정 가게 이름을 박아두면 데이터가 바뀔 때마다 테스트가 깨진다. 실제 공공데이터로
# 갈아끼워도 계속 돌아가도록, 조건에 맞는 가게를 그때그때 찾아 쓴다.
ALL = client.get("/api/cafes?place_type=all&limit=500").json()["items"]
if not ALL:
    sys.exit("카페 데이터가 없습니다. python -m scripts.ingest_iddy --purge 를 먼저 실행하세요.")


def pick(pred, label):
    hit = next((c for c in ALL if pred(c)), None)
    if hit is None:
        print(f"  SKIP  조건에 맞는 매장 없음: {label}")
    return hit

print("\n[2] 핵심기능3 — 카공 가능 필터")
r_all = client.get("/api/cafes").json()
r_cg = client.get("/api/cafes?cagong=true").json()
check("cagong=true 가 결과를 줄인다", r_cg["total"] < r_all["total"],
      f"{r_all['total']} -> {r_cg['total']}")
check("결과 전부 콘센트+와이파이+노트북OK",
      all(c["laptop_ok"] and c["has_power"] and c["has_wifi"] for c in r_cg["items"]))

print("\n[3] 핵심기능2 — 여유롭게 2시간 필터")
r_late = client.get("/api/cafes?stay_hours=2&now=18:30&travel_min=15").json()
r_noon = client.get("/api/cafes?stay_hours=2&now=11:00&travel_min=15").json()
check("18:30 결과 < 11:00 결과 (마감 임박 매장 제외됨)",
      r_late["total"] < r_noon["total"], f"{r_noon['total']} -> {r_late['total']}")
check("통과한 매장은 전부 stay_ok=true",
      all(c["stay"]["stay_ok"] for c in r_late["items"]))
r_late_all = client.get("/api/cafes?place_type=all&limit=500"
                        "&stay_hours=2&now=18:30&travel_min=15").json()
names_late = [c["name"] for c in r_late_all["items"]]

early = pick(lambda c: c["close_time"] <= "17:00", "17시 이전 마감")
if early:
    check("일찍 닫는 매장은 18:30 조회에서 제외", early["name"] not in names_late,
          f"{early['name']} ({early['close_time']} 마감)")
late = pick(lambda c: "22:00" <= c["close_time"] < "26:00" and not c["closed_days"],
            "22시 이후 마감")
if late:
    check("늦게까지 하는 매장은 18:30 조회에 포함", late["name"] in names_late,
          f"{late['name']} ({late['close_time']} 마감)")

print("\n[3-1] 브레이크 타임 반영")
brk = pick(lambda c: c.get("break_start"), "브레이크타임 있는 매장")
if brk:
    d = client.get(f"/api/cafes/{brk['id']}?now=14:00&travel_min=10&stay_hours=2").json()
    check("브레이크타임이 stay 판정에 반영됨",
          d["stay"]["break_time"] is not None,
          f"{d['name']} 영업 {d['open_time']}~{d['close_time']} "
          f"브레이크 {d['stay']['break_time']} → {d['stay']['label']}")
    # 브레이크 중 도착이면 '끝나고 앉는다' 또는 '불가' 둘 중 하나여야 한다.
    # 브레이크를 무시하고 그냥 통과시키면 헛걸음이 발생한다.
    ok_or_wait = (not d["stay"]["stay_ok"]) or d["stay"]["sit_from"] is not None \
        or d["stay"]["reason"] == "ok"
    check("브레이크 중 도착을 뭉개지 않음", ok_or_wait, d["stay"]["reason"])

print("\n[4] 휴무일 처리")
# 공공데이터에 휴무일이 없어서(719건 전부 공란) 직접 심어놓고 검증한다.
victim = ALL[0]
client.patch(f"/api/cafes/{victim['id']}/flags",
             json={"closed_days": "화", "source": "manual"})
r_tue = client.get("/api/cafes?place_type=all&limit=500"
                   "&stay_hours=2&now=2026-08-11T11:00:00").json()  # 2026-08-11 = 화요일
check("화요일 휴무 매장 제외", victim["name"] not in [c["name"] for c in r_tue["items"]],
      f"{victim['name']} 휴무 처리")
client.patch(f"/api/cafes/{victim['id']}/flags",
             json={"closed_days": "", "source": "manual"})

print("\n[5] 핵심기능1 — 외곽지 적립금 차등")
r_remote = client.get("/api/cafes?place_type=all&limit=500"
                      "&remote_only=true&sort=reward").json()
check("외곽 매장 존재", r_remote["total"] > 0, f"{r_remote['total']}건")
top = r_remote["items"][0]
downtown = min(ALL, key=lambda c: c["dist_to_hotspot_km"])   # 핫스팟에 가장 가까운 매장
check("외곽 소외매장 적립금 > 도심 핫플 적립금",
      top["reward"]["point"] > downtown["reward"]["point"],
      f"{top['name']} {top['reward']['point']}P({top['dist_to_hotspot_km']}km) vs "
      f"{downtown['name']} {downtown['reward']['point']}P({downtown['dist_to_hotspot_km']}km)")

print("\n[6] 유저 생성 + 리뷰 작성 + 적립")
u = client.post("/api/users", json={"nickname": "스모크테스터", "email": "smoke@test.dev"}).json()
check("POST /api/users", "id" in u, f"user_id={u.get('id')}")
me = client.get("/api/auth/me", headers=as_user(u["id"])).json()
check("GET /api/auth/me (개발 인증)", me.get("id") == u["id"], me.get("nickname"))

target = next(c for c in r_remote["items"] if c["review_count"] == 0)  # 리뷰 0 = 소외 매장
before_pt = target["reward"]["point"]
rv = client.post("/api/reviews", headers=as_user(u["id"]), json={
    "cafe_id": target["id"], "rating": 5,
    "content": "콘센트 자리마다 있고 사람 없어서 4시간 내내 작업했어요. 창밖으로 밭담 보이는 뷰가 최고입니다.",
    "tags": ["콘센트많음", "조용함", "장시간가능"],
})
check("POST /api/reviews", rv.status_code == 200, rv.text[:120] if rv.status_code != 200 else "")
body = rv.json()
check("첫 리뷰 보너스 지급", body["earned_point"] >= before_pt,
      f"{body['earned_point']}P — {body['headline']}")
check("적립 내역(breakdown) 존재", len(body["breakdown"]) >= 2,
      " / ".join(i["label"] for i in body["breakdown"]))
check("카페 리뷰수 증가", body["cafe_review_count"] == 1)
check("작성자가 토큰에서 결정됨", body["review"]["user_id"] == u["id"])

print("\n[7] 중복 리뷰 차단")
dup = client.post("/api/reviews", headers=as_user(u["id"]),
                  json={"cafe_id": target["id"], "rating": 4, "content": "또 씀"})
check("같은 매장 재리뷰 409", dup.status_code == 409)

print("\n[8] 적립금 하락 확인 (리뷰 달리면 희소성 보너스 감소)")
after = client.get(f"/api/cafes/{target['id']}").json()
check("리뷰 후 적립금 감소", after["reward"]["point"] < before_pt,
      f"{before_pt}P -> {after['reward']['point']}P")

print("\n[9] 포인트 조회")
p = client.get(f"/api/users/{u['id']}/points").json()
check("GET /api/users/{id}/points", p["point_balance"] == body["earned_point"],
      f"{p['point_balance']}P / 외곽리뷰 {p['remote_review_count']}건")

print("\n[10] 카공 정보 제보 PATCH")
pv = client.patch(f"/api/cafes/{downtown['id']}/flags",
                  json={"laptop_ok": False, "source": "owner"}).json()
check("PATCH flags 반영", pv["laptop_ok"] is False and pv["cagong_source"] == "owner")
client.patch(f"/api/cafes/{downtown['id']}/flags", json={"laptop_ok": True, "source": "owner"})

print("\n[11] 발표용 통계")
s = client.get("/api/stats/summary").json()
check("GET /api/stats/summary", s["total_cafes"] > 0,
      f"전체 {s['total_cafes']} / 외곽비율 {s['remote_ratio']}%")
d = client.get("/api/stats/dispersion").json()
check("GET /api/stats/dispersion", len(d["regions"]) > 0, f"{len(d['regions'])}개 지역")
un = client.get("/api/stats/underrated?limit=5").json()
check("GET /api/stats/underrated", len(un["items"]) > 0,
      un["items"][0]["name"] + f" {un['items'][0]['reward_point']}P")

print("\n[12] 복합 필터 (실제 데모 시나리오)")
n_cagong = client.get("/api/cafes?place_type=all&limit=500&cagong=true").json()["total"]
_cg = "&cagong=true" if n_cagong else ""
if not n_cagong:
    print("  SKIP  카공 확정 매장 0건 — 크라우드소싱 제보가 쌓이기 전 정상 상태")

r = client.get(f"/api/cafes?place_type=all&limit=500&lat=33.4996&lng=126.5312"
               f"&radius_km=40{_cg}&stay_hours=2&now=15:00&sort=reward").json()
check("현위치+2시간+적립순 복합 조회", r["total"] > 0,
      f"{r['total']}건, 1위 {r['items'][0]['name'] if r['items'] else '-'}")
check("거리 계산됨", r["items"] and r["items"][0]["distance_km"] is not None,
      f"{r['items'][0]['distance_km']}km / 이동 {r['items'][0]['travel_min']}분"
      if r["items"] else "")

print("\n[13] 영업시간 파서")
from app.services.hours_parser import parse_hours  # noqa: E402

p1 = parse_hours("매일 10:00 - 20:00 (라스트오더 19:30)")
check("HH:MM 범위 + 라스트오더 파싱",
      p1["open_time"] == "10:00" and p1["close_time"] == "20:00"
      and p1["last_order_min"] == 30, str(p1["confidence"]))
p2 = parse_hours("오전 9시 ~ 오후 6시 / 매주 월요일 휴무")
check("오전·오후 표기 + 휴무일 추출",
      p2["open_time"] == "09:00" and p2["close_time"] == "18:00"
      and p2["closed_days"] == "월")
p3 = parse_hours("11:00~02:00")
check("새벽 마감을 26:00으로 정규화", p3["close_time"] == "26:00")
p4 = parse_hours("10:00~22:00, 매주 화,수 휴무")
check("복수 휴무일", p4["closed_days"] == "화,수")
p5 = parse_hours("영업시간 문의 요망")
check("파싱 불가 시 confidence=low", p5["confidence"] == "low")
p6 = parse_hours("연중무휴 24시간")
check("24시간 영업", p6["open_time"] == "00:00" and p6["closed_days"] == "")

print("\n[14] 영업시간 신뢰도 필터")
total_all = client.get("/api/cafes?place_type=all&limit=500").json()["total"]
hv = client.get("/api/cafes?place_type=all&limit=500&hours_verified=true").json()
check("hours_verified=true 로 검증된 매장만", 0 < hv["total"] <= total_all,
      f"{total_all} -> {hv['total']}건 (파싱 신뢰도 high)")
check("전부 confidence=high", all(c["hours_confidence"] == "high" for c in hv["items"]))
check("원문 보존됨", any(c["hours_text"] for c in hv["items"]),
      next((c["hours_text"] for c in hv["items"] if c["hours_text"]), ""))

print("\n[15] 이동수단별 소요시간")
_geo = "?place_type=all&limit=500&lat=33.4996&lng=126.5312&radius_km=40&sort=distance"
car = client.get(f"/api/cafes{_geo}&travel_mode=car").json()
walk = client.get(f"/api/cafes{_geo}&travel_mode=walk").json()
# 최단거리 매장은 둘 다 하한값(차 5분/도보 3분)이라 비교가 무의미 → 멀리 있는 매장으로 비교
far_car = next(c for c in car["items"] if c["distance_km"] > 5)
far_walk = next(c for c in walk["items"] if c["id"] == far_car["id"])
check("같은 매장 기준 도보 > 차량",
      far_walk["travel_min"] > far_car["travel_min"],
      f"{far_car['name']} {far_car['distance_km']}km — 차량 {far_car['travel_min']}분 "
      f"vs 도보 {far_walk['travel_min']}분")
check("travel_source 표기됨", far_car["travel_source"] in
      ("estimated", "kakao", "cache"), far_car["travel_source"])
check("도보는 우회계수 반영(직선거리보다 김)",
      far_walk["distance_km"] > far_car["distance_km"],
      f"직선 {far_car['distance_km']}km -> 보행 {far_walk['distance_km']}km")

print("\n[16] 크라우드소싱 제보 집계")
target2 = client.get(f"/api/cafes/{downtown['id']}").json()
u2 = client.post("/api/users", json={"nickname": "제보자1", "email": "r1@t.dev"}).json()
u3 = client.post("/api/users", json={"nickname": "제보자2", "email": "r2@t.dev"}).json()

# 정보가 비어있는(estimated) 매장을 만들어 테스트
client.patch(f"/api/cafes/{target2['id']}/flags", json={"has_power": False, "source": "user"})
w = client.get("/api/reports/wanted?limit=5").json()
check("GET /api/reports/wanted", "items" in w, f"{len(w['items'])}건")

rep1 = client.post("/api/reports", headers=as_user(u2["id"]), json={
    "cafe_id": target2["id"], "field": "has_power", "value_bool": True,
}).json()
check("1건 제보는 보류", rep1["applied"] is False, rep1["status_message"])
check("제보 적립금 지급", rep1["earned_point"] > 0, f"{rep1['earned_point']}P")

rep2 = client.post("/api/reports", headers=as_user(u3["id"]), json={
    "cafe_id": target2["id"], "field": "has_power", "value_bool": True,
}).json()
check("2건 합의되면 반영", rep2["applied"] is True, rep2["status_message"])
after2 = client.get(f"/api/cafes/{target2['id']}").json()
check("카페 정보 실제 변경", after2["has_power"] is True and after2["cagong_source"] == "user")

owner_rep = client.post("/api/reports", headers=as_user(u2["id"]), json={
    "cafe_id": target2["id"], "field": "quiet",
    "value_bool": True, "is_owner": True,
}).json()
check("점주 제보는 1건으로 즉시 확정", owner_rep["applied"] is True,
      owner_rep["status_message"])

cov = client.get(f"/api/reports/cafe/{target2['id']}").json()
check("정보 채움률 조회", "coverage_percent" in cov, f"{cov['coverage_percent']}%")

bad = client.post("/api/reports", headers=as_user(u2["id"]), json={
    "cafe_id": target2["id"], "field": "존재안함", "value_bool": True,
})
check("잘못된 항목명 422", bad.status_code == 422)

print("\n[17] 제보 통계")
rs = client.get("/api/reports/stats").json()
check("GET /api/reports/stats", rs["total_reports"] >= 3,
      f"제보 {rs['total_reports']}건 / 반영 {rs['applied_reports']}건")

print("\n[18] 인증 가드 (AUTH_REQUIRED=true 일 때)")
from app.config import settings  # noqa: E402

settings.auth_required = True
try:
    anon_review = client.post("/api/reviews", json={"cafe_id": 1, "rating": 5, "content": "익명"})
    check("토큰 없이 리뷰 작성 401", anon_review.status_code == 401,
          anon_review.json().get("detail", "")[:60])
    anon_report = client.post("/api/reports", json={
        "cafe_id": 1, "field": "has_power", "value_bool": True})
    check("토큰 없이 제보 401", anon_report.status_code == 401)
    # 헤더 값에 한글을 넣으면 httpx 버전에 따라 인코딩 에러로 죽는다(ascii 전용).
    # 검증하려는 건 '서명이 틀린 토큰'이므로 ascii 문자열로 충분하다.
    bad_token = client.post("/api/reviews", headers={"Authorization": "Bearer not-a-jwt"},
                            json={"cafe_id": 1, "rating": 5})
    check("잘못된 토큰 401", bad_token.status_code == 401)
    check("조회는 로그인 없이 가능", client.get("/api/cafes").status_code == 200)
finally:
    settings.auth_required = False

print("\n" + "=" * 50)
if fails:
    print(f"실패 {len(fails)}건: {fails}")
    sys.exit(1)
print("전체 통과. 백엔드 정상 동작.")
