"""YouTube channel email finder: public About pages and video descriptions only.

Pipeline, one function per step so each can be reused:

  1. discover   channel_discovery.search_channels()       youtube/search, 5 credits
  2. about      fetch_about()  https://www.youtube.com/@<handle>/about   1 credit
  3. parse      parse_about()  ytInitialData -> aboutChannelViewModel
  4. extract    find_emails()  description text and link labels
  5. fallback   video_emails() youtube/metadata for recent uploads, 5 credits each

The About page also carries `signInForBusinessEmail` ("Sign in to see email
address"). That button needs a Google sign in and a CAPTCHA, so it is recorded
as a flag and never followed.

    export SCRAPINGBEE_API_KEY=...
    python youtube_email_finder.py @mkbhd @DylansHomeEspressoBar
    python youtube_email_finder.py --search "home espresso" --min-subs 10000 --videos 2 --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

from channel_discovery import search_channels, subscriber_number

API = "https://app.scrapingbee.com/api/v1/"
YT_DATA_RE = re.compile(r"var ytInitialData = (\{.*?\});</script>", re.S)
EMAIL_RE = re.compile(
    r"(?<![\w.+-])([a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9_])?@(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24})(?![\w-])",
    re.I,
)
ASSET_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}


# ---------------------------------------------------------------- records

@dataclass
class Channel:
    handle: str
    channel_id: str = ""
    title: str = ""
    status: str = "pending"          # ok | no_about | error_<code>
    subscribers: str = ""
    views: str = ""
    videos: str = ""
    country: str = ""
    joined: str = ""
    description: str = ""
    links: list[dict] = field(default_factory=list)
    email_button_gated: bool = False  # the "View email address" button exists
    emails_about: list[str] = field(default_factory=list)
    emails_videos: list[str] = field(default_factory=list)
    videos_checked: int = 0
    attempts: int = 0
    credits: int = 0

    @property
    def emails(self) -> list[str]:
        return list(dict.fromkeys(self.emails_about + self.emails_videos))


# ---------------------------------------------------------------- transport

class Bee:
    def __init__(self, key: str | None = None):
        self.key = key or os.environ.get("SCRAPINGBEE_API_KEY")
        if not self.key:
            raise SystemExit("Set SCRAPINGBEE_API_KEY first.")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {self.key}"
        self.credits = 0

    def call(self, endpoint: str, params: dict, tries: int = 3) -> requests.Response:
        for attempt in range(1, tries + 1):
            r = self.s.get(API + endpoint, params=params, timeout=120)
            if r.status_code < 500 or attempt == tries:
                self.credits += int(r.headers.get("spb-cost", 0) or 0)
                return r
            time.sleep(2 * attempt)  # 5xx is not billed
        return r

    def html(self, url: str) -> tuple[int, str, int]:
        r = self.call("", {"url": url, "render_js": "false"})
        return r.status_code, r.text, int(r.headers.get("spb-cost", 0) or 0)

    def video_metadata(self, video_id: str) -> tuple[dict | None, int]:
        r = self.call("youtube/metadata", {"video_id": video_id})
        cost = int(r.headers.get("spb-cost", 0) or 0)
        return (r.json() if r.status_code == 200 else None), cost


# ---------------------------------------------------------------- parsing

def find_emails(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text or "")
    out: list[str] = []
    for m in EMAIL_RE.finditer(text):
        e = m.group(1).lower()
        if e.rsplit(".", 1)[-1] not in ASSET_TLDS and e not in out:
            out.append(e)
    return out


def walk(node, key):
    """Yield every value stored under `key` anywhere in a nested structure."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, key)


def _content(v) -> str:
    return v.get("content", "") if isinstance(v, dict) else (v or "")


def parse_about(html: str) -> tuple[dict | None, list[str]]:
    """Return (aboutChannelViewModel, video ids seen on the page)."""
    m = YT_DATA_RE.search(html)
    if not m:
        return None, []
    data = json.loads(m.group(1))
    about = next(walk(data, "aboutChannelViewModel"), None)
    ids = list(dict.fromkeys(re.findall(r'"videoId":"([\w-]{11})"', m.group(1))))
    return about, ids


def link_target(link_vm: dict) -> str:
    """Links point at youtube.com/redirect?q=<real url>; return the real url."""
    for run in link_vm.get("link", {}).get("commandRuns", []):
        url = run.get("onTap", {}).get("innertubeCommand", {}).get("urlEndpoint", {}).get("url", "")
        q = parse_qs(urlparse(url).query).get("q")
        if q:
            return q[0]
    return _content(link_vm.get("link"))


