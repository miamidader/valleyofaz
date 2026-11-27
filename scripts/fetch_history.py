#!/usr/bin/env python3
"""Write data/history.json: today's Arizona-related 'On this day' entries from Wikipedia.

Standard library only. If the lookup fails or nothing Arizona-related happened today,
it writes an empty list and the page simply hides the History card.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "history.json"
CURATED = DATA / "history_curated.json"
URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{mm}/{dd}"
UA = "valleyofaz-reader/1.0 (news feed for the Phoenix area)"
MAX_ITEMS = 8

AZ = re.compile(
    r"\bArizona\b|\bPhoenix(?! Park| Islands| Rising)\b|\bTempe\b|\bScottsdale\b|\bTucson\b|\bFlagstaff\b"
    r"|\bGrand Canyon\b|\bHoover Dam\b|\bDiamondbacks\b|\bSun Devils\b|\bNavajo Nation\b|, AZ\b"
)
KIND_ORDER = {"event": 0, "birth": 1, "death": 2}


def fetch(mm, dd):
    req = urllib.request.Request(URL.format(mm=mm, dd=dd), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def clip(text, n=220):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def parse(data):
    """Pick the entries that mention Arizona places, keeping the page they are about."""
    found = []
    for key, kind in (("events", "event"), ("births", "birth"), ("deaths", "death")):
        for e in data.get(key, []):
            pages = e.get("pages") or []
            blob = " ".join([e.get("text", "")] + [p.get("description", "") + " " + p.get("extract", "") for p in pages])
            if not AZ.search(blob):
                continue
            page = next((p for p in pages if AZ.search(p.get("description", "") + " " + p.get("extract", ""))), pages[0] if pages else {})
            url = ((page.get("content_urls") or {}).get("desktop") or {}).get("page", "")
            if not url.startswith("https://"):
                url = ""
            found.append({
                "kind": kind,
                "year": e.get("year"),
                "text": clip(e.get("text", "")),
                "title": page.get("normalizedtitle") or page.get("title", ""),
                "url": url,
            })
    found.sort(key=lambda x: (KIND_ORDER[x["kind"]], -(x["year"] or 0)))
    return found[:MAX_ITEMS]


def curated_for(mmdd):
    """Hand-checked entries (each confirmed against two sources) for this date."""
    try:
        rows = json.loads(CURATED.read_text())
    except (OSError, ValueError):
        return []
    return [{"kind": r.get("kind", "event"), "year": r["year"], "text": r["text"], "title": r.get("source", ""), "url": r.get("url", "")}
            for r in rows if r.get("date") == mmdd]


def main():
    today = datetime.now(ZoneInfo("America/Phoenix"))
    mine = curated_for(f"{today:%m-%d}")
    items = []
    try:
        items = parse(fetch(f"{today:%m}", f"{today:%d}"))
    except Exception as exc:  # noqa: BLE001 - history is a bonus, never fail the run
        print("history lookup failed:", type(exc).__name__, str(exc)[:120])
    years = {m["year"] for m in mine}
    items = (mine + [i for i in items if i["year"] not in years])[:MAX_ITEMS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"date": f"{today:%m-%d}", "label": f"{today:%b} {today.day}", "items": items}, indent=1, ensure_ascii=False))
    print(f"{len(items)} history items for {today:%m-%d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
