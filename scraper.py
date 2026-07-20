#!/usr/bin/env python3
"""Scrape 270toWin's 2028 Democratic nomination poll averages into data.json.

Appends one row per candidate per day to DATA_FILE, keyed on
(date, candidate, source) so re-running on the same day is a no-op.
Exits non-zero with a clear message on any network or page-structure
failure, and never writes partial/empty data over a good history.

270toWin only ever shows the *current* poll average -- it doesn't publish
a history of past daily averages. To backfill a real trend instead of
starting from a single flat day, we also read every individual poll row
listed on the page (each has a real pollster and date) and, for each past
date, average that date's individual polls per candidate as a stand-in for
what the average likely was that day. That derived value is distinct from
270toWin's own (differently weighted) methodology, so treat early history
as an approximation -- it converges to the real thing once enough of our
own daily snapshots have accumulated.
"""
import datetime
import json
import statistics
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.270towin.com/2028-democratic-nomination/"
DATA_FILE = Path(__file__).parent / "data.json"
SOURCE_LABEL = "270toWin avg"
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "dem-primary-tracker/1.0 "
    "(+https://github.com/; personal polling dashboard; contact via repo issues)"
)


class ScraperError(Exception):
    """Raised when the page can't be fetched or its structure doesn't match what we expect."""


def fetch_html(url: str) -> str:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScraperError(f"Failed to fetch {url}: {exc}") from exc
    if not resp.text or len(resp.text) < 1000:
        raise ScraperError(
            f"Fetched {url} but response body looks too small ({len(resp.text)} bytes) "
            "to be the real page."
        )
    return resp.text


def find_polls_table(html: str):
    soup = BeautifulSoup(html, "html.parser")

    table = soup.find("table", id="polls")
    if table is None:
        raise ScraperError(
            "Could not find <table id=\"polls\"> on the page. "
            "270toWin's page structure likely changed."
        )

    thead = table.find("thead")
    if thead is None:
        raise ScraperError("Found the polls table but it has no <thead>; page structure changed.")

    candidate_ths = thead.find_all("th", class_="can_name")
    if not candidate_ths:
        raise ScraperError(
            "Found the polls table's <thead> but no th.can_name candidate columns; "
            "page structure changed."
        )
    candidate_names = [th.get_text(strip=True) for th in candidate_ths]
    candidate_id_to_name = {
        th.get("candidate_id"): th.get_text(strip=True)
        for th in candidate_ths
        if th.get("candidate_id")
    }
    return table, candidate_names, candidate_id_to_name


def parse_poll_averages(table, candidate_names: list[str]) -> list[tuple[str, float]]:
    """Parse the poll_avg_row from 270toWin's polls table.

    Returns a list of (candidate_name, pct) tuples, skipping candidates
    with no current average ("-").
    """
    avg_row = table.find("tr", id="poll_avg_row")
    if avg_row is None:
        raise ScraperError(
            "Could not find <tr id=\"poll_avg_row\"> in the polls table; "
            "page structure changed."
        )

    all_tds = avg_row.find_all("td")
    # Layout: [0] = "Poll Averages" label (colspan over src/date/sample),
    # [1:-1] = one td per candidate in the same order as candidate_names,
    # [-1] = "Other" column. Values are text, either "NN.N%" or "-".
    candidate_tds = all_tds[1:-1]
    if len(candidate_tds) != len(candidate_names):
        raise ScraperError(
            f"Expected {len(candidate_names)} average cells to match {len(candidate_names)} "
            f"candidate columns, found {len(candidate_tds)}. Page structure changed."
        )

    results = []
    for name, td in zip(candidate_names, candidate_tds):
        text = td.get_text(strip=True)
        if text in ("", "-"):
            continue
        try:
            pct = float(text.rstrip("%"))
        except ValueError:
            raise ScraperError(f"Could not parse percentage for {name!r} from cell text {text!r}.")
        if not (0.0 <= pct <= 100.0):
            raise ScraperError(f"Parsed out-of-range percentage {pct} for {name!r}.")
        results.append((name, pct))

    if not results:
        raise ScraperError(
            "Parsed the averages row successfully but got zero candidates with data. "
            "Refusing to write an empty result."
        )

    return results


