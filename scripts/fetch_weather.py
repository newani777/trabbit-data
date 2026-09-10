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

## 시간대별과 미세먼지 (2026-09-11, 광호님)

날씨 알약을 누르고 들어오면 오늘 하루 한 줄만 있었다. 두 칸을 더 굽는다.

- `hourly` — 지금부터 36시간, 1시간 간격(기온·하늘·비·강수확률). 하루 요약은
  05:00 판을 쓰지만 시간대별은 **가장 최근 발표 판**을 쓴다(13시 굽기면 11:00 판).
- `air` — 에어코리아 미세먼지 예보(PM10·PM2.5, 서울, 오늘·내일). **같은 키**를
  쓰지만 공공데이터포털에서 「한국환경공단_에어코리아_대기오염정보」 활용신청이
  따로 필요하다. 승인 전에는 조용히 건너뛰고 앱은 그 칸을 안 그린다.

둘 다 예보라 아침에 구워도 그날 저녁까지 맞는다. 옛 앱은 모르는 키를 무시한다.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

# **시계는 반드시 한국이다.** 굽는 기계가 어디 있든(깃허브 러너는 UTC) 기상청
# 예보도, 앱이 「오늘」인지 판정하는 기준도 한국 날짜다. UTC 로 구우면 06:00 KST
# 실행분이 전날로 찍혀 앱에서 줄이 통째로 사라진다 — 2026-09-03 에 겪었다.
KST = timezone(timedelta(hours=9))

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


BASES = ["0200", "0500", "0800", "1100", "1400", "1700", "2000", "2300"]


def _get(url: str) -> str:
    last = None
    for attempt in range(1, 4):
        try:
            return urllib.request.urlopen(url, timeout=45).read().decode("utf-8")
        except Exception as e:  # 타임아웃·일시적 5xx
            last = e
            print(f"  기상청 호출 {attempt}/3 실패: {e}", flush=True)
            if attempt < 3:
                time.sleep(10)
    raise RuntimeError(f"기상청 3회 모두 실패: {last}")


def fetch_base(bd: str, bt: str) -> list[dict]:
    """한 발표 판을 통째로. 한 쪽 1000줄을 넘으면 다음 쪽까지 받는다."""
    out: list[dict] = []
    page = 1
    while True:
        q = {"dataType": "JSON", "numOfRows": 1000, "pageNo": page,
             "base_date": bd, "base_time": bt, "nx": NX, "ny": NY}
        url = (f"{API}?serviceKey={key()}&"
               + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in q.items()))
        j = json.loads(_get(url))
        header = (j.get("response") or {}).get("header") or {}
        if header.get("resultCode") not in ("00", "0000"):
            raise RuntimeError(f"기상청 응답 오류: {header}")
        body = j["response"].get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        out.extend(items)
        total = int(body.get("totalCount") or 0)
        if not items or page * 1000 >= total:
            return out
        page += 1


def fetch(now: datetime) -> list[dict]:
    """하루 요약용 판(05:00, 이르면 전날 23:00)."""
    return fetch_base(*base_of(now))


def latest_base(now: datetime):
    """지금 받을 수 있는 가장 최근 발표 판. 발표 10분 뒤부터 나오니 15분 여유를 둔다."""
    t = now - timedelta(minutes=15)
    hhmm = t.strftime("%H%M")
    done = [b for b in BASES if b <= hhmm]
    if done:
        return t.strftime("%Y%m%d"), done[-1]
    y = t - timedelta(days=1)
    return y.strftime("%Y%m%d"), "2300"


def hourly(items: list[dict], now: datetime, hours: int = 36) -> list[dict]:
    """지금 시부터 [hours] 시간, 1시간 간격. 시각은 서울 시각 'YYYY-MM-DDTHH:00'."""
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=hours)
    by: dict[datetime, dict] = {}
    for i in items:
        d, t = i.get("fcstDate"), i.get("fcstTime")
        if not d or not t:
            continue
        try:
            at = datetime.strptime(d + t, "%Y%m%d%H%M").replace(tzinfo=KST)
        except ValueError:
            continue
        if at < start or at > end:
            continue
        by.setdefault(at, {})[i.get("category")] = i.get("fcstValue")
    out = []
    for at in sorted(by):
        m = by[at]
        try:
            tmp = round(float(m["TMP"]))
        except (KeyError, TypeError, ValueError):
            continue
        pop = m.get("POP")
        out.append({
            "t": at.strftime("%Y-%m-%dT%H:00"),
            "tmp": tmp,
            "sky": SKY.get(str(m.get("SKY", ""))),
            "rain": PTY.get(str(m.get("PTY", "0")), "none"),
            "pop": int(pop) if str(pop or "").isdigit() else None,
        })
    return out


AIR_API = ("https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
           "/getMinuDustFrcstDspth")
GRADE = {"좋음": "good", "보통": "moderate", "나쁨": "bad", "매우나쁨": "very_bad"}


