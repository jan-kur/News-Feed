#!/usr/bin/env python3
"""Validate a feed file, compute reading time, enforce the time budget, and
update data/feed/latest.json and data/feed/index.json.

Usage: python scripts/finalize_feed.py data/feed/YYYY-MM-DD.json

Reading time counts the words of each item's title, summary and verdict at
`feed.words_per_minute` (config/sources.yaml). If the total is above
`feed.max_minutes`, the lowest-scored items move to the "more" list until it fits.
Items in "more" do not count toward the time.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FEED_DIR = ROOT / "data" / "feed"
SECTIONS = ("news", "youtube", "reddit")
REQUIRED = ("title", "source", "url", "summary")


def count_words(text: str) -> int:
    text = re.sub(r"```.*?```", lambda m: m.group(0).replace("`", " "), text or "", flags=re.S)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_`#>\[\]()|-]+", " ", text)
    return len(re.findall(r"\w[\w'.+/:-]*", text))


def item_words(it: dict) -> int:
    return count_words(it.get("title", "")) + count_words(it.get("summary", "")) + count_words(it.get("verdict", ""))


def main(path_arg: str) -> int:
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())
    fc = cfg.get("feed") or {}
    wpm = float(fc.get("words_per_minute", 230))
    max_min = float(fc.get("max_minutes", 25))

    path = Path(path_arg)
    if not path.is_absolute():
        path = ROOT / path
    feed = json.loads(path.read_text())
    errors = []

    date = feed.get("date") or path.stem
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        errors.append(f"bad date {date!r}")
    feed["date"] = date

    secs = feed.get("sections") or {}
    for s in list(secs):
        if s not in SECTIONS:
            errors.append(f"unknown section {s!r} (allowed: {SECTIONS})")
    for s in SECTIONS:
        secs.setdefault(s, {"items": []})
        if isinstance(secs[s], list):  # allow the short form "news": [ ... ]
            secs[s] = {"items": secs[s]}
        for i, it in enumerate(secs[s]["items"]):
            for k in REQUIRED:
                if not it.get(k):
                    errors.append(f"{s}[{i}] missing {k!r}")
            it.setdefault("score", 50)
            if s == "youtube" and not it.get("verdict"):
                errors.append(f"youtube[{i}] {it.get('title','')!r} missing 'verdict'")
    feed["sections"] = secs
    more = feed.setdefault("more", [])
    for i, m in enumerate(more):
        if not (m.get("title") and m.get("url")):
            errors.append(f"more[{i}] needs title and url")

    if errors:
        print("Feed has errors:\n  " + "\n  ".join(errors), file=sys.stderr)
        return 1

    # Enforce the budget: drop lowest-scored items into "more" until it fits.
    def total_words():
        return sum(item_words(it) for s in SECTIONS for it in secs[s]["items"])

    moved = []
    while total_words() / wpm > max_min:
        candidates = [(it.get("score", 50), s, it) for s in SECTIONS for it in secs[s]["items"]]
        score, s, it = min(candidates, key=lambda c: c[0])
        secs[s]["items"].remove(it)
        more.insert(0, {"title": it["title"], "url": it["url"], "source": it["source"], "section": s,
                        "reason": "cut to fit the time budget"})
        moved.append(f"{s}: {it['title']} (score {score})")

    total = 0
    for s in SECTIONS:
        items = secs[s]["items"]
        items.sort(key=lambda it: it.get("score", 50), reverse=True)
        w = 0
        for it in items:
            it["words"] = item_words(it)
            it["minutes"] = round(it["words"] / wpm, 1)
            w += it["words"]
        secs[s]["words"] = w
        secs[s]["minutes"] = round(w / wpm, 1)
        total += w

    feed["total_words"] = total
    feed["total_minutes"] = round(total / wpm, 1)
    feed["words_per_minute"] = wpm
    feed["max_minutes"] = max_min
    feed["x_list_url"] = fc.get("x_list_url")
    feed["x_list_minutes"] = fc.get("x_list_reserved_minutes", 5)
    feed["finalized_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    path.write_text(json.dumps(feed, indent=1, ensure_ascii=False) + "\n")

    # latest.json = newest dated feed
    dated = sorted(p for p in FEED_DIR.glob("????-??-??.json"))
    newest = json.loads(dated[-1].read_text()) if dated else feed
    (FEED_DIR / "latest.json").write_text(json.dumps(newest, indent=1, ensure_ascii=False) + "\n")

    index = []
    for p in reversed(dated):
        f = json.loads(p.read_text())
        index.append({
            "date": f.get("date", p.stem),
            "minutes": f.get("total_minutes"),
            "counts": {s: len((f.get("sections") or {}).get(s, {}).get("items", [])) for s in SECTIONS},
        })
    (FEED_DIR / "index.json").write_text(json.dumps({"days": index}, indent=1) + "\n")

    print(f"{date}: {feed['total_minutes']} min ({total} words) | "
          + ", ".join(f"{s} {secs[s]['minutes']} min/{len(secs[s]['items'])} items" for s in SECTIONS)
          + f" | more: {len(more)}")
    if moved:
        print("Over budget, moved to 'more':\n  " + "\n  ".join(moved))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