def parse_poll_history(table, candidate_id_to_name: dict) -> list[tuple[str, str, float]]:
    """Parse every individual poll row for its real (dated) results.

    Each poll_row has a real pollster and date, and one <td candidate_id="..">
    per candidate that has a value (candidates with no result in that poll
    simply have no candidate_id cell). Returns one row per (date, candidate),
    averaging across polls that share a date, as ("YYYY-MM-DD", name, pct).
    Malformed individual rows are skipped rather than failing the whole run --
    this is a backfill enrichment, not the critical path.
    """
    poll_rows = table.find_all("tr", class_=lambda c: c and "poll_row" in c.split())

    by_date_candidate: dict[tuple[str, str], list[float]] = {}
    for row in poll_rows:
        date_td = row.find("td", class_="poll_date")
        if date_td is None:
            continue
        try:
            date_iso = datetime.datetime.strptime(date_td.get_text(strip=True), "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue

        for td in row.find_all("td", attrs={"candidate_id": True}):
            name = candidate_id_to_name.get(td["candidate_id"])
            if name is None:
                continue
            text = td.get_text(strip=True)
            try:
                pct = float(text.rstrip("%"))
            except ValueError:
                continue
            if not (0.0 <= pct <= 100.0):
                continue
            by_date_candidate.setdefault((date_iso, name), []).append(pct)

    return [
        (date_iso, name, round(statistics.mean(values), 1))
        for (date_iso, name), values in by_date_candidate.items()
    ]


def load_existing(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        raise ScraperError(f"Could not read/parse existing {path}: {exc}") from exc
    if not isinstance(data, list):
        raise ScraperError(f"Expected {path} to contain a JSON list, got {type(data).__name__}.")
    return data


def merge_rows(existing: list[dict], new_rows: list[dict]) -> tuple[list[dict], int]:
    existing_keys = {(r.get("date"), r.get("candidate"), r.get("source")) for r in existing}
    added = 0
    merged = list(existing)
    for row in new_rows:
        key = (row["date"], row["candidate"], row["source"])
        if key in existing_keys:
            continue
        merged.append(row)
        existing_keys.add(key)
        added += 1
    merged.sort(key=lambda r: (r["date"], -r["pct"]))
    return merged, added


def main() -> int:
    try:
        html = fetch_html(URL)
        table, candidate_names, candidate_id_to_name = find_polls_table(html)
        averages = parse_poll_averages(table, candidate_names)

        today = datetime.date.today().isoformat()
        new_rows = [
            {"date": today, "candidate": name, "pct": pct, "source": SOURCE_LABEL}
            for name, pct in averages
        ]

        try:
            history = parse_poll_history(table, candidate_id_to_name)
        except Exception as exc:  # backfill is best-effort, never blocks the primary snapshot
            print(f"WARNING: history backfill failed, continuing without it: {exc}", file=sys.stderr)
            history = []
        # today's poll_avg_row value is authoritative; don't let a same-day
        # derived average override it.
        new_rows += [
            {"date": date_iso, "candidate": name, "pct": pct, "source": SOURCE_LABEL}
            for date_iso, name, pct in history
            if date_iso != today
        ]

        existing = load_existing(DATA_FILE)
        merged, added = merge_rows(existing, new_rows)

        with DATA_FILE.open("w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")

        print(f"OK: parsed {len(averages)} candidates for {today} plus {len(history)} "
              f"backfilled (date, candidate) history points, added {added} new row(s), "
              f"{len(merged)} total rows in {DATA_FILE.name}.")
        return 0

    except ScraperError as exc:
        print(f"SCRAPE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
