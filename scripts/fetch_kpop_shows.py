#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KOPIS에서 이번 달·다음 달 서울 대중음악 공연을 받아 정적 JSON으로 굽는다.

    두 달치인 이유(광호님 2026-09-11): 월말이 되면 다음 달 공연이 있어야 계획을
    세운다. 이번 달만 실으면 25일쯤부터 달력이 쓸모를 잃는다. 앱은 지난 달을
    버리고 남은 달을 넘겨 보게 한다. 하루 두 번 trabbit-data 가 굽는다.

    python scripts/fetch_kpop_shows.py --key <인증키>
    python scripts/fetch_kpop_shows.py --key <인증키> --month 202610

## 왜 앱이 직접 안 부르나

환율과 같은 이유다. 한 달치를 모으려면 여러 번 호출해야 하는데(한 번에
31일·100건 제한), 그걸 사용자마다 하면 낭비다. 여기서 하루 한 번 받아
`assets/data/kpop_shows.json` 에 넣고 앱은 그 파일만 읽는다.
덤으로 **인증키가 APK 에 안 들어간다.**

## 장르를 코드가 아니라 이름으로 거르는 이유

API에 장르코드 파라미터(`shcate`)가 있지만, 대중음악 코드가 무엇인지
문서에서 확인하지 못했다. 잘못된 코드를 넣으면 조용히 0건이 온다.
그래서 **장르 없이 받아 응답의 `genrenm` 으로 거른다.** 호출이 몇 번
늘지만 틀린 결과를 내는 것보다 낫다.
코드를 확실히 알게 되면 `--genre` 로 넘겨서 호출을 줄일 수 있다.

## 주의

- 인증키는 발급일로부터 1년. **90일 이상 미사용이면 승인이 취소된다.**
  그래서 성공할 때마다 등록부에 도장을 찍는다(check_apis.stamp_success).
- 포스터는 KOPIS 서버 주소를 그대로 들고 온다. 우리가 복제하지 않는다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.parse
import re
import urllib.request
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import date

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "http://kopis.or.kr/openApi/restful/pblprfr"
SEOUL = "11"
GENRE_NAME = "대중음악"
OUT = os.path.join("assets", "data", "kpop_shows.json")

# 공연장 영어 이름. KOPIS 는 한국어만 준다.
#
# 손으로 적는다. 자동 번역을 돌리면 "장충체육관"이 "Jangchung Gymnasium"
# 같은 제각각 표기로 나오는데, 이건 **여행자가 지도에 찍어 넣을 이름**이라
# 통용 표기가 아니면 쓸모가 없다. 15곳뿐이라 손이 더 싸고 정확하다.
#
# 여기 없는 공연장은 한국어 이름 그대로 나가고, 스크립트가 경고를 찍는다.
VENUE_EN = {
    "올림픽공원": "Olympic Park",
    "잠실종합운동장": "Jamsil Sports Complex",
    "장충체육관": "Jangchung Arena",
    "블루스퀘어": "Blue Square",
    "세종문화회관": "Sejong Center",
    "예술의전당 [서울]": "Seoul Arts Center",
    "롯데콘서트홀": "Lotte Concert Hall",
    "LG아트센터 서울": "LG Arts Center Seoul",
    "KBS스포츠월드(아레나)": "KBS Arena",
    "예스24 라이브홀 (구. 악스코리아)": "YES24 Live Hall",
    "마포아트센터": "Mapo Art Center",
    "문화비축기지": "Oil Tank Culture Park",
    "고려대학교 화정체육관": "Korea University Hwajung Gymnasium",
    "연세대학교 대강당": "Yonsei University Grand Auditorium",
    "티켓링크 1975 씨어터(구.능동 어린이회관)": "Ticketlink 1975 Theater",
}

# 제목 꼬리표. "[서울]", "[서울 (앵콜)" 같은 건 지역 표시라 우리에겐
# 소음이다 — 애초에 서울 공연만 싣고 있다.
TITLE_TAIL = re.compile(r"\s*\[\s*서울[^\]]*\]?\s*$")


def clean_title(t):
    return TITLE_TAIL.sub("", t).strip()


ROWS = 100          # API 상한
MAX_PAGES = 12      # 한 달 서울 전체 공연이 1,200건을 넘지는 않는다


VENUE_CACHE = os.path.join("scripts", "venue_scale.json")
PLC_LIST = "http://kopis.or.kr/openApi/restful/prfplc"