def fill_from_about(c: Channel, about: dict) -> None:
    c.status = "ok"
    c.channel_id = about.get("channelId", c.channel_id)
    c.description = about.get("description", "")
    c.subscribers = about.get("subscriberCountText", "")
    c.views = about.get("viewCountText", "")
    c.videos = about.get("videoCountText", "")
    c.country = about.get("country", "")
    c.joined = _content(about.get("joinedDateText")).replace("Joined ", "")
    c.email_button_gated = "signInForBusinessEmail" in about
    for item in about.get("links", []):
        vm = item.get("channelExternalLinkViewModel", {})
        c.links.append({"title": _content(vm.get("title")), "url": link_target(vm)})
    label_text = " ".join(f"{l['title']} {l['url']}" for l in c.links)
    c.emails_about = find_emails(c.description + "\n" + label_text.replace("mailto:", " "))


# ---------------------------------------------------------------- steps

def about_url(handle_or_id: str) -> str:
    h = handle_or_id.strip()
    if h.startswith("UC") and len(h) == 24:
        return f"https://www.youtube.com/channel/{h}/about"
    return f"https://www.youtube.com/@{h.lstrip('@')}/about"


def fetch_about(bee: Bee, handle: str, tries: int = 3) -> tuple[Channel, list[str]]:
    """Some responses come back without the About model (a different page
    variant, still HTTP 200 and still billed). Asking again usually fixes it."""
    c = Channel(handle=handle if handle.startswith(("@", "UC")) else "@" + handle)
    ids: list[str] = []
    for _ in range(tries):
        c.attempts += 1
        status, html, cost = bee.html(about_url(handle))
        c.credits += cost
        if status != 200:
            c.status = f"error_{status}"
            if status == 404:
                break
            continue
        about, ids = parse_about(html)
        if about:
            fill_from_about(c, about)
            return c, ids
        c.status = "no_about"
    return c, ids


def video_emails(bee: Bee, c: Channel, video_ids: list[str], limit: int) -> None:
    """Read up to `limit` of the channel's own videos through youtube/metadata.

    The About page lists video ids from its shelves, which can include other
    channels, so each response is checked against the channel id.
    """
    for vid in video_ids:
        if c.videos_checked >= limit:
            break
        meta, cost = bee.video_metadata(vid)
        c.credits += cost
        if not meta or meta.get("channel_id") != c.channel_id:
            continue
        c.videos_checked += 1
        for e in find_emails(meta.get("description", "")):
            if e not in c.emails_about and e not in c.emails_videos:
                c.emails_videos.append(e)


def run(handles: list[str], video_limit: int) -> tuple[list[Channel], int]:
    bee = Bee()
    rows = []
    for h in dict.fromkeys(handles):
        c, ids = fetch_about(bee, h)
        if c.status == "ok" and not c.emails_about and video_limit:
            video_emails(bee, c, ids, video_limit)
        rows.append(c)
        mark = ", ".join(c.emails) or "-"
        src = "about" if c.emails_about else ("video" if c.emails_videos else "")
        print(f"{c.handle:<26} {c.status:<8} {c.subscribers:<19} tries {c.attempts}  gated={str(c.email_button_gated):<5} {src:<5} {mark}")
    return rows, bee.credits


def save(rows: list[Channel], csv_path: str | None, json_path: str | None) -> None:
    if csv_path:
        cols = ["handle", "channel_id", "status", "subscribers", "country", "joined",
                "emails", "email_source", "email_button_gated", "videos_checked", "attempts", "credits"]
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for c in rows:
                d = asdict(c)
                d["emails"] = ";".join(c.emails)
                d["email_source"] = "about" if c.emails_about else ("video" if c.emails_videos else "")
                w.writerow({k: d[k] for k in cols})
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump([{**asdict(c), "emails": c.emails} for c in rows], fh, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube channel email finder (public text only)")
    ap.add_argument("handles", nargs="*", help="@handle or UC channel id")
    ap.add_argument("--search", help="discover channels first with youtube/search")
    ap.add_argument("--min-subs", type=float, default=0)
    ap.add_argument("--max", type=int, default=20, help="cap on discovered channels")
    ap.add_argument("--videos", type=int, default=0, help="video descriptions to read when About has no email")
    ap.add_argument("--csv")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    handles = list(args.handles)
    discovery_credits = 0
    if args.search:
        hits, discovery_credits = search_channels(args.search)
        hits = [h for h in hits if (subscriber_number(h.subscribers) or 0) >= args.min_subs]
        handles += [h.handle or h.channel_id for h in hits[: args.max]]
    if not handles:
        ap.error("give handles or --search")

    rows, credits = run(handles, args.videos)
    ok = [c for c in rows if c.status == "ok"]
    gated = sum(c.email_button_gated for c in ok)
    print(
        f"\n{len(ok)}/{len(rows)} About pages read. Email in About text: {sum(bool(c.emails_about) for c in ok)}. "
        f"Only in a video description: {sum(bool(c.emails_videos) and not c.emails_about for c in ok)}. "
        f"Gated email button present: {gated}. Credits: {credits + discovery_credits}"
    )
    save(rows, args.csv, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
