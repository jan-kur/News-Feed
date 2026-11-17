# AI Daily: a personal daily AI news feed

A free, personal daily AI news feed, read on your phone as an installable web app.
It fits a 25-minute reading budget (plus 5 minutes you keep for your X List).

## How it works

One Claude Code scheduled task runs every morning on your Claude plan and
follows `prompts/daily-feed.md`:

1. **Collect.** It runs `scripts/collect.py`, which reads
   `config/sources.yaml`, fetches YouTube (RSS plus transcripts), Reddit,
   GitHub releases, RSS feeds, Anthropic and Claude blog pages, and Hacker
   News. It skips anything listed in `data/seen.json` and writes
   `data/raw/YYYY-MM-DD.json`.
2. **Summarize.** It reads the raw file and `config/topics.md`, runs a few web
   searches for missed news, scores and summarizes items within the time
   budget, and writes `data/feed/YYYY-MM-DD.json`. Then it runs
   `scripts/finalize_feed.py`, which counts words at 230 wpm, enforces the
   25-minute cap, and updates `latest.json` and `index.json`.
3. **Publish.** It pushes to `main`. GitHub Pages redeploys the web app (plain
   HTML/CSS/JS in the repo root) on every push. The app always asks the
   network first and reloads the feed when you reopen it, so the new day's
   feed appears on its own.

The app shows "Today: N min" with minutes per section, the News, YouTube and
Reddit sections, the X List button, a collapsed "More" list, and a day picker.

Nothing here uses a paid API. GitHub Pages is free for public repos. The
GitHub Actions workflow in `.github/workflows/collect.yml` is optional and has
no schedule; the task does not need it.

## Changing sources and topics

Edit the files in `config/` on GitHub (pencil icon) or locally, and commit to
`main`. The collector and the Claude task read them on every run.

- **YouTube channel**: add an entry under `youtube.channels` with `name` and
  `handle` (for example `"@anthropic-ai"`). `channel_id` is optional; without
  it the collector looks it up from the handle each run. To remove a channel,
  delete its entry.
- **Subreddit**: add or remove a name under `reddit.subreddits` (without `r/`).
- **RSS or Atom feed**: add `- name: ...` and `url: ...` under `rss`. Set
  `fetch_full_text: true` if the feed only has short excerpts.
- **Site without RSS**: add an entry under `web_pages` with the index page URL
  and a `link_pattern` regex matching article paths.
- **GitHub releases**: add `- repo: owner/name` under `github_releases`.
- **Hacker News**: edit `hacker_news.queries` (search terms) or `min_points`.
- **Topics**: edit `config/topics.md`. Order matters: higher means more
  important. The "Not interested" list filters content out.
- **Time budget**: `feed.max_minutes` and `feed.words_per_minute` in
  `config/sources.yaml`.
- **Summary style or rules**: edit `prompts/daily-feed.md`.

## Setup

1. **Repo public and GitHub Pages on.** Settings, Pages, Source "Deploy from a
   branch", Branch `main`, folder `/ (root)`. The site is
   `https://jan-kur.github.io/News-Feed/`.
2. **Cloud environment with network access to the sources.** At
   [claude.ai/code](https://claude.ai/code), open the environment menu, add an
   environment named `News-Feed`, and set Network access to **Full**. (Full is
   needed because Hacker News links to articles on any site. If you prefer
   Limited, allow at least: www.youtube.com, youtube.com, www.reddit.com,
   old.reddit.com, api.reddit.com, hn.algolia.com, news.ycombinator.com,
   openai.com, simonwillison.net, deepmind.google, api.github.com,
   www.anthropic.com, claude.com, and keep "Allow package managers" ticked.)
3. **Create the scheduled task** at
   [claude.ai/code/routines](https://claude.ai/code/routines), New:
   - Prompt: `Follow the instructions in prompts/daily-feed.md exactly.`
   - Repository: `jan-kur/News-Feed`. Environment: `News-Feed`.
   - Trigger: Schedule, daily at 05:00 Europe/Berlin.
4. **Add the app to your home screen.**
   - iPhone: open the site in Safari, Share, "Add to Home Screen".
   - Android: open the site in Chrome, menu, "Add to Home screen" or "Install app".

Reddit OAuth is optional and not set up: Reddit requires manual approval for
new API apps since November 2025. The collector uses Reddit's public JSON and
RSS endpoints. If you get an approved app, set `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET` and `REDDIT_USER_AGENT` as environment variables on the
`News-Feed` environment.

## Running by hand

- Whole pipeline: open the scheduled task and click "Run now".
- Collector only, locally: `pip install -r scripts/requirements.txt && python scripts/collect.py`.
- Inspect a raw file: `python scripts/raw.py overview`.
- Re-finalize a feed: `python scripts/finalize_feed.py data/feed/YYYY-MM-DD.json`.

## Known limits

- YouTube often blocks transcript requests from cloud servers. The
  collector tries `youtube-transcript-api`, then `yt-dlp`, and otherwise marks
  the video "no transcript". The summary then uses the title and description,
  and the app shows a "no transcript" badge.
- Reddit's free OAuth API needs Reddit's manual approval for new apps since
  November 2025. The public JSON and RSS endpoints work without it but may be
  rate-limited or blocked from cloud IP ranges. Failures show up under
  "Source issues" at the bottom of the feed.
- daily.dev has no free public feed (its API needs a token and is in beta), so
  it is not used.
- Anthropic and Claude blog pages are scraped from their index pages because
  they have no RSS feed. If their HTML changes, update `link_pattern`.
