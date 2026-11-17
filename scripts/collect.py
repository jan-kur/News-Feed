#!/usr/bin/env python3
"""Collect raw items for the daily AI news feed.

Reads config/sources.yaml, fetches every source, drops items already listed in
data/seen.json, and writes data/raw/YYYY-MM-DD.json. Each source is isolated:
one failing source is recorded in `sources_status` and never stops the run.

Usage: python scripts/collect.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sources.yaml"
RAW_DIR = ROOT / "data" / "raw"
SEEN_FILE = ROOT / "data" / "seen.json"

UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
SEEN_KEEP_DAYS = 45

session = requests.Session()
session.headers.update({"User-Agent": UA_BROWSER, "Accept-Language": "en-US,en;q=0.9"})


# ---------------------------------------------------------------- helpers

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(d: dt.datetime | None) -> str | None:
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def parse_date(value) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    if isinstance(value, time.struct_time):
        return dt.datetime(*value[:6], tzinfo=dt.timezone.utc)
    s = str(value).strip()
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</pre>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def clip(s: str, max_chars: int) -> str:
    if s and len(s) > max_chars:
        return s[:max_chars].rsplit(" ", 1)[0] + " [...truncated]"
    return s or ""


def get(url: str, **kw) -> requests.Response:
    kw.setdefault("timeout", 30)
    r = session.get(url, **kw)
    r.raise_for_status()
    return r


def fetch_article(url: str, max_chars: int) -> tuple[str, dt.datetime | None, str]:
    """Return (main text, published date, title) for an article URL using trafilatura."""
    import trafilatura

    r = get(url)
    text = trafilatura.extract(r.text, url=url, include_comments=False, favor_precision=True) or ""
    published, title = None, ""
    try:
        meta = trafilatura.extract_metadata(r.text, default_url=url)
        if meta and meta.date:
            published = parse_date(meta.date)
        if meta and meta.title:
            title = meta.title.strip()
    except Exception:
        pass
    if published is None:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', r.text) or re.search(
            r'property="article:published_time"\s+content="([^"]+)"', r.text
        )
        if m:
            published = parse_date(m.group(1))
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S)
        title = html.unescape(m.group(1)).strip() if m else url
    return clip(text, max_chars), published, title


class Status:
    def __init__(self):
        self.data: dict[str, dict] = {}

    def ok(self, name, count, note=None):
        self.data[name] = {"ok": True, "count": count, **({"note": note} if note else {})}
        print(f"[ok]   {name}: {count} items" + (f" ({note})" if note else ""))

    def fail(self, name, error):
        self.data[name] = {"ok": False, "count": 0, "error": str(error)[:500]}
        print(f"[FAIL] {name}: {error}", file=sys.stderr)


# ---------------------------------------------------------------- YouTube

def resolve_channel_id(handle: str) -> str:
    r = get(f"https://www.youtube.com/{handle.lstrip('/')}", cookies={"CONSENT": "YES+1"})
    for pat in (r'"externalId":"(UC[\w-]{22})"', r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
                r'"channelId":"(UC[\w-]{22})"', r'channel_id=(UC[\w-]{22})'):
        m = re.search(pat, r.text)
        if m:
            return m.group(1)
    raise RuntimeError(f"could not resolve channel id for {handle}")


class TranscriptFetcher:
    """Tries youtube-transcript-api, then yt-dlp. Remembers when YouTube blocks us."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars
        self.api_blocked = False
        self.ytdlp_blocked = False
        self.errors: list[str] = []

    def get(self, video_id: str) -> tuple[str | None, str]:
        """Return (transcript text or None, method/reason)."""
        if not self.api_blocked:
            text, reason = self._api(video_id)
            if text:
                return text, "youtube-transcript-api"
            if reason == "blocked":
                self.api_blocked = True
            elif reason == "none":
                return None, "no captions on this video"
        if not self.ytdlp_blocked:
            text, reason = self._ytdlp(video_id)
            if text:
                return text, "yt-dlp"
            if reason == "blocked":
                self.ytdlp_blocked = True
        return None, "blocked by YouTube" if (self.api_blocked and self.ytdlp_blocked) else "transcript fetch failed"

    def _api(self, video_id):
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api import _errors as E

            try:
                fetched = YouTubeTranscriptApi().fetch(video_id, languages=("en", "en-US", "en-GB"))
            except (E.NoTranscriptFound, E.TranscriptsDisabled):
                return None, "none"
            text = " ".join(s.text for s in fetched.snippets)
            return clip(re.sub(r"\s+", " ", text), self.max_chars), "ok"
        except Exception as e:  # RequestBlocked, IpBlocked, PoTokenRequired, ...
            name = type(e).__name__
            self.errors.append(f"youtube-transcript-api {video_id}: {name}: {str(e)[:200]}")
            blocked = name in {"RequestBlocked", "IpBlocked", "PoTokenRequired", "YouTubeRequestFailed",
                               "FailedToCreateConsentCookie", "VideoUnplayable"}
            return None, "blocked" if blocked else "error"

    def _ytdlp(self, video_id):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--write-subs", "--write-auto-subs",
                   "--sub-langs", "en.*,en", "--sub-format", "json3", "-o", f"{tmp}/%(id)s.%(ext)s",
                   "--no-warnings", "--quiet", f"https://www.youtube.com/watch?v={video_id}"]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            except Exception as e:
                self.errors.append(f"yt-dlp {video_id}: {e}")
                return None, "error"
            files = sorted(Path(tmp).glob("*.json3"), key=lambda f: ("orig" in f.name, f.name))
            if not files:
                err = (p.stderr or p.stdout or "no subtitle file").strip()[:300]
                self.errors.append(f"yt-dlp {video_id}: {err}")
                blocked = any(k in err.lower() for k in ("sign in", "bot", "429", "403", "blocked"))
                return None, "blocked" if blocked else "error"
            data = json.loads(files[0].read_text())
            parts = []
            for ev in data.get("events", []):
                for seg in ev.get("segs") or []:
                    parts.append(seg.get("utf8", ""))
            text = re.sub(r"\s+", " ", "".join(parts)).strip()
            return (clip(text, self.max_chars), "ok") if text else (None, "error")


