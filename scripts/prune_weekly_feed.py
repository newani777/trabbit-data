#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「이번 주 한국」 지난 정보 정리 — 매일 bake 가 돌린다.

manifest 에서 두 가지를 내린다(카드 폴더도 지운다. 기록은 git 에 남는다).

1. **끝난 행사** — 회차의 `ends`("2026-10-04", 행사 마지막 날)가 한국 날짜로 지났으면.
2. **4주 전 회차** — 이번 주·지난 3주가 아닌 것. 주는 `weekly.week`
   ("Week of September 21, 2026")의 월요일, 없으면 발행일이 든 주.

앱(1.0.17~)도 같은 규칙으로 숨기지만, 이 정리가 있어야 예전 버전 앱에서도 사라진다.
규칙 원본: trabbit_app lib/data/card_feed.dart `CardFeed.byWeek`,
운영 문서: trabbit_app docs/weekly_feed_publish.md (광호님 2026-09-21).

    python scripts/prune_weekly_feed.py          # 정리
    python scripts/prune_weekly_feed.py --dry    # 무엇이 내려갈지만
"""
import datetime
import io
import json
import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(HERE, "..", "weekly_feed")
PAST_WEEKS = 3
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def today_kst(now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now + datetime.timedelta(hours=9)).date()


def monday(d):
    return d - datetime.timedelta(days=d.weekday())


def week_of(issue):
    w = (issue.get("weekly") or {}).get("week", "")
    m = re.search(r"(\w+)\s+(\d{1,2}),\s*(\d{4})", w or "")
    if m and m.group(1).lower() in MONTHS:
        return monday(datetime.date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))))
    try:
        return monday(datetime.date.fromisoformat(issue.get("published", "")[:10]))
    except ValueError:
        return datetime.date(2000, 1, 3)


def ends_of(issue):
    try:
        return datetime.date.fromisoformat(str(issue.get("ends", ""))[:10])
    except ValueError:
        return None


def state(issue, today):
    """'예약' | '이번 주' | '지난 호' | '끝난 행사' | '4주 지남'"""
    this_mon = monday(today)
    e = ends_of(issue)
    if e and e < today:
        return "끝난 행사"
    w = week_of(issue)
    if w > this_mon:
        return "예약"
    if w == this_mon:
        return "이번 주"
    if w >= this_mon - datetime.timedelta(days=7 * PAST_WEEKS):
        return "지난 호"
    return "4주 지남"


GONE = ("끝난 행사", "4주 지남")


def prune(feed_dir=FEED, dry=False, today=None):
    today = today or today_kst()
    mpath = os.path.join(feed_dir, "manifest.json")
    if not os.path.exists(mpath):
        return []
    m = json.load(io.open(mpath, encoding="utf-8"))
    keep, log = [], []
    for i in m.get("issues", []):
        s = state(i, today)
        if s in GONE:
            log.append(f"내림  No.{i.get('no')} {i['id']} ({s}"
                       + (f", {i['ends']} 끝" if s == "끝난 행사" else "") + ")")
            if not dry:
                shutil.rmtree(os.path.join(feed_dir, i["id"]), ignore_errors=True)
        else:
            keep.append(i)
    if log and not dry:
        m["issues"] = keep
        m["updated"] = today.isoformat()
        io.open(mpath, "w", encoding="utf-8", newline="\n").write(
            json.dumps(m, ensure_ascii=False, indent=2) + "\n")
    return log


if __name__ == "__main__":
    log = prune(dry="--dry" in sys.argv)
    print("\n".join(log) if log else "내릴 것 없음")
