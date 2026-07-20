# 2028 Democratic Primary Polling Tracker

A static dashboard tracking national polling averages for the 2028 Democratic
presidential primary. A daily GitHub Action scrapes [270toWin](https://www.270towin.com/2028-democratic-nomination/),
appends the result to `data.json`, and commits it — the page itself is pure
static HTML/JS that reads that file, so there's no backend to run or host.

## How it works

- **`scraper.py`** — fetches the 270toWin nomination page, parses the "Poll
  Averages" row of `<table id="polls">`, and appends one row per candidate
  per day to `data.json`. Re-running on the same day is a no-op (deduped on
  `date` + `candidate` + `source`). Exits non-zero with a clear message on
  the stderr if the fetch fails or the page's HTML structure has changed —
  it never writes partial or empty data over good history.
- **`data.json`** — the accumulating history, shape:
  ```json
  [{ "date": "2026-07-20", "candidate": "Harris", "pct": 28.4, "source": "270toWin avg" }]
  ```
- **`dem_primary_tracker.html`** — fetches `data.json`, charts the top 8
  candidates by current average as a Chart.js line chart, and lists every
  candidate in a leaderboard table below it.
- **`.github/workflows/update-polls.yml`** — runs `scraper.py` daily at
  13:00 UTC (and on manual dispatch), commits `data.json` if it changed, and
  pushes. If the scrape fails, the workflow run fails loudly instead of
  silently corrupting the data file.

## Source & scraping policy

270toWin's `robots.txt` blocks a named list of AI crawlers (ClaudeBot,
GPTBot, etc.) site-wide but allows generic user agents (`Allow: /`) with a
`Content-Signal: ai-train=no, use=reference` policy, and the polling page
itself isn't in their disallowed-paths list. `scraper.py` identifies itself
with an honest, non-spoofed `User-Agent` and runs once a day. RealClearPolling
was evaluated as a fallback source but sits behind DataDome bot-detection
(CAPTCHA challenge) and was dropped rather than worked around.

If 270toWin's markup changes, the scraper will fail loudly (see "Error
handling" below) rather than silently ingesting garbage — the parser will
need a matching update at that point.

## Local development

```bash
pip install -r requirements.txt
python scraper.py            # updates data.json
python -m http.server 8000   # then open http://localhost:8000/dem_primary_tracker.html
```

`dem_primary_tracker.html` fetches `data.json` via `fetch()`, which most
browsers block on a bare `file://` URL — serve the folder over HTTP locally
as shown above.

## Deploying with GitHub Pages

1. Push this repo to GitHub.
2. In the repo, go to **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **Deploy from a branch**.
4. Pick the `main` branch and `/ (root)` folder, then **Save**.
5. GitHub will publish the site at `https://<username>.github.io/<repo>/`.
   `index.html` redirects to `dem_primary_tracker.html`, so the root URL
   works directly.
6. Every day the Action commits a fresh `data.json`; Pages picks it up
   automatically on the next deploy (typically within a minute or two of
   the push), no redeploy step needed.

## Error handling

`scraper.py` treats these as hard failures (non-zero exit, clear stderr
message, `data.json` left untouched):

- The request fails or times out.
- The response body is implausibly small.
- `<table id="polls">`, its `<thead>`, or `<tr id="poll_avg_row">` is missing.
- The number of average cells doesn't match the number of candidate columns.
- A percentage cell can't be parsed, or parses outside 0–100.
- Zero candidates end up with usable data.

In GitHub Actions this surfaces as a failed, red workflow run with the
reason in the logs — check the **Actions** tab if the site stops updating.