def collect_youtube(cfg, since, seen, status: Status) -> list[dict]:
    yt = cfg.get("youtube") or {}
    fetcher = TranscriptFetcher(int(yt.get("transcript_max_chars", 80000)))
    items = []
    for ch in yt.get("channels") or []:
        name = f"YouTube: {ch['name']}"
        try:
            cid = ch.get("channel_id") or resolve_channel_id(ch["handle"])
            feed = feedparser.parse(get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}").content)
            if feed.bozo and not feed.entries:
                raise RuntimeError(f"bad feed: {feed.bozo_exception}")
            count = 0
            for e in feed.entries:
                vid = e.get("yt_videoid")
                published = parse_date(e.get("published_parsed"))
                item_id = f"youtube:{vid}"
                if not vid or item_id in seen or not published or published < since:
                    continue
                link = e.get("link", f"https://www.youtube.com/watch?v={vid}")
                if "/shorts/" in link and yt.get("skip_shorts", True):
                    continue
                description = e.get("summary") or e.get("media_description") or ""
                transcript, method = fetcher.get(vid)
                items.append({
                    "id": item_id, "section": "youtube", "source": ch["name"], "title": e.get("title", ""),
                    "url": link, "published": iso(published),
                    "description": clip(html_to_text(description), 5000),
                    "transcript": transcript, "transcript_status": method if transcript else f"no transcript ({method})",
                    "views": int(e["media_statistics"]["views"]) if e.get("media_statistics", {}).get("views") else None,
                })
                count += 1
            status.ok(name, count, None if ch.get("channel_id") else f"resolved channel_id {cid}")
        except Exception as e:
            status.fail(name, e)
    with_t = sum(1 for i in items if i["transcript"])
    note = f"{with_t}/{len(items)} videos with transcript"
    if fetcher.errors:
        note += "; errors: " + " | ".join(fetcher.errors[:4])
    status.data["YouTube transcripts"] = {"ok": with_t == len(items), "count": with_t, "note": note}
    print(f"[info] YouTube transcripts: {note}")
    return items


