#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K-pop 공연(KOPIS)만 광호님 PC 에서 굽는다 — Windows 예약 작업 「Trabbit KOPIS bake」가 매일 부른다(2026-10-10).

왜: GitHub Actions(해외 서버)에서 부르면 KOPIS 가 1건만 주고 끝난다(9/18 뒤로 3주 멈춤). 같은 키로 한국 PC 에서 부르면 정상.
     해외 IP 를 막는 것으로 보인다. Actions 쪽 단계는 그대로 두되(실패해도 어제 파일을 지킨다) 실제 갱신은 여기서 한다.

- 인증키는 D:/app/trabbit_app/.secrets/공연예술통합전산망 api.txt 에서 읽는다. 로그에 찍지 않는다.
- 순서: git pull --rebase → fetch_kpop_shows.py → 바뀌었으면 커밋·푸시. 기록은 C:/jtmp/trabbit_kpop_bake.log.
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEYFILE = Path("D:/app/trabbit_app/.secrets/공연예술통합전산망 api.txt")
LOG = Path("C:/jtmp/trabbit_kpop_bake.log")


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M} {msg}\n")


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8")


def main():
    m = re.search(r"\b([0-9a-f]{32})\b", KEYFILE.read_text(encoding="utf-8"))
    if not m:
        log("키를 못 찾음")
        return 1
    key = m.group(1)
    r = git("pull", "--rebase", "-q")
    if r.returncode:
        log("pull 실패: " + (r.stderr or "").strip()[:200])
        return 1
    p = subprocess.run([sys.executable, "-X", "utf8", "scripts/fetch_kpop_shows.py", "--key", key, "--out", "kpop_shows.json"],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    out = ((p.stdout or "") + (p.stderr or "")).replace(key, "KEY").strip().splitlines()
    log(f"fetch rc={p.returncode} " + (out[-1] if out else ""))
    if p.returncode:
        return 1
    git("add", "kpop_shows.json", "scripts/venue_scale.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("바뀐 것 없음")
        return 0
    c = git("commit", "-q", "-m", f"kpop_shows: 로컬 굽기 {datetime.date.today():%Y-%m-%d}")
    if c.returncode:
        log("commit 실패: " + (c.stderr or "").strip()[:200])
        return 1
    for _ in range(2):
        ps = git("push", "-q")
        if ps.returncode == 0:
            log("푸시 완료")
            return 0
        git("pull", "--rebase", "-q")
    log("push 실패: " + (ps.stderr or "").strip()[:200])
    return 1


if __name__ == "__main__":
    sys.exit(main())
