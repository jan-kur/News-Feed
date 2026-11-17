#!/usr/bin/env python3
"""Inspect a raw collection file without loading it all at once.

  python scripts/raw.py overview [data/raw/YYYY-MM-DD.json]   # one line per item + source status
  python scripts/raw.py show ID [ID ...] [--file PATH]        # full item(s), long text wrapped
  python scripts/raw.py latest                                # print path of newest raw file
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


def newest() -> Path:
    files = sorted(RAW_DIR.glob("????-??-??.json"))
    if not files:
        sys.exit("no raw files in data/raw")
    return files[-1]


def load(path: str | None) -> dict:
    p = Path(path) if path else newest()
    if not p.is_absolute():
        p = ROOT / p
    return json.loads(p.read_text())


def words(s) -> int:
    return len((s or "").split())


def overview(path=None):
    d = load(path)
    print(f"date={d['date']} collected_at={d['collected_at']} window_start={d['window_start']} counts={d['counts']}")
    print("\nSOURCES")
    for name, st in d["sources_status"].items():
        flag = "ok  " if st.get("ok") else "FAIL"
        extra = st.get("error") or st.get("note") or ""
        print(f"  {flag} {name}: {st.get('count', 0)} {extra[:300]}")
    print("\nITEMS (id | source | published | metrics | words | title)")
    for it in d["items"]:
        body_words = words(it.get("transcript")) or words(it.get("text")) or words(it.get("description"))
        metrics = []
        for k in ("score", "points", "num_comments", "views"):
            if it.get(k) is not None:
                metrics.append(f"{k}={it[k]}")
        if it["section"] == "youtube":
            metrics.append(it.get("transcript_status", ""))
        if it["section"] == "reddit":
            metrics.append(f"comments_fetched={len(it.get('comments') or [])}")
        print(f"- {it['id']} | {it['section']}/{it['source']} | {(it.get('published') or '')[:16]} | "
              f"{', '.join(metrics)} | {body_words}w | {it['title'][:120]}")


def show(ids, path=None):
    d = load(path)
    by_id = {it["id"]: it for it in d["items"]}
    for i in ids:
        it = by_id.get(i)
        if not it:
            print(f"!! {i} not found")
            continue
        print("=" * 80)
        for k, v in it.items():
            if k == "comments":
                print("comments:")
                for c in v:
                    print(textwrap.indent(textwrap.fill(f"[{c.get('score')}] {c.get('body') or c.get('error')}", 110), "  "))
                    for r in c.get("replies") or []:
                        print(textwrap.indent(textwrap.fill(f"[{r.get('score')}] {r.get('body')}", 106), "      > "))
            elif isinstance(v, str) and len(v) > 200:
                print(f"{k}:")
                for para in v.split("\n"):
                    print(textwrap.fill(para, 110) if para.strip() else "")
            else:
                print(f"{k}: {v}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("overview", "show", "latest"):
        print(__doc__)
        sys.exit(2)
    file_arg = None
    if "--file" in args:
        j = args.index("--file")
        file_arg = args[j + 1]
        del args[j:j + 2]
    if args[0] == "latest":
        print(newest().relative_to(ROOT))
    elif args[0] == "overview":
        overview(args[1] if len(args) > 1 else file_arg)
    else:
        show(args[1:], file_arg)