# ---------------------------------------------------------------- Reddit

class Reddit:
    """Reddit access, in order: OAuth (if secrets set), public JSON, public RSS."""

    def __init__(self):
        self.ua = os.environ.get("REDDIT_USER_AGENT") or "linux:personal-news-feed:v1.0 (personal daily digest)"
        self.token = None
        self.mode = None
        cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
        if cid and secret:
            r = requests.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                              data={"grant_type": "client_credentials"}, headers={"User-Agent": self.ua}, timeout=30)
            r.raise_for_status()
            self.token = r.json()["access_token"]
            self.mode = "oauth"

    def _json(self, path, params):
        if self.token:
            r = requests.get(f"https://oauth.reddit.com{path}", params=params, timeout=30,
                             headers={"User-Agent": self.ua, "Authorization": f"bearer {self.token}"})
            r.raise_for_status()
            return r.json()
        last = None
        for host in ("https://www.reddit.com", "https://old.reddit.com", "https://api.reddit.com"):
            try:
                r = requests.get(f"{host}{path}.json" if host != "https://api.reddit.com" else f"{host}{path}",
                                 params={**params, "raw_json": 1}, headers={"User-Agent": self.ua}, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
        raise last

    def top_posts(self, sub, limit):
        data = self._json(f"/r/{sub}/top", {"t": "day", "limit": limit})
        self.mode = self.mode or "json"
        return [c["data"] for c in data["data"]["children"]]

    def comments(self, sub, post_id, limit):
        data = self._json(f"/r/{sub}/comments/{post_id}", {"sort": "top", "limit": limit, "depth": 2})
        out = []
        for c in data[1]["data"]["children"]:
            if c.get("kind") != "t1":
                continue
            d = c["data"]
            if d.get("stickied") or d.get("author") == "AutoModerator":
                continue
            replies = []
            rep = d.get("replies")
            if isinstance(rep, dict):
                for rc in rep["data"]["children"][:2]:
                    if rc.get("kind") == "t1" and rc["data"].get("score", 0) >= 3:
                        replies.append({"score": rc["data"].get("score"), "body": clip(rc["data"].get("body", ""), 1200)})
            out.append({"score": d.get("score"), "body": clip(d.get("body", ""), 2500), "replies": replies})
            if len(out) >= limit:
                break
        return out

    # RSS fallback: no scores, but works from more IPs.
    def rss_posts(self, sub, limit):
        r = requests.get(f"https://www.reddit.com/r/{sub}/top/.rss", params={"t": "day", "limit": limit},
                         headers={"User-Agent": UA_BROWSER}, timeout=30)
        r.raise_for_status()
        self.mode = "rss"
        return feedparser.parse(r.content).entries

    def rss_comments(self, link, limit):
        r = requests.get(link.rstrip("/") + "/.rss", params={"sort": "top", "limit": limit + 1},
                         headers={"User-Agent": UA_BROWSER}, timeout=30)
        r.raise_for_status()
        entries = feedparser.parse(r.content).entries[1:]  # first entry is the post itself
        return [{"score": None, "body": clip(html_to_text(e.get("summary", "")), 2500), "replies": []}
                for e in entries[:limit]]


def collect_reddit(cfg, since, seen, status: Status) -> list[dict]:
    rc = cfg.get("reddit") or {}
    subs = rc.get("subreddits") or []
    if not subs:
        return []
    per_sub, min_score, n_comments = rc.get("posts_per_subreddit", 10), rc.get("min_score", 20), rc.get("comments_per_post", 10)
    items = []
    try:
        reddit = Reddit()
    except Exception as e:
        status.fail("Reddit OAuth", e)
        reddit = Reddit.__new__(Reddit)
        reddit.ua, reddit.token, reddit.mode = os.environ.get("REDDIT_USER_AGENT") or "personal-news-feed/1.0", None, None
    for sub in subs:
        name = f"Reddit: r/{sub}"
        try:
            count = 0
            try:
                posts = reddit.top_posts(sub, 50)
                for p in sorted(posts, key=lambda p: p.get("score", 0), reverse=True):
                    item_id = f"reddit:{p['id']}"
                    created = parse_date(p.get("created_utc"))
                    if item_id in seen or p.get("stickied") or p.get("score", 0) < min_score:
                        continue
                    if created and created < since:
                        continue
                    try:
                        comments = reddit.comments(sub, p["id"], n_comments)
                    except Exception as e:
                        comments = [{"error": str(e)[:200]}]
                    items.append({
                        "id": item_id, "section": "reddit", "source": f"r/{sub}", "title": p.get("title", ""),
                        "url": f"https://www.reddit.com{p.get('permalink', '')}", "published": iso(created),
                        "score": p.get("score"), "num_comments": p.get("num_comments"),
                        "flair": p.get("link_flair_text"), "author": p.get("author"),
                        "linked_url": None if p.get("is_self") else p.get("url"),
                        "text": clip(p.get("selftext", ""), 12000), "comments": comments,
                    })
                    count += 1
                    if count >= per_sub:
                        break
                status.ok(name, count, f"via {reddit.mode}")
            except Exception as json_err:
                # Fallback to RSS (no scores; Reddit already ranks by top-of-day)
                entries = reddit.rss_posts(sub, per_sub + 5)
                for e in entries:
                    m = re.search(r"/comments/([a-z0-9]+)/", e.get("link", ""))
                    if not m:
                        continue
                    item_id = f"reddit:{m.group(1)}"
                    published = parse_date(e.get("published_parsed") or e.get("updated_parsed"))
                    if item_id in seen or (published and published < since):
                        continue
                    try:
                        comments = reddit.rss_comments(e["link"], n_comments)
                    except Exception as ce:
                        comments = [{"error": str(ce)[:200]}]
                    items.append({
                        "id": item_id, "section": "reddit", "source": f"r/{sub}", "title": e.get("title", ""),
                        "url": e.get("link"), "published": iso(published), "score": None,
                        "text": clip(html_to_text(e.get("summary", "")), 12000), "comments": comments,
                    })
                    count += 1
                    time.sleep(1.5)
                    if count >= per_sub:
                        break
                status.ok(name, count, f"via RSS fallback (JSON failed: {str(json_err)[:120]})")
        except Exception as e:
            status.fail(name, e)
    return items


# ---------------------------------------------------------------- GitHub releases

def collect_github_releases(cfg, since, seen, status: Status) -> list[dict]:
    items = []
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    for rel_cfg in cfg.get("github_releases") or []:
        repo = rel_cfg["repo"]
        name = f"GitHub releases: {repo}"
        try:
            rels = get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 30}, headers=headers).json()
            count = 0
            for r in rels:
                item_id = f"ghrelease:{repo}:{r['tag_name']}"
                published = parse_date(r.get("published_at"))
                if item_id in seen or r.get("draft") or not published or published < since:
                    continue
                if r.get("prerelease") and not rel_cfg.get("include_prereleases", False):
                    continue
                if re.search(r"(alpha|beta|rc|nightly)", r["tag_name"], re.I) and not rel_cfg.get("include_prereleases", False):
                    continue
                items.append({
                    "id": item_id, "section": "news", "source": rel_cfg.get("name", repo), "kind": "release",
                    "title": r.get("name") or r["tag_name"], "url": r.get("html_url"), "published": iso(published),
                    "text": clip(r.get("body") or "", 15000),
                })
                count += 1
            status.ok(name, count)
        except Exception as e:
            status.fail(name, e)
    return items


