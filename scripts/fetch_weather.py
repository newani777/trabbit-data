#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""기상청 단기예보를 받아 서울 '오늘 하루' 한 줄로 굽는다.

    python scripts/fetch_weather.py            # assets/data/weather.json

## 왜 앱이 직접 부르지 않나

전에는 위젯이 기상청 API 를 **앱에서 직접** 불렀다. 그러려면 인증키가 APK 에
들어가야 하고(뜯긴다), 호출 수가 사용자 수에 비례한다(하루 한도가 있다).
환율과 같은 방식으로 바꿨다 — 여기서 하루 몇 번 받아 정적 JSON 으로 두고,
앱은 그 파일만 읽는다. 키는 앱에 안 들어가고 호출은 사용자 수와 무관하다.

## 무엇을 담나 — '지금 몇 도'가 아니라 '오늘 하루'

실황(현재 기온)은 굽는 순간 낡는다. 대신 **오늘의 최저·최고·하늘·비 확률**을
담는다. 이건 아침에 한 번 구워도 하루 종일 맞는 정보다. 그리고 앱은 파일의
날짜가 오늘이 아니면 날씨를 **아예 그리지 않는다** — 어제 예보를 오늘 것처럼
보이는 것이 빈칸보다 나쁘다.

## 격자·시각

서울 시청 격자 nx=60, ny=127. 발표 시각은 05:00 판을 쓴다(오늘 최저·최고가
둘 다 들어 있는 가장 이른 판). 05:15 이전이면 전날 23:00 판을 쓴다.

인증키는 공공데이터포털 것(관광공사 TourAPI 와 같은 키)이다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "data", "weather.json")
API = ("https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
       "/getVilageFcst")
NX, NY = 60, 127

SKY = {"1": "clear", "3": "cloudy", "4": "overcast"}
PTY = {"0": "none", "1": "rain", "2": "rain", "3": "snow", "4": "shower",
       "5": "rain", "6": "rain", "7": "snow"}


def key() -> str:
    """환경변수 -> .env.local -> D:/config/opendata.env. 이미 URL 인코딩된 값이다."""
    v = os.environ.get("OPENDATA_API_KEY")
    if v:
        return v
    for path in (os.path.join(ROOT, ".env.local"), "D:/config/opendata.env"):
        if os.path.exists(path):
            for line in open(path, encoding="utf-8"):
                if line.startswith("OPENDATA_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("OPENDATA_API_KEY 를 못 찾았다.")


def base_of(now: datetime):
    if now.hour > 5 or (now.hour == 5 and now.minute >= 15):
        return now.strftime("%Y%m%d"), "0500"
    y = now - timedelta(days=1)
    return y.strftime("%Y%m%d"), "2300"


def fetch(now: datetime) -> list[dict]:
    bd, bt = base_of(now)
    q = {"dataType": "JSON", "numOfRows": 1000, "pageNo": 1,
         "base_date": bd, "base_time": bt, "nx": NX, "ny": NY}
    url = (f"{API}?serviceKey={key()}&"
           + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in q.items()))
    raw = urllib.request.urlopen(url, timeout=30).read().decode("utf-8")
    j = json.loads(raw)
    header = (j.get("response") or {}).get("header") or {}
    if header.get("resultCode") not in ("00", "0000"):
        raise RuntimeError(f"기상청 응답 오류: {header}")
    items = ((j["response"].get("body") or {}).get("items") or {}).get("item")
    return items or []


def digest(items: list[dict], today: str) -> dict:
    day = [i for i in items if i.get("fcstDate") == today]
    if not day:
        raise RuntimeError("오늘 예보가 응답에 없다.")

    def vals(cat, lo="0000", hi="2400"):
        return [(i["fcstTime"], i["fcstValue"]) for i in day
                if i.get("category") == cat and lo <= i.get("fcstTime", "") < hi]

    tmn = vals("TMN")
    tmx = vals("TMX")
    # 낮 시간(09~18시)의 하늘 상태 중 가장 잦은 것. 한 시각만 보면 아침 안개에
    # 속는다.
    sky_codes = [v for _, v in vals("SKY", "0900", "1900")] or [v for _, v in vals("SKY")]
    sky = SKY.get(Counter(sky_codes).most_common(1)[0][0], "cloudy") if sky_codes else None
    # 비·눈은 하루 중 한 번이라도 예보되면 그걸 쓴다 — 우산은 그 한 번 때문에 챙긴다.
    pty_codes = [v for _, v in vals("PTY", "0600", "2200")]
    rain = "none"
    for c in pty_codes:
        if PTY.get(c, "none") != "none":
            rain = PTY[c]
            break
    pops = [int(v) for _, v in vals("POP", "0600", "2200") if v.isdigit()]
    # 05:00 판에는 오늘 TMN 이 빠져 올 때가 있다(2026-09-02 실제로 그랬다).
    # 그럴 땐 시간별 기온(TMP)의 최저·최고로 대신한다 — 정오 이후엔 오차가
    # 한두 도라 표시용으로는 충분하다.
    tmps = [float(v) for _, v in vals("TMP") if v.replace("-", "").replace(".", "").isdigit()]
    tmin_v = float(tmn[0][1]) if tmn else (min(tmps) if tmps else None)
    tmax_v = float(tmx[0][1]) if tmx else (max(tmps) if tmps else None)

    return {
        "date": f"{today[:4]}-{today[4:6]}-{today[6:]}",
        "city": "Seoul",
        "tmin": round(tmin_v) if tmin_v is not None else None,
        "tmax": round(tmax_v) if tmax_v is not None else None,
        "sky": sky,
        "rain": rain,
        "pop": max(pops) if pops else None,
        "source": "KMA",
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    # 깃허브 액션은 저장소 뿌리에 굽는다. 기본값은 앱 안의 씨앗 파일.
    ap.add_argument("--out", default=OUT)
    out_path = ap.parse_args().out

    now = datetime.now()
    today = now.strftime("%Y%m%d")
    out = digest(fetch(now), today)
    out["fetched_at"] = now.strftime("%Y-%m-%dT%H:%M")
    if os.path.dirname(out_path):
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("weather.json:", json.dumps(out, ensure_ascii=False))
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from check_apis import stamp_success
        stamp_success("kma-forecast")
    except Exception as e:
        print("등록부 기록 실패(무시):", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
