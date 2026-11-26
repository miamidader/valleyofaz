#!/usr/bin/env python3
"""Pull every enabled feed in feeds.json into data/items.json.

Standard library only, so it runs anywhere (including GitHub Actions) with no installs.
A feed that fails is recorded and skipped; it never stops the run.
"""
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEEDS_PATH = ROOT / "feeds.json"
OUT_PATH = ROOT / "data" / "items.json"
KEEP_DAYS = 7
MAX_ITEMS = 800
UA = "Mozilla/5.0 (compatible; valleyofaz-reader/1.0)"

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def now():
    return datetime.now(timezone.utc)


def clean(text, limit=300):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def text_of(el, *names):
    for name in names:
        found = el.find(name)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def parse_feed(xml_bytes):
    """Return a list of dicts: title, link, summary, published (datetime or None)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        text = xml_bytes.decode("utf-8", errors="replace")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        text = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text)
        root = ET.fromstring(text.encode("utf-8"))
    items = []
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = ""
            for l in e.findall(ATOM + "link"):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href", "")
                    break
            items.append({
                "title": text_of(e, ATOM + "title"),
                "link": link,
                "summary": text_of(e, ATOM + "summary", ATOM + "content"),
                "published": parse_date(text_of(e, ATOM + "published", ATOM + "updated")),
            })
    else:
        for e in root.iter("item"):
            items.append({
                "title": text_of(e, "title"),
                "link": text_of(e, "link"),
                "summary": text_of(e, "description", CONTENT + "encoded"),
                "published": parse_date(text_of(e, "pubDate", DC + "date")),
            })
    return items


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def build_city_matcher(cities):
    # Longer names first so "Sun City West" wins over "Sun City".
    ordered = sorted(cities, key=len, reverse=True)
    return [(c, re.compile(r"\b" + re.escape(c) + r"\b", re.I)) for c in ordered]


def tag_cities(text, matchers, feed_city):
    """Cities named in the text win; the feed's own city is only a fallback."""
    found = []
    for name, rx in matchers:
        if name == "Sun City" and "Sun City West" in found:
            continue
        if rx.search(text):
            found.append(name)
    if not found and feed_city:
        found.append(feed_city)
    return found


def is_breaking(text, words):
    low = text.lower()
    return any(w in low for w in words)


SPORTS_SOURCES = {"maxpreps", "yahoo sports", "sports illustrated", "espn", "the athletic", "bleacher report", "cbs sports", "247sports", "on3", "rivals", "fox sports", "nbc sports", "sportskeeda", "fansided", "si.com", "arizona sports", "azpreps365", "just baseball", "the sporting news"}
DROP_SOURCES = {"wisevoter"}
DROP_TITLE_WORDS = ["obituary", "obituaries"]
SPORTS_WORDS = ["suns", "cardinals", "diamondbacks", "d-backs", "coyotes", "mercury", "sun devils", "asu ", "wildcats", "high school", "playoff", "nfl", "nba", "mlb", "nhl", "wnba", "ncaa", "football", "basketball", "baseball", "softball", "volleyball", "soccer", "golf", "quarterback", "touchdown", "recruit", "tournament", "preseason", "postseason", "box score", "halftime", "all-star"]

CATEGORIES = [
    ("Crime & Safety", ["shot", "shooting", "killed", "homicide", "murder", "police", "arrest", "suspect", "stabbing", "chase", "fire", "explosion", "standoff", "barricade", "missing", "amber alert", "indicted", "sentenced", "drowning", "rescue", "deputies", "sheriff"]),
    ("Roads & Weather", ["crash", "collision", "freeway", "highway", "i-10", "i-17", "loop 101", "loop 202", "sr 51", "closure", "closed", "traffic", "adot", "light rail", "forecast", "weather", "storm", "rain", "heat", "flood", "wind", "monsoon", "airport", "sky harbor"]),
    ("Government", ["council", "mayor", "election", "runoff", "budget", "vote", "voters", "ordinance", "county board", "supervisors", "legislature", "governor", "lawsuit", "settle", "tax", "water services", "drought", "city manager", "esa", "schools board", "zoning", "permit"]),
]