# ---------------------------------------------------------------- RSS / Atom feeds

def collect_rss(cfg, since, seen, status: Status) -> list[dict]:
    items = []
    for f in cfg.get("rss") or []:
        name = f"RSS: {f['name']}"
        try:
            parsed = feedparser.parse(get(f["url"]).content)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(f"unparseable feed: {parsed.bozo_exception}")
            count = 0
            for e in parsed.entries:
                link = e.get("link")
                item_id = f"rss:{e.get('id') or link}"
                published = parse_date(e.get("published_parsed") or e.get("updated_parsed"))
                if item_id in seen or not published or published < since:
                    continue
                content = ""
                if e.get("content"):
                    content = e["content"][0].get("value", "")
                content = html_to_text(content or e.get("summary", ""))
                if f.get("fetch_full_text") and len(content) < 1500 and link:
                    try:
                        full, _, _ = fetch_article(link, 20000)
                        content = full or content
                    except Exception:
                        pass
                items.append({
                    "id": item_id, "section": f.get("section", "news"), "source": f["name"],
                    "title": html.unescape(e.get("title", "")), "url": link, "published": iso(published),
                    "text": clip(content, 20000),
                })
                count += 1
            status.ok(name, count)
        except Exception as e:
            status.fail(name, e)
    return items


