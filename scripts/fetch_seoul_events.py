#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""서울시 문화행사 정보를 받아 「서울 Upcoming」 정적 JSON으로 굽는다(2026-10-10 광호님).

    python scripts/fetch_seoul_events.py --out seoul_events.json        # 키는 SEOUL_OPENAPI_KEY 환경변수

- 원본: 서울 열린데이터광장 「서울시 문화행사 정보」(OA-15486, openapi.seoul.go.kr/…/culturalEventInfo).
  서울시 누리집 첫 화면 「서울 Upcoming!」이 같은 자료다.
- 오늘부터 DAYS 일 안에 열려 있는 행사만 남긴다. 「교육/체험」은 뺀다 — 구청 주민 강좌가 대부분이라 여행자와 맞지 않는다.
- 제목·장소는 한국어 그대로(번역 안 함, 광호님 10-10).
- **포스터는 서울시 서버 주소만 들고 온다. 우리가 복제하지 않는다**(K-pop 포스터와 같은 원칙).
- 한 번에 1,000건까지라 전체를 끝까지 넘겨 받는다(약 20회). 실패하면 어제 파일을 그대로 둔다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, timedelta, datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "http://openapi.seoul.go.kr:8088/{key}/json/culturalEventInfo/{a}/{b}/"
PAGE = 1000
DAYS = 21
SKIP = {"교육/체험"}
# 같은 날 안의 차례 — 축제가 맨 앞, 공연, 전시 순(서울시 첫 화면도 큰 행사가 앞이다).
RANK = {"축제-문화/예술": 0, "축제-전통/역사": 0, "축제-시민화합": 0, "축제-관광/체육": 0, "축제-자연/경관": 0, "축제-기타": 1,
        "콘서트": 2, "뮤지컬/오페라": 2, "국악": 2, "클래식": 2, "무용": 3, "연극": 3, "독주/독창회": 3, "영화": 4, "전시/미술": 4, "기타": 6}


def kst_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def fetch(key: str, a: int, b: int) -> dict:
    with urllib.request.urlopen(API.format(key=key, a=a, b=b), timeout=60) as r:
        return json.load(r).get("culturalEventInfo") or {}


def day(s: str) -> str:
    return (s or "")[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="seoul_events.json")
    a = ap.parse_args()
    key = os.environ.get("SEOUL_OPENAPI_KEY", "").strip()
    if not key:
        sys.exit("SEOUL_OPENAPI_KEY 없음")
    today = kst_today()
    last = today + timedelta(days=DAYS - 1)
    first = fetch(key, 1, 1)
    total = int(first.get("list_total_count") or 0)
    if not total:
        sys.exit(f"응답 이상: {first.get('RESULT')}")
    rows = []
    for start in range(1, total + 1, PAGE):
        rows += fetch(key, start, min(start + PAGE - 1, total)).get("row") or []
    events, seen = [], set()
    for r in rows:
        s, e = day(r.get("STRTDATE")), day(r.get("END_DATE"))
        if not s or not e or e < today.isoformat() or s > last.isoformat():
            continue
        cat = (r.get("CODENAME") or "").strip()
        if cat in SKIP:
            continue
        title = " ".join((r.get("TITLE") or "").split())
        k = (title, s, e)
        if not title or k in seen:
            continue
        seen.add(k)
        try:
            lat, lon = float(r.get("LAT") or 0), float(r.get("LOT") or 0)
        except ValueError:
            lat = lon = 0.0
        # 서울시 자료는 위도·경도 칸이 뒤바뀐 줄이 있다 — 서울 범위로 바로잡는다.
        if 126 < lat < 128 and 37 < lon < 38:
            lat, lon = lon, lat
        ok = 37.3 < lat < 37.8 and 126.6 < lon < 127.3
        events.append({
            "t": title,
            "c": cat,
            "gu": (r.get("GUNAME") or "").strip(),
            "p": " ".join((r.get("PLACE") or "").split()),
            "s": s,
            "e": e,
            "free": (r.get("IS_FREE") or "") == "무료",
            "fee": " ".join((r.get("USE_FEE") or "").split())[:80],
            "img": (r.get("MAIN_IMG") or "").strip(),
            "url": (r.get("HMPG_ADDR") or r.get("ORG_LINK") or "").strip(),
            **({"lat": round(lat, 6), "lon": round(lon, 6)} if ok else {}),
        })
    events.sort(key=lambda x: (x["s"] > today.isoformat(), RANK.get(x["c"], 5), x["e"], x["t"]))
    doc = {
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "서울특별시 문화행사 정보(서울 열린데이터광장 OA-15486)",
        "from": today.isoformat(),
        "to": last.isoformat(),
        "events": events,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"전체 {total} → {today}~{last} 행사 {len(events)}건 · {os.path.getsize(a.out) // 1024}KB")


if __name__ == "__main__":
    main()
