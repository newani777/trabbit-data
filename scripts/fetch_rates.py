#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""한국수출입은행 환율을 받아 앱이 읽는 정적 JSON으로 굽는다.

    python scripts/fetch_rates.py --key <인증키>

## 왜 이렇게 하나

은행 API는 **키 하나당 하루 1,000회**다. 앱이 직접 부르면 사용자 전원이
그 1,000회를 나눠 쓰게 되고, 하루 사용자가 1,000명만 넘어도 늦게 연
사람은 환율을 못 본다. 키를 APK에 심으면 뜯길 수도 있다.

그래서 하루 한 번 여기서 받아 `assets/data/rates.json`에 넣는다. 호출은
하루 1회로 고정되고, 사용자가 몇 명이든 상관없다.

## 주의

- 데이터는 **영업일 11시 전후** 갱신. 주말·공휴일에는 빈 배열이 온다.
  그래서 오늘부터 최대 5일 거슬러 올라가며 마지막 고시를 찾는다.
- 엔·루피아는 100단위로 고시된다(`JPY(100)`). 그대로 쓰면 계산이 100배
  틀리므로 여기서 1단위로 환산해 넣는다.
- 인증키는 개인정보 보유기간 2년이 지나면 파기된다. RESULT 3이 오면
  키가 죽은 것이니 koreaexim.go.kr에서 재발급받는다.
- 도메인은 2026-04-30에 `www` → `oapi`로 바뀌었다. 옛 주소는 죽었다.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import ssl
import time
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

# 고시일은 한국 영업일 기준이다. UTC 로 계산하면 하루 앞을 찾는다.
KST = timezone(timedelta(hours=9))


def today_kst() -> date:
    return datetime.now(KST).date()

# 윈도우 콘솔은 기본이 cp949라 한글·em대시에서 죽는다. 출력만 UTF-8로 돌린다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

# 앱에 노출할 통화. 트래빗이 지원하는 언어권 + 방한객 상위 국가 기준으로
# 골랐고, **은행 API가 실제로 주는 코드만** 넣었다.
# 은행 API에 없는 TWD(대만)·PHP(필리핀)·VND(베트남)·INR(인도)는 한국은행
# ECOS 「주요국 통화의 대원화환율」(731Y001, 영업일 일별)로 채운다(2026-10-02 광호님).
# 이 넷은 미국 달러를 거친 재정환율이다 — 여행자 가늠용으로 충분하다.
# ECOS 키가 없거나 실패하면 그 넷만 빠지고 나머지 16종은 그대로 나간다.
WANTED = {
    "USD": "US Dollar",
    "JPY(100)": "Japanese Yen",
    "CNH": "Chinese Yuan",
    "EUR": "Euro",
    "GBP": "British Pound",
    "HKD": "Hong Kong Dollar",
    "SGD": "Singapore Dollar",
    "THB": "Thai Baht",
    "MYR": "Malaysian Ringgit",
    "IDR(100)": "Indonesian Rupiah",
    "AUD": "Australian Dollar",
    "NZD": "New Zealand Dollar",
    "CAD": "Canadian Dollar",
    "CHF": "Swiss Franc",
    "SAR": "Saudi Riyal",
    "AED": "UAE Dirham",
}

# ECOS 항목코드 → (통화코드, 이름, 고시 단위). 731Y001 항목표는 StatisticItemList 로 확인(2026-10-02).
ECOS = {
    "0000031": ("TWD", "Taiwan Dollar", 1),
    "0000034": ("PHP", "Philippine Peso", 1),
    "0000035": ("VND", "Vietnamese Dong", 100),
    "0000037": ("INR", "Indian Rupee", 1),
}
ECOS_API = "https://ecos.bok.or.kr/api/StatisticSearch"
# 시세판에 놓는 순서 — 방한객이 실제로 들고 오는 순서. 여기 없는 코드는 뒤에 붙는다.
ORDER = ["USD", "JPY", "CNH", "TWD", "HKD", "EUR", "GBP", "SGD", "THB", "PHP", "VND",
         "MYR", "IDR", "AUD", "NZD", "CAD", "CHF", "INR", "SAR", "AED"]

OUT = os.path.join("assets", "data", "rates.json")


def fetch_ecos(key: str) -> tuple[list, str | None]:
    """ECOS 에서 넷을 받는다. 최근 10일 중 마지막 고시값. 실패하면 빈 목록."""
    end = today_kst()
    start = end - timedelta(days=10)
    out, latest = [], None
    for item, (code, name, unit) in ECOS.items():
        url = (f"{ECOS_API}/{key}/json/kr/1/10/731Y001/D/"
               f"{start:%Y%m%d}/{end:%Y%m%d}/{item}")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                j = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  [ECOS] {code} 받기 실패: {e}", file=sys.stderr)
            continue
        rows = (j.get("StatisticSearch") or {}).get("row") or []
        if not rows:
            # 키 오류·한도는 RESULT 로 온다(키 값은 찍지 않는다).
            print(f"  [ECOS] {code} 값 없음: {(j.get('RESULT') or {}).get('MESSAGE', '')}",
                  file=sys.stderr)
            continue
        last = max(rows, key=lambda x: x["TIME"])
        try:
            v = float(last["DATA_VALUE"]) / unit
        except (KeyError, ValueError):
            continue
        if v <= 0:
            continue
        out.append({"code": code, "name": name, "krw": round(v, 4)})
        latest = max(latest or "", last["TIME"])
    return out, latest




