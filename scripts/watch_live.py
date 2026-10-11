#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""매일 자료가 멈췄는지 본다 — PC 가 꺼져 있어도 깃허브에서 돈다(2026-10-11 광호님).

    python scripts/watch_live.py            # 표 + 멈춘 게 있으면 종료 1
    python scripts/watch_live.py --md OUT   # 알림·이슈 본문(한국어)을 OUT 에

기준(파일·날짜 칸·허용 날수·최소 개수)은 scripts/watch_live.json — 손으로 고치지 않는다.
원본은 트래빗 앱 저장소의 운영 장부(docs/upkeep/ledger.yaml, kind: live)이고
`python scripts/check_upkeep.py --export-watch` 가 이 파일을 다시 쓴다. 장부 누락 검사가 둘이 다르면 잡는다.

왜 여기서 도나: 앱 저장소의 주간 점검은 PC 예약 작업이라 PC 가 꺼지면 같이 멈춘다.
PC 에서 굽는 K-pop 공연이 멈췄다면 PC 가 꺼져 있다는 뜻일 수도 있다 — 이 감시가 그 사각지대를 본다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "scripts", "watch_live.json")


def today_kst() -> dt.date:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).date()


def parse_date(v) -> dt.date | None:
    """20261008 · 2026-10-11T08:57 · 2026-10-10T23:58:30+00:00(→ 한국 날짜) · 2026-10-07."""
    s = str(v or "").strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})$", s)
    if m:  # 시간대가 붙은 시각은 한국 날짜로 바꾼다(UTC 23:58 은 한국 다음 날)
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (t.astimezone(dt.timezone(dt.timedelta(hours=9)))).date()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", s)
    if m:
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None


def dig(obj, dotted: str):
    for k in dotted.split("."):
        obj = obj.get(k) if isinstance(obj, dict) else None
    return obj


def check(today: dt.date) -> list[dict]:
    conf = json.load(open(CONF, encoding="utf-8"))
    out = []
    for w in conf["watch"]:
        row = {"id": w["id"], "name": w["name"], "max_days": w["max_days"], "why": "", "ok": True, "hint": w.get("hint", "")}
        dates = []
        for c in w["checks"]:
            try:
                doc = json.load(open(os.path.join(ROOT, c["file"]), encoding="utf-8"))
            except Exception as e:
                row["ok"], row["why"] = False, f"{c['file']} 를 못 읽음({type(e).__name__})"
                break
            d = parse_date(dig(doc, c["field"]))
            if d is None:
                row["ok"], row["why"] = False, f"{c['file']} {c['field']} 날짜를 못 읽음"
                break
            dates.append(d)
            if c.get("min"):
                key, n = c["min"]
                cnt = len(dig(doc, key) or [])
                if cnt < n:
                    row["ok"], row["why"] = False, f"{c['file']} {key} {cnt}개(최소 {n}) — 날짜는 새로운데 내용이 비었다"
        if row["ok"] and dates:
            last = min(dates)
            age = (today - last).days
            row["last"], row["age"] = last.isoformat(), age
            if age > w["max_days"]:
                row["ok"], row["why"] = False, f"{age}일째 그대로(마지막 {last}, 허용 {w['max_days']}일)"
            else:
                row["why"] = f"{age}일 전"
        out.append(row)
    return out


def main() -> int:
    today = parse_date(sys.argv[sys.argv.index("--today") + 1]) if "--today" in sys.argv else today_kst()  # 시험용
    rows = check(today)
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(f"{'정상' if r['ok'] else '멈춤'}  {r['name']} — {r['why']}")
    if "--md" in sys.argv:
        path = sys.argv[sys.argv.index("--md") + 1]
        L = [f"트래빗 매일 자료 감시 {today} (한국 시간) — 멈춘 것 {len(bad)}개", ""]
        for r in bad:
            L.append(f"· {r['name']}: {r['why']}")
            if r["hint"]:
                L.append(f"  (원인 찾을 곳: {r['hint']})")
        L += ["", "앱은 이 자료가 멈추면 그 칸을 조용히 감춰요. 해결될 때까지 매일 아침 다시 알려요.",
              "세션에서 '트래빗 매일 자료 멈춤 봐줘'라고 하시면 원인을 찾을게요."]
        open(path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