def categorize(text, source=""):
    low = text.lower()
    if source.lower() in SPORTS_SOURCES or any(w in low for w in SPORTS_WORDS):
        return "Sports"
    for name, words in CATEGORIES:
        if any(w in low for w in words):
            return name
    return "Community"


def split_google_source(title):
    """Google News titles look like 'Headline - Outlet'."""
    if " - " in title:
        head, _, outlet = title.rpartition(" - ")
        if head and 0 < len(outlet) <= 60:
            return head.strip(), outlet.strip()
    return title, ""


def norm_title(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


STOP = set("a an the of in on at to for and or but with as by from is are was were be been after before over into near amid says say said new via vs his her its their who what how why".split())
LOCAL_OUTLETS = ["abc15", "azfamily", "az family", "12news", "fox 10", "ktar", "azcentral", "arizona republic", "phoenix new times", "axios", "kjzz", "cbs news 5", "kpnx", "kpho", "city of ", "police", "fire department"]
CAT_WEIGHT = {"Crime & Safety": 14, "Roads & Weather": 10, "Government": 8, "Community": 6, "Sports": 4}
HIGH_WORDS = ["shot", "shooting", "killed", "dead", "death", "homicide", "murder", "explosion", "standoff", "evacuat", "missing", "amber alert", "stabbing", "wildfire", "drowning", "crash", "chase", "barricade"]
HOOK_WORDS = ["video", "caught on camera", "viral", "shocking", "surprising", "first", "record", "rare", "mystery", "discover", "rescued", "dog", "kids", "child", "baby", "massive", "huge", "closing", "closes", "opens", "opening", "free", "warning", "warns"]


def tokens(title):
    words = [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", title.lower())]
    return {w for w in words if w not in STOP and len(w) > 2}


def similar(a, b):
    inter = len(a & b)
    if inter < 3:
        return False
    return inter / len(a | b) >= 0.4 or inter / min(len(a), len(b)) >= 0.6


def is_local(source):
    low = source.lower()
    return any(k in low for k in LOCAL_OUTLETS)


def base_score(item):
    """Everything except recency, which the page adds because it changes between runs."""
    why = []
    score = 0
    outlets = {m["source"].lower() for m in item["members"]}
    if len(outlets) > 1:
        score += min((len(outlets) - 1) * 8, 40)
        why.append(f"Covered by {len(outlets)} outlets")
    score += CAT_WEIGHT.get(item["cat"], 6)
    low = item["title"].lower()
    if any(w in low for w in HIGH_WORDS):
        score += 10
        why.append("Urgent or high-stakes wording")
    if any(w in low for w in HOOK_WORDS):
        score += 6
        why.append("Strong hook for a post")
    if any(is_local(m["source"]) for m in item["members"]):
        score += 6
        why.append("Reported by a local outlet")
    if item["cities"] and any(c.lower() in low for c in item["cities"]):
        score += 5
        why.append("City named in the headline")
    return score, why


def cluster_and_score(nodes):
    """Merge items about the same story, count the outlets covering it, and score it."""
    for n in nodes:
        n.setdefault("members", [{"source": n.get("source", ""), "link": n["link"], "published": n["published"]}])
    toks = [tokens(n["title"]) for n in nodes]
    parent = list(range(len(nodes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen_title = {}
    for i, n in enumerate(nodes):
        key = norm_title(n["title"])
        if key in seen_title:
            parent[find(i)] = find(seen_title[key])
        else:
            seen_title[key] = i
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if find(i) != find(j) and similar(toks[i], toks[j]):
                parent[find(j)] = find(i)

    groups = {}
    for i, n in enumerate(nodes):
        groups.setdefault(find(i), []).append(n)

    out = []
    for group in groups.values():
        # Representative: a local outlet if one covered it, otherwise the earliest report.
        group.sort(key=lambda n: n["published"])
        rep = next((n for n in group if is_local(n["source"])), group[0])
        members, cities = {}, []
        for n in group:
            for m in n["members"]:
                members[m["link"]] = m
            for c in n["cities"]:
                if c not in cities:
                    cities.append(c)
        rep = dict(rep)
        rep["members"] = sorted(members.values(), key=lambda m: m["published"])[:12]
        rep["cities"] = cities
        rep["latest"] = max(m["published"] for m in members.values())
        rep["outlets"] = len({m["source"].lower() for m in members.values()})
        rep["base"], rep["why"] = base_score(rep)
        out.append(rep)
    out.sort(key=lambda i: i["latest"], reverse=True)
    return out


def main():
    config = json.loads(FEEDS_PATH.read_text())
    matchers = build_city_matcher(config["cities"])
    breaking_words = [w.lower() for w in config.get("breaking_words", [])]
    run_time = now()
    cutoff = run_time - timedelta(days=KEEP_DAYS)

    previous = {}
    if OUT_PATH.exists():
        try:
            for it in json.loads(OUT_PATH.read_text()).get("items", []):
                previous[it["id"]] = it
        except (ValueError, KeyError):
            pass

    collected = {}
    feed_status = []
    for feed in config["feeds"]:
        if not feed.get("enabled", True):
            continue
        status = {"id": feed["id"], "name": feed["name"], "group": feed.get("group", ""), "ok": False, "count": 0, "error": ""}
        try:
            raw_items = parse_feed(fetch(feed["url"]))
            status["ok"] = True
        except Exception as exc:  # noqa: BLE001 - record and move on
            status["error"] = (type(exc).__name__ + ": " + str(exc))[:160]
            feed_status.append(status)
            continue

        for raw in raw_items:
            title = clean(raw["title"], 220)
            link = raw["link"].strip()
            if not title or not link:
                continue
            source = feed["name"]
            if feed["id"].startswith("gn-"):
                title, outlet = split_google_source(title)
                source = outlet or "Google News"
            summary = clean(raw["summary"])
            if summary.lower().startswith(title.lower()[:40]):
                summary = ""  # Google News repeats the headline as the summary
            if any(w in title.lower() for w in DROP_TITLE_WORDS):
                continue
            published = raw["published"] or run_time
            if published < cutoff:
                continue
            item_id = hashlib.sha1(link.encode()).hexdigest()[:16]
            blob = title + " " + summary
            cities_found = tag_cities(blob, matchers, feed.get("city"))
            if not cities_found:
                continue  # broad searches pick up unrelated stories; keep only ones naming a Valley city
            collected[item_id] = {
                "id": item_id,
                "title": title,
                "link": link,
                "summary": summary,
                "source": source,
                "feed": feed["id"],
                "group": feed.get("group", ""),
                "cities": cities_found,
                "breaking": is_breaking(title, breaking_words),
                "cat": categorize(title + " " + summary, source),
                "published": published.isoformat(),
                "first_seen": previous.get(item_id, {}).get("first_seen", run_time.isoformat()),
            }
            status["count"] += 1
        feed_status.append(status)

    # Keep recent items from earlier runs even if a feed no longer lists them.
    for item_id, item in previous.items():
        if item_id not in collected:
            try:
                if datetime.fromisoformat(item["published"]) >= cutoff:
                    collected[item_id] = item
            except (ValueError, KeyError):
                pass

    for item_id in [k for k, v in collected.items() if v.get("source", "").lower() in DROP_SOURCES]:
        del collected[item_id]
    for item in collected.values():
        item["cat"] = categorize(item["title"] + " " + item.get("summary", ""), item.get("source", ""))

    items = cluster_and_score(list(collected.values()))[:MAX_ITEMS]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "generated": run_time.isoformat(),
        "cities": config["cities"],
        "feeds": feed_status,
        "items": items,
    }, indent=1, ensure_ascii=False))
    ok = sum(1 for f in feed_status if f["ok"])
    print(f"{len(items)} items from {ok}/{len(feed_status)} feeds")
    for f in feed_status:
        if not f["ok"]:
            print(f"  FAILED {f['id']}: {f['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
