#!/usr/bin/env python3
"""One-off migration: replace the original single-poll-per-day backfill
(everything before the tracker's first real daily snapshot) with the new
rolling-window-smoothed history. Real snapshot rows (date >= CUTOFF) are
left untouched. Not part of the daily scraper -- run manually, once.
"""
import json
import sys

from scraper import DATA_FILE, SOURCE_LABEL, fetch_html, find_polls_table, parse_poll_history, URL

CUTOFF = "2026-07-20"  # first date scraper.py ran as a real daily snapshot


def main() -> int:
    html = fetch_html(URL)
    table, _, candidate_id_to_name = find_polls_table(html)
    history = parse_poll_history(table, candidate_id_to_name)

    with DATA_FILE.open(encoding="utf-8") as f:
        existing = json.load(f)

    kept = [r for r in existing if r["date"] >= CUTOFF]
    replaced = [
        {"date": d, "candidate": name, "pct": pct, "source": SOURCE_LABEL}
        for d, name, pct in history
        if d < CUTOFF
    ]
    merged = kept + replaced
    merged.sort(key=lambda r: (r["date"], -r["pct"]))

    removed_count = len(existing) - len(kept)
    print(f"Removed {removed_count} old backfill rows (date < {CUTOFF}), "
          f"kept {len(kept)} real snapshot rows, added {len(replaced)} smoothed rows. "
          f"Total: {len(merged)}.")

    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