def _get(url: str, ctx=None, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(key: str, ymd: str):
    """한 날짜의 고시를 받아온다. 못 받으면 예외.

    깃허브 액션에서 이 서버가 **응답 없이 끊기는 일이 잦다**(2026-09-08 실패:
    urlopen error timed out). 예전엔 첫 시도에서 그대로 죽어 그날 환율이
    통째로 안 실렸다. 15초 한 번 → 30초씩 세 번으로 늘리고 사이를 띄운다.
    """
    url = f"{API}?authkey={key}&searchdate={ymd}&data=AP01"
    last = None
    for attempt in range(3):
        try:
            # 이 서버는 인증서 체인이 종종 불완전하다. 값 자체는 공개 정보라
            # 검증 실패 시에도 받아오되, 그 사실을 로그로 남긴다.
            try:
                return _get(url)
            except ssl.SSLError as e:
                print(f"  [경고] TLS 검증 실패({e}) — 검증 없이 재시도",
                      file=sys.stderr)
                return _get(url, ctx=ssl._create_unverified_context())
        except Exception as e:  # noqa: BLE001 — 시간초과·연결끊김 모두 여기로
            last = e
            print(f"  [경고] {ymd} 받기 실패 {attempt + 1}/3: {e}",
                  file=sys.stderr)
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None,
                    help="수출입은행 인증키 (환경변수 EXIM_KEY 도 가능)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    # 순서: --key > 환경변수 > .env.local
    # .env.local 을 마지막이 아니라 하나의 정식 경로로 두는 이유는,
    # 인증키를 명령줄에 적으면 셸 히스토리에 남기 때문이다.
    if not args.key:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from check_apis import _secret
            args.key = _secret("EXIM_KEY")
        except Exception:
            args.key = os.environ.get("EXIM_KEY")

    if not args.key:
        print("인증키가 없습니다. --key 또는 EXIM_KEY 환경변수로 주세요.",
              file=sys.stderr)
        print("발급: https://www.koreaexim.go.kr/ir/HPHKIR019M01",
              file=sys.stderr)
        return 2

    # 주말·공휴일·오전 고시 전에는 빈 배열이 온다. 최대 5일 거슬러 올라간다.
    for back in range(5):
        d = today_kst() - timedelta(days=back)
        ymd = d.strftime("%Y%m%d")
        rows = fetch(args.key, ymd)

        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            result = rows[0].get("result")
            if result == 3:
                print("인증코드 오류(result 3). 키가 파기됐을 수 있습니다 — "
                      "재발급이 필요합니다.", file=sys.stderr)
                return 3
            if result == 4:
                print("일일 1,000회 한도 마감(result 4). 내일 다시 받으세요.",
                      file=sys.stderr)
                return 4

        if not rows:
            print(f"  {ymd}: 고시 없음 (휴일이거나 11시 전) — 전날로")
            continue

        # API는 코드 알파벳순으로 준다. 그대로 쓰면 AED가 맨 앞이고 USD는
        # 화면 밖으로 밀린다. WANTED에 적은 순서 = 방한객이 실제로 들고 오는
        # 순서로 다시 세운다.
        by_unit = {e.get("cur_unit"): e for e in rows if isinstance(e, dict)}
        out = []
        for unit in WANTED:
            e = by_unit.get(unit)
            if e is None:
                continue
            raw = str(e.get("deal_bas_r", "")).replace(",", "")
            try:
                v = float(raw)
            except ValueError:
                continue
            if v <= 0:
                continue
            # 100단위 고시를 1단위로. 이걸 빼먹으면 계산이 100배 틀린다.
            if "(100)" in unit:
                v /= 100
            out.append({
                "code": unit.replace("(100)", ""),
                "name": WANTED[unit],
                "krw": round(v, 4),
            })

        if not out:
            print(f"  {ymd}: 원하는 통화가 하나도 없음 — 전날로")
            continue

        missing = set(WANTED) - set(by_unit)
        if missing:
            print(f"  [참고] 고시에 없는 통화: {', '.join(sorted(missing))}")

        payload = {
            "date": ymd,
            "source": "koreaexim",
            "note": "deal_bas_r (매매기준율). 100단위 통화는 1단위로 환산됨.",
            "rates": out,
        }
        ecos_key = os.environ.get("ECOS_KEY")
        if not ecos_key:
            try:
                from check_apis import _secret
                ecos_key = _secret("ECOS_KEY")
            except Exception:
                pass
        if ecos_key:
            extra, edate = fetch_ecos(ecos_key)
            if extra:
                have = {r["code"] for r in out}
                out.extend(r for r in extra if r["code"] not in have)
                payload["extra_source"] = "bok-ecos 731Y001 (재정환율)"
                payload["extra_date"] = edate
                try:
                    from check_apis import stamp_success
                    stamp_success("bok-ecos-fx")
                except Exception:
                    pass
                print(f"  ECOS {len(extra)}종 추가 (기준일 {edate})")
        else:
            print("  [참고] ECOS_KEY 없음 — TWD·PHP·VND·INR 은 빠진다", file=sys.stderr)
        rank = {c: i for i, c in enumerate(ORDER)}
        out.sort(key=lambda r: rank.get(r["code"], len(ORDER)))
        outdir = os.path.dirname(args.out)
        if outdir:
            os.makedirs(outdir, exist_ok=True)
        with io.open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        # 등록부에 "오늘 잘 썼다"고 도장을 찍는다. 이걸 빼먹으면
        # check_apis.py 의 미사용 검사가 장식이 된다.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from check_apis import stamp_success
            stamp_success("koreaexim-fx")
        except Exception as e:
            print(f"  [참고] 등록부 기록 실패: {e}", file=sys.stderr)

        print(f"OK  {args.out}  기준일 {ymd}  통화 {len(out)}종")
        return 0

    print("최근 5일 안에 고시를 찾지 못했습니다.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
