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
import sys
import urllib.request
from datetime import date, timedelta

# 윈도우 콘솔은 기본이 cp949라 한글·em대시에서 죽는다. 출력만 UTF-8로 돌린다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

API = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

# 앱에 노출할 통화. 트래빗이 지원하는 언어권 + 방한객 상위 국가 기준으로
# 골랐고, **은행 API가 실제로 주는 코드만** 넣었다.
# 없어서 못 넣은 것: TWD(대만), VND(베트남), PHP(필리핀), INR(인도).
#   → zh_tw·vi 사용자에게는 이 화면이 반쪽이다. 대안 출처를 찾기 전까지는
#     직접 입력 계산기로 쓰게 두는 게 낫다(엉뚱한 환율을 보여주는 것보다).
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

OUT = os.path.join("assets", "data", "rates.json")


def fetch(key: str, ymd: str):
    url = f"{API}?authkey={key}&searchdate={ymd}&data=AP01"
    # 이 서버는 인증서 체인이 종종 불완전하다. 값 자체는 공개 정보라
    # 검증 실패 시에도 받아오되, 그 사실을 로그로 남긴다.
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except ssl.SSLError as e:
        print(f"  [경고] TLS 검증 실패({e}) — 검증 없이 재시도", file=sys.stderr)
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(url, timeout=15, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))


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
        d = date.today() - timedelta(days=back)
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