def air(now: datetime) -> dict | None:
    """에어코리아 미세먼지 예보 — 서울, 오늘·내일(발표가 닿는 데까지).

    같은 날짜에 발표가 여러 번 있다(05·11·17·23시). 가장 늦은 발표를 쓴다.
    informGrade 는 '서울 : 보통,제주 : 좋음,…' 꼴이다.
    """
    today = now.strftime("%Y-%m-%d")
    per_day: dict[str, dict] = {}
    for code, field in (("PM10", "pm10"), ("PM25", "pm25")):
        q = {"returnType": "json", "numOfRows": 100, "pageNo": 1,
             "searchDate": today, "InformCode": code}
        url = (f"{AIR_API}?serviceKey={key()}&"
               + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in q.items()))
        j = json.loads(urllib.request.urlopen(url, timeout=30).read().decode("utf-8"))
        body = (j.get("response") or {}).get("body") or {}
        latest: dict[str, tuple[str, str]] = {}
        for it in body.get("items") or []:
            if it.get("informCode") != code:
                continue
            day, when = it.get("informData"), it.get("dataTime") or ""
            grade = None
            for part in (it.get("informGrade") or "").split(","):
                name, _, g = part.partition(":")
                if name.strip() == "서울":
                    grade = GRADE.get(g.strip().replace(" ", ""))
            if not day or not grade:
                continue
            if day not in latest or when > latest[day][0]:
                latest[day] = (when, grade)
        for day, (_, g) in latest.items():
            per_day.setdefault(day, {"date": day})[field] = g
    days = [per_day[d] for d in sorted(per_day) if d >= today]
    return {"source": "AirKorea", "days": days} if days else None


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


def digest_days(items: list[dict], start: datetime, count: int = 3) -> list[dict]:
    """오늘부터 며칠치를 담는다.

    ## 왜 하루치로 안 되나 (2026-09-04)

    앱은 「오늘 날짜인 파일만 참」으로 본다. 그런데 굽는 일은 06:00 KST 에 한
    번뿐이라, **자정부터 06시까지는 파일이 어제 날짜**다. 그 사이 날씨 줄이
    통째로 사라졌다(광호님 새벽 제보). 굽는 시각을 늘리는 건 워크플로 권한이
    필요해서, 대신 **내일치를 미리 담는다.** 새벽에 앱이 어제 파일을 받아도
    그 안에서 오늘 날짜를 찾아 쓴다.
    """
    out = []
    for n in range(count):
        d = (start + timedelta(days=n)).strftime("%Y%m%d")
        try:
            out.append(digest(items, d))
        except RuntimeError:
            break  # 예보가 닿는 데까지만
    return out


def _probe() -> None:
    """실패 원인을 로그에 남긴다. 해외에서 도는 러너에서는 통째로 막히기도 한다."""
    import socket
    import time as _t
    host = urllib.parse.urlparse(API).hostname or ""
    try:
        ip = socket.gethostbyname(host)
        print(f"  진단: {host} -> {ip} (DNS 정상)", flush=True)
    except Exception as e:
        print(f"  진단: DNS 실패 {host}: {e}", flush=True)
        return
    t0 = _t.time()
    try:
        with socket.create_connection((ip, 443), timeout=20):
            print(f"  진단: 443 연결 성공 ({_t.time()-t0:.1f}s) — 막힌 게 아니라 느린 것", flush=True)
    except Exception as e:
        print(f"  진단: 443 연결 실패 ({_t.time()-t0:.1f}s): {e} — 해외 IP 차단 가능성", flush=True)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    # 깃허브 액션은 저장소 뿌리에 굽는다. 기본값은 앱 안의 씨앗 파일.
    ap.add_argument("--out", default=OUT)
    out_path = ap.parse_args().out

    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    try:
        got = fetch(now)
        out = digest(got, today)
        # 내일(+모레)까지 같이 담는다 — 새벽 공백을 메우는 장치. 자세한 이유는
        # digest_days 주석.
        out["days"] = digest_days(got, now)
    except Exception as e:
        # 굽기 실패는 사고가 아니다. 앱은 어제 파일을 계속 읽고, 날짜가 지나면
        # 스스로 줄을 감춘다. 여기서 1 을 뱉으면 뒤 단계(환율·커밋)까지 막힌다.
        print(f"날씨 굽기 실패 — 기존 파일 유지: {e}", flush=True)
        _probe()
        return 0
    # 시간대별 — 가장 최근 발표 판. 실패하면 하루 요약 판에서라도 뽑는다.
    try:
        lb = latest_base(now)
        items_h = got if lb == base_of(now) else fetch_base(*lb)
        out["hourly"] = hourly(items_h, now)
        out["hourly_base"] = "".join(lb)
    except Exception as e:
        print(f"시간대별 굽기 실패 — 요약 판으로 대신: {e}", flush=True)
        try:
            out["hourly"] = hourly(got, now)
        except Exception:
            pass
    # 미세먼지 — 활용신청 전이면 403 이 온다. 그 칸만 빼고 나머지는 굽는다.
    try:
        a = air(now)
        if a:
            out["air"] = a
    except Exception as e:
        print(f"미세먼지 굽기 건너뜀: {e}", flush=True)
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