def load_venues():
    try:
        return json.load(io.open(VENUE_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def save_venues(v):
    io.open(VENUE_CACHE, "w", encoding="utf-8").write(
        json.dumps(v, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def venue_ids(key):
    """서울 공연시설 이름 -> 시설ID. 한 번 받아 캐시에 남긴다."""
    out = {}
    for page in range(1, 30):
        q = {"service": key, "cpage": page, "rows": ROWS, "signgucode": SEOUL}
        with urllib.request.urlopen(
                PLC_LIST + "?" + urllib.parse.urlencode(q), timeout=20) as r:
            got = parse(r.read().decode("utf-8"))
        for g in got:
            if g.get("fcltynm") and g.get("mt10id"):
                out[g["fcltynm"]] = g["mt10id"]
        if len(got) < ROWS:
            break
    return out


def venue_seats(key, mt10id):
    """가장 큰 홀의 좌석 수. 시설 합계가 아니라 **최대 홀**을 쓴다 —
    작은 홀 여러 개를 합쳐 1,000석이 넘는 복합 공연장이 큰 공연장으로
    둔갑하는 것을 막으려는 것이다."""
    url = f"{PLC_LIST}/{mt10id}?service={key}"
    with urllib.request.urlopen(url, timeout=20) as r:
        root = ET.fromstring(r.read().decode("utf-8"))
    best = 0
    for el in root.iter("mt13"):
        t = el.findtext("seatscale") or ""
        n = int(re.sub(r"[^0-9]", "", t) or 0)
        best = max(best, n)
    if best == 0:  # 홀 정보가 없으면 시설 합계로 대신한다
        t = root.findtext(".//seatscale") or ""
        best = int(re.sub(r"[^0-9]", "", t) or 0)
    return best


def fetch_page(key, stdate, eddate, page, genre=None):
    q = {
        "service": key, "stdate": stdate, "eddate": eddate,
        "cpage": page, "rows": ROWS, "signgucode": SEOUL,
    }
    if genre:
        q["shcate"] = genre
    url = API + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read().decode("utf-8")


def parse(xml_text):
    """<dbs><db>… 를 dict 목록으로. 오류 응답이면 예외를 올린다."""
    root = ET.fromstring(xml_text)
    if root.tag != "dbs":
        # 인증 오류 등은 다른 루트로 온다. 내용을 그대로 보여준다.
        raise RuntimeError(
            "".join(root.itertext()).strip()[:300] or f"예상 밖 응답: {root.tag}")
    out = []
    for db in root.findall("db"):
        g = {c.tag: (c.text or "").strip() for c in db}
        out.append(g)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None,
                    help="KOPIS 인증키 (환경변수 KOPIS_KEY 도 가능)")
    ap.add_argument("--month", default=None, help="YYYYMM (기본: 이번 달)")
    ap.add_argument("--months", type=int, default=2,
                    help="시작 달부터 몇 달치 (기본 2 = 이번 달 + 다음 달)")
    ap.add_argument("--genre", default=None,
                    help="장르코드를 안다면 넘긴다. 없으면 이름으로 거른다")
    ap.add_argument("--min-seats", type=int, default=1000,
                    help="이 좌석 수 미만인 공연장은 뺀다 (0이면 전부)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    # 순서: --key > 환경변수 > .env.local
    # .env.local 을 마지막이 아니라 하나의 정식 경로로 두는 이유는,
    # 인증키를 명령줄에 적으면 셸 히스토리에 남기 때문이다.
    if not args.key:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from check_apis import _secret
            args.key = _secret("KOPIS_KEY")
        except Exception:
            args.key = os.environ.get("KOPIS_KEY")

    if not args.key:
        print("인증키가 없습니다. --key 또는 KOPIS_KEY 환경변수로 주세요.",
              file=sys.stderr)
        print("발급: https://kopis.or.kr/por/cs/openapi/openApiUseSend.do"
              "?menuId=MNU_00074", file=sys.stderr)
        return 2

    today = date.today()
    ym = args.month or today.strftime("%Y%m")
    y, m = int(ym[:4]), int(ym[4:])
    months = []
    for k in range(max(1, args.months)):
        yy, mm = y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1
        months.append(f"{yy}{mm:02d}")

    # 달마다 따로 부른다(한 번에 31일 제한). 두 달에 걸친 공연은 한 번만 싣는다.
    rows, seen = [], set()
    for mon in months:
        yy, mm = int(mon[:4]), int(mon[4:])
        last = monthrange(yy, mm)[1]
        stdate, eddate = f"{mon}01", f"{mon}{last:02d}"
        for page in range(1, MAX_PAGES + 1):
            try:
                got = parse(fetch_page(args.key, stdate, eddate, page, args.genre))
            except RuntimeError as e:
                print(f"API 오류: {e}", file=sys.stderr)
                return 3
            for g in got:
                pid = g.get("mt20id")
                if pid in seen:
                    continue
                seen.add(pid)
                rows.append(g)
            if len(got) < ROWS:
                break
        else:
            print(f"  [참고] {mon}: {MAX_PAGES}페이지에서 끊었다. 더 있을 수 있음.")

    shows = []
    for g in rows:
        if not args.genre and g.get("genrenm") != GENRE_NAME:
            continue
        shows.append({
            "id": g.get("mt20id", ""),
            "name": clean_title(g.get("prfnm", "")),
            "venue": g.get("fcltynm", ""),
            "venue_en": VENUE_EN.get(g.get("fcltynm", "")) or "",
            "from": g.get("prfpdfrom", ""),
            "to": g.get("prfpdto", ""),
            # 안드로이드는 API 28부터 평문 http 를 기본 차단한다. KOPIS 는
            # 같은 주소를 https 로도 주므로 여기서 바꿔 둔다. 안 바꾸면
            # 포스터가 폰에서만 조용히 안 뜬다 — 웹 프리뷰에서는 멀쩡해서
            # 놓치기 딱 좋은 종류의 버그다.
            "poster": g.get("poster", "").replace("http://", "https://"),
            "state": g.get("prfstate", ""),
        })

    # ── 공연장 규모로 거른다 ────────────────────────────────────────
    # 대중음악 전체를 그대로 실으면 90%가 홍대 인디 클럽·재즈바다. 나쁜
    # 정보는 아니지만 **여행객이 일부러 찾아가는 공연**과 섞이면 캘린더가
    # 못 쓰게 된다. 이름으로 고르면 매번 손이 가므로 좌석 수로 자른다.
    if args.min_seats > 0:
        cache = load_venues()
        ids = cache.get("_ids") or {}
        seats = cache.get("_seats") or {}
        need = {x["venue"] for x in shows} - set(seats)
        if need and not ids:
            print(f"  공연장 목록을 받는 중…")
            ids = venue_ids(args.key)
        for name in sorted(need):
            mt = ids.get(name)
            if not mt:
                seats[name] = 0   # 목록에 없는 곳은 소규모로 본다
                continue
            try:
                seats[name] = venue_seats(args.key, mt)
            except Exception:
                seats[name] = 0
        if need:
            save_venues({"_ids": ids, "_seats": seats})
            print(f"  공연장 {len(need)}곳 규모 확인 (캐시: {VENUE_CACHE})")

        before = len(shows)
        shows = [x for x in shows if seats.get(x["venue"], 0) >= args.min_seats]
        for x in shows:
            x["seats"] = seats.get(x["venue"], 0)
        print(f"  {args.min_seats}석 이상: {len(shows)}건 (거른 것 {before - len(shows)}건)")

    missing_en = sorted({x["venue"] for x in shows if not x["venue_en"]})
    if missing_en:
        print("  [참고] 영어 이름이 없는 공연장 — VENUE_EN 에 추가할 것:")
        for v in missing_en:
            print(f"           {v}")

    # 시작일 순. 달력 옆 띠를 훑으면 시간 순서대로 읽히는 게 자연스럽다.
    shows.sort(key=lambda s: s["from"])

    if not shows:
        # 0건을 파일로 덮으면 앱에서 코너가 사라진다. 그건 되돌리기 어려우니
        # 기존 파일을 남기고 사람에게 알린다.
        print(f"{ym}: 조건에 맞는 공연이 0건입니다. 기존 파일을 그대로 둡니다.",
              file=sys.stderr)
        print(f"  (전체 {len(rows)}건을 받았고 그중 '{GENRE_NAME}' 이 없었습니다. "
              f"장르명이 바뀌었는지 확인해 보세요.)", file=sys.stderr)
        return 1

    payload = {
        # month 는 옛 앱이 읽는 자리라 남긴다(첫 달). 새 앱은 months 를 본다.
        "month": ym,
        "months": months,
        "fetched": today.strftime("%Y%m%d"),
        "source": "KOPIS",
        "shows": shows,
    }
    outdir = os.path.dirname(args.out)
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from check_apis import stamp_success
        stamp_success("kopis-performances")
    except Exception as e:
        print(f"  [참고] 등록부 기록 실패: {e}", file=sys.stderr)

    print(f"OK  {args.out}  {ym}  전체 {len(rows)}건 중 {GENRE_NAME} {len(shows)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