# ---------------------------------------------------------------- Sites without RSS

def collect_web_pages(cfg, since, seen, status: Status, date: str) -> list[dict]:
    """Scrape an index page for article links, then fetch new articles and keep the recent ones."""
    items = []
    for w in cfg.get("web_pages") or []:
        name = f"Web: {w['name']}"
        try:
            r = get(w["index"])
            pattern = re.compile(w["link_pattern"])
            exclude = set(w.get("exclude") or [])
            links = []
            for href in re.findall(r'href="([^"#?]+)"', r.text):
                path = urlparse(urljoin(w["index"], href)).path.rstrip("/")
                if pattern.search(path) and path not in exclude and path not in links:
                    links.append(path)
            base = f"{urlparse(w['index']).scheme}://{urlparse(w['index']).netloc}"
            source_seen_before = w["name"] in (seen.get("_sources") or [])
            count, checked = 0, 0
            for path in links[: w.get("max_links", 40)]:
                url = base + path
                item_id = f"web:{url}"
                if item_id in seen:
                    continue
                checked += 1
                try:
                    text, published, title = fetch_article(url, 20000)
                except Exception as e:
                    print(f"  skip {url}: {e}", file=sys.stderr)
                    continue
                # Index pages list old posts too. Remember old ones so they are not fetched again.
                # Allow 24h of slack: some sites date posts by day only.
                if published and published < since - dt.timedelta(hours=24):
                    seen[item_id] = date
                    continue
                if not published and not source_seen_before:
                    seen[item_id] = date  # first run and no date: treat as backlog
                    continue
                items.append({
                    "id": item_id, "section": "news", "source": w["name"], "title": title,
                    "url": url, "published": iso(published), "text": text,
                })
                count += 1
            status.ok(name, count, f"{len(links)} links on index, {checked} new checked")
        except Exception as e:
            status.fail(name, e)
    return items


# ---------------------------------------------------------------- Hacker News

