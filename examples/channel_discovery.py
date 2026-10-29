"""Step 1: find YouTube channels for a topic with ScrapingBee's YouTube search endpoint.

GET https://app.scrapingbee.com/api/v1/youtube/search?search=<q>&type=channel
costs 5 credits and answers {"results": "<JSON as a string>", "search": ...}.
The results field is a string, so it is decoded a second time here.

One quirk worth knowing: on channel results YouTube shifts its labels.
`subscriberCountText` holds the @handle and `videoCountText` holds the
subscriber count. The parser looks at the text, not at the field name.

    export SCRAPINGBEE_API_KEY=...
    python channel_discovery.py "home espresso" --out channels.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import asdict, dataclass

import requests

SEARCH_URL = "https://app.scrapingbee.com/api/v1/youtube/search"


@dataclass
class ChannelHit:
    channel_id: str
    title: str
    handle: str            # "@DrEspresso"
    subscribers: str       # as displayed, e.g. "28.3K subscribers"
    snippet: str


def _text(node: dict | None) -> str:
    if not node:
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(run.get("text", "") for run in node.get("runs", []))


def parse_hits(payload: dict) -> list[ChannelHit]:
    raw = payload.get("results", "[]")
    items = json.loads(raw) if isinstance(raw, str) else raw
    hits = []
    for item in items:
        if "channelId" not in item:
            continue  # type=channel can still mix in other shapes
        browse = item.get("navigationEndpoint", {}).get("browseEndpoint", {})
        labels = [_text(item.get("subscriberCountText")), _text(item.get("videoCountText"))]
        subs = next((t for t in labels if "subscriber" in t), "")
        handle = browse.get("canonicalBaseUrl", "").lstrip("/") or next((t for t in labels if t.startswith("@")), "")
        hits.append(
            ChannelHit(
                channel_id=item["channelId"],
                title=_text(item.get("title")),
                handle=handle,
                subscribers=subs,
                snippet=re.sub(r"\s+", " ", _text(item.get("descriptionSnippet"))).strip(),
            )
        )
    return hits


def search_channels(query: str, api_key: str | None = None, retries: int = 3) -> tuple[list[ChannelHit], int]:
    key = api_key or os.environ["SCRAPINGBEE_API_KEY"]
    params = {"search": query, "type": "channel"}
    for attempt in range(1, retries + 1):
        resp = requests.get(SEARCH_URL, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=90)
        if resp.status_code < 500 or attempt == retries:
            break
    resp.raise_for_status()
    return parse_hits(resp.json()), int(resp.headers.get("spb-cost", 0) or 0)


def subscriber_number(label: str) -> float | None:
    """'28.3K subscribers' -> 28300.0, '1 subscriber' -> 1.0"""
    m = re.match(r"([\d.,]+)\s*([KMB]?)", label.replace(",", ""))
    if not m:
        return None
    mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
    return float(m.group(1)) * mult


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube channel search through ScrapingBee")
    ap.add_argument("query")
    ap.add_argument("--min-subs", type=float, default=0, help="drop channels below this many subscribers")
    ap.add_argument("--out", help="CSV path")
    args = ap.parse_args(argv)

    hits, cost = search_channels(args.query)
    hits = [h for h in hits if (subscriber_number(h.subscribers) or 0) >= args.min_subs]
    for h in hits:
        print(f"{h.handle:<28} {h.subscribers:<20} {h.title}")
    print(f"{len(hits)} channels, {cost} credits", file=sys.stderr)
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(hits[0]))) if hits else None
            if w:
                w.writeheader()
                w.writerows(asdict(h) for h in hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
