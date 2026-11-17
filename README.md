# AI Daily: a personal daily AI news feed

A free, personal daily AI news feed, read on your phone as an installable web app.
It fits a 25-minute reading budget (plus 5 minutes you keep for your X List).

## How it works

1. **Collector** (GitHub Actions, `.github/workflows/collect.yml`, daily at
   03:40 UTC plus manual runs). `scripts/collect.py` reads `config/sources.yaml`,
   fetches YouTube, Reddit, GitHub releases, RSS feeds, Anthropic and Claude
   blog pages, and Hacker News. It skips anything listed in `data/seen.json`,
   writes `data/raw/YYYY-MM-DD.json` and commits both files. Its last step
   starts the Claude task through the task's API trigger.
2. **Summarizer** (Claude Code scheduled task on your Claude plan). It runs
   `prompts/daily-feed.md`: reads the raw file and `config/topics.md`, runs a
   few web searches for missed news, scores and summarizes items within the
   time budget, writes `data/feed/YYYY-MM-DD.json`, runs
   `scripts/finalize_feed.py` (which counts words at 230 wpm, enforces the
   25-minute cap, and updates `latest.json` and `index.json`), then pushes to
   `main`.
3. **Web app** (GitHub Pages, plain HTML/CSS/JS in the repo root). It loads
   `data/feed/latest.json`, shows "Today: N min" with minutes per section, the
   News, YouTube and Reddit sections, the X List button, a collapsed "More"
   list, and a day picker. Every push to `main` redeploys Pages. The app always
   asks the network first and reloads the feed when you reopen it, so a new
   day's feed appears without reinstalling.

Nothing here uses a paid API. GitHub Actions minutes are free for public
repos (and 2,000 min/month for private ones; one run takes 2 to 5 minutes).

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

1. **Make the repo public, or have GitHub Pro.** GitHub Pages is free only for
   public repos. Settings, General, Danger Zone, Change visibility. The repo
   holds only public news and your config, no secrets.
2. **Enable GitHub Pages.** Settings, Pages, Source: "Deploy from a branch",
   Branch: `main`, folder `/ (root)`, Save. The site will be at
   `https://<user>.github.io/News-Feed/`.
3. **Create the Claude scheduled task** at
   [claude.ai/code/routines](https://claude.ai/code/routines), New:
   - Prompt: `Follow the instructions in prompts/daily-feed.md exactly.`
   - Repository: this repo. Environment: Default.
   - Triggers: **API** (copy the URL and the token shown once) and a
     **Schedule**, daily at 07:30, as a backup. The backup run stops at once if
     today's feed already exists.
4. **Add repo secrets** (Settings, Secrets and variables, Actions, New
   repository secret):
   - `CLAUDE_TASK_FIRE_URL`: the API trigger URL, ending in `/fire`.
   - `CLAUDE_TASK_TOKEN`: the API trigger token.
   - Optional, only if Reddit approves an API app for you:
     `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`.
     Without them the collector uses Reddit's public JSON and RSS.
5. **Add the app to your home screen.**
   - iPhone: open the site in Safari, Share, "Add to Home Screen".
   - Android: open the site in Chrome, menu, "Add to Home screen" or "Install app".

## Running by hand

- Collector: Actions tab, "Collect raw items", Run workflow. Uncheck
  "Start the Claude feed task" to collect only.
- Locally: `pip install -r scripts/requirements.txt && python scripts/collect.py`.
- Inspect a raw file: `python scripts/raw.py overview`.
- Re-finalize a feed: `python scripts/finalize_feed.py data/feed/YYYY-MM-DD.json`.

## Known limits

- YouTube often blocks transcript requests from GitHub's servers. The
  collector tries `youtube-transcript-api`, then `yt-dlp`, and otherwise marks
  the video "no transcript". The summary then uses the title and description,
  and the app shows a "no transcript" badge.
- Reddit's free OAuth API needs Reddit's manual approval for new apps since
  November 2025. The public JSON and RSS endpoints work without it but may be
  rate-limited or blocked from GitHub's IP ranges. Failures show up under
  "Source issues" at the bottom of the feed.
- daily.dev has no free public feed (its API needs a token and is in beta), so
  it is not used.
- Anthropic and Claude blog pages are scraped from their index pages because
  they have no RSS feed. If their HTML changes, update `link_pattern`.