def collect_hn(cfg, since_default, seen, status: Status) -> list[dict]:
    hc = cfg.get("hacker_news") or {}
    if not hc.get("queries"):
        return []
    since = now_utc() - dt.timedelta(hours=hc.get("lookback_hours", 48))
    min_points = hc.get("min_points", 80)
    hits: dict[str, dict] = {}
    try:
        for q in hc["queries"]:
            r = get("https://hn.algolia.com/api/v1/search", params={
                "query": q, "tags": "story", "hitsPerPage": 50,
                "numericFilters": f"created_at_i>{int(since.timestamp())},points>={min_points}",
            })
            for h in r.json().get("hits", []):
                hits.setdefault(h["objectID"], h)
        items = []
        for oid, h in sorted(hits.items(), key=lambda kv: kv[1].get("points", 0), reverse=True):
            item_id = f"hn:{oid}"
            if item_id in seen:
                continue
            url = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            text = html_to_text(h.get("story_text") or "")
            if hc.get("fetch_article_text", True) and h.get("url"):
                try:
                    text, _, _ = fetch_article(h["url"], 12000)
                except Exception as e:
                    text = f"(article fetch failed: {str(e)[:100]})"
            items.append({
                "id": item_id, "section": "news", "source": "Hacker News", "title": h.get("title", ""),
                "url": url, "hn_url": f"https://news.ycombinator.com/item?id={oid}",
                "published": iso(parse_date(h.get("created_at_i"))), "points": h.get("points"),
                "num_comments": h.get("num_comments"), "text": text,
            })
            if len(items) >= hc.get("max_items", 25):
                break
        status.ok("Hacker News", len(items), f"{len(hits)} matches >= {min_points} points")
        return items
    except Exception as e:
        status.fail("Hacker News", e)
        return []


# ---------------------------------------------------------------- main

def load_seen() -> dict:
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text() or "{}")
    return {}


def previous_collection_time() -> dt.datetime | None:
    files = sorted(RAW_DIR.glob("*.json"))
    for f in reversed(files):
        try:
            return parse_date(json.loads(f.read_text()).get("collected_at"))
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="date label for the raw file (default: today, Europe/Berlin)")
    ap.add_argument("--dry-run", action="store_true", help="do not write files")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text())
    try:
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo(cfg.get("timezone", "Europe/Berlin"))).date().isoformat()
    except Exception:
        today = now_utc().date().isoformat()
    date = args.date or today

    started = now_utc()
    min_h, max_h = cfg.get("lookback_hours", 24), cfg.get("max_lookback_hours", 72)
    prev = previous_collection_time()
    since = started - dt.timedelta(hours=min_h)
    if prev and prev < since:  # cover the gap if a day was skipped
        since = max(prev, started - dt.timedelta(hours=max_h))
    print(f"Collecting for {date}, items published since {iso(since)}")

    seen = load_seen()
    status = Status()

    items: list[dict] = []
    items += collect_youtube(cfg, since, seen, status)
    items += collect_reddit(cfg, since, seen, status)
    items += collect_github_releases(cfg, since, seen, status)
    items += collect_rss(cfg, since, seen, status)
    items += collect_web_pages(cfg, since, seen, status, date)
    items += collect_hn(cfg, since, seen, status)

    # De-duplicate by URL across sources (e.g. HN links to an Anthropic post we already have).
    by_url, unique = set(), []
    for it in items:
        key = (it.get("url") or "").rstrip("/").replace("http://", "https://")
        if key and key in by_url:
            continue
        by_url.add(key)
        unique.append(it)

    out = {
        "date": date,
        "collected_at": iso(started),
        "window_start": iso(since),
        "sources_status": status.data,
        "counts": {s: sum(1 for i in unique if i["section"] == s) for s in ("news", "youtube", "reddit")},
        "items": unique,
    }

    for it in unique:
        seen[it["id"]] = date
    srcs = set(seen.get("_sources", [])) | {w["name"] for w in cfg.get("web_pages") or []
                                            if status.data.get(f"Web: {w['name']}", {}).get("ok")}
    seen["_sources"] = sorted(srcs)
    cutoff = (started - dt.timedelta(days=SEEN_KEEP_DAYS)).date().isoformat()
    seen = {k: v for k, v in seen.items() if k == "_sources" or v >= cutoff}

    print(json.dumps(out["counts"]), f"total={len(unique)}")
    failed = [k for k, v in status.data.items() if not v.get("ok")]
    if failed:
        print("Sources with problems: " + ", ".join(failed))
    if args.dry_run:
        return
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{date}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    SEEN_FILE.write_text(json.dumps(seen, indent=0, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
