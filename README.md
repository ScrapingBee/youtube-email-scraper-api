# YouTube Email Scraper API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="REPLACE_WITH_SCREENSHOT_URL" alt="youtube-email-scraper-api" />
  </a>
</p>

[![checks](https://github.com/ScrapingBee/youtube-email-scraper-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/youtube-email-scraper-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/youtube-email-scraper-api.svg)](LICENSE)

Creators who want sponsorship offers usually type a business address into their channel description. This [YouTube email scraper](https://www.scrapingbee.com/scrapers/youtube-email-scraper-api/) reads that public text, and nothing else, through ScrapingBee. The same five steps are written three times below, in Python, Node.js and cURL, so you can copy the one that fits your stack. Python is the full version with retries, typed records and CSV output.

Every output on this page was produced by the files in [`examples/`](examples) on 2026-10-29.

| File | Language | What it covers |
|---|---|---|
| [`channel_discovery.py`](examples/channel_discovery.py) | Python | Step 1 on its own, with a subscriber filter |
| [`youtube_email_finder.py`](examples/youtube_email_finder.py) | Python | Steps 1 to 5, retries, CSV and JSON export, CLI |
| [`youtube_email_finder.js`](examples/youtube_email_finder.js) | Node.js 18+ | Steps 1 to 4, JSON lines |
| [`youtube_email_steps.sh`](examples/youtube_email_steps.sh) | bash + cURL | Steps 1 to 4 with grep and sed |

## Out of scope: the "View email address" button

Many channels have a button on their About panel that reveals a business email. On a logged out page it is replaced by this object inside `ytInitialData`:

```json
"signInForBusinessEmail":{"content":"Sign in to see email address","commandRuns":[{"startIndex":0,"length":7,"onTap":{"innertubeCommand": ...
```

Its tap target is `accounts.google.com/ServiceLogin`, so the address is only shown to a signed in Google account. Getting past that means scraping as a logged in user, which ScrapingBee's terms do not allow. So the code only records whether the button exists (`email_button_gated`) and moves on. In our runs it existed on 16 of the 29 channels we could read, which tells you how many creators keep their address behind it rather than in plain text.

## Step 1: find channels

ScrapingBee has a dedicated [YouTube API](https://www.scrapingbee.com/documentation/youtube/) with three endpoints: `youtube/search`, `youtube/metadata` and `youtube/subtitles`, 5 credits per call according to the docs and the `spb-cost` header. Search takes `type=channel`, which is what discovery needs.

**Python**

```python
resp = requests.get(
    "https://app.scrapingbee.com/api/v1/youtube/search",
    params={"search": "sourdough baking", "type": "channel"},
    headers={"Authorization": f"Bearer {key}"},
)
items = json.loads(resp.json()["results"])   # "results" is a JSON string, decode it again
```

**Node.js**

```javascript
const res = await bee("youtube/search", { search: query, type: "channel" });
const items = JSON.parse((await res.json()).results || "[]");
```

**cURL**

```bash
curl -s -G "https://app.scrapingbee.com/api/v1/youtube/search" \
  -H "Authorization: Bearer $SCRAPINGBEE_API_KEY" \
  --data-urlencode "search=home espresso" --data-urlencode "type=channel"
```

Two things surprised us in the response. `results` is a string holding JSON, not an array, so it has to be parsed twice. And on channel results the labels are shifted: `subscriberCountText` holds the handle (`"@DrEspresso"`) while `videoCountText` holds `"616 subscribers"`. The parser in `channel_discovery.py` picks whichever label contains "subscriber" instead of trusting the field name.

```bash
python examples/channel_discovery.py "sourdough baking" --min-subs 10000 --out channels.csv
```

```
@OhmyBread.online            18.4K subscribers    Oh my Bread Sourdough Baking School
@shebakesourdough            181K subscribers     Shebakesourdough
@Bakewithjack                322K subscribers     Bake with Jack
@MaryGraceBread              252K subscribers     Mary Grace Bread
@ellyswholegrainsourdough    35.1K subscribers    Elly's Everyday Wholegrain Sourdough
...
17 channels, 5 credits
```

The sourdough run kept 17 channels after the 10,000 subscriber filter. For "home espresso" the same call returned 20.

## Step 2: fetch the About page

`https://www.youtube.com/@<handle>/about` (or `/channel/<UC id>/about`) through the ordinary HTML API with `render_js=false`. The data is in the server HTML, so 1 credit is enough.

**Python**

```python
status, html, cost = bee.html(f"https://www.youtube.com/@{handle}/about")
# bee.html() sends {"url": ..., "render_js": "false"} and retries 5xx answers
```

**Node.js**

```javascript
const res = await bee("", { url: `https://www.youtube.com/@${handle}/about`, render_js: "false" });
```

**cURL**

```bash
curl -s -G "https://app.scrapingbee.com/api/v1/" \
  -H "Authorization: Bearer $SCRAPINGBEE_API_KEY" \
  --data-urlencode "url=https://www.youtube.com/@mkbhd/about" \
  --data-urlencode "render_js=false" -o about.html
```

The page is large (2.9 MB for @mkbhd). On the first pass 5 of 30 requests came back as HTTP 200 without the About data, billed like any other call. Asked again, four of them had it and the fifth, a handle that does not exist, answered 404. The Python version now retries up to three times and keeps a `tries` count per channel; on the nine channel run below, 5 of its 14 About requests were retries and every channel resolved.

## Step 3: read `ytInitialData`

The page assigns one big object, `var ytInitialData = {...};`. The part you want is `aboutChannelViewModel`, trimmed here from the @mkbhd page:

```json
"aboutChannelViewModel": {
  "description": "MKBHD: Quality Tech Videos | YouTuber | Geek | Consumer Electronics | Tech Head | Internet Personality!\n\nbusiness@MKBHD.com\n\nNYC",
  "country": "United States",
  "subscriberCountText": "21.4M subscribers",
  "viewCountText": "5,838,434,786 views",
  "joinedDateText": {"content": "Joined Mar 21, 2008"},
  "canonicalChannelUrl": "http://www.youtube.com/@mkbhd",
  "channelId": "UCBJycsmduvYEL83R_U4JriQ",
  "videoCountText": "1,863 videos",
  "signInForBusinessEmail": {"content": "Sign in to see email address", ...},
  "links": [{"channelExternalLinkViewModel": {"title": {"content": "Twitter"}, "link": {"content": "twitter.com/MKBHD", ...}}}]
}
```

**Python**

```python
m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
about = next(walk(json.loads(m.group(1)), "aboutChannelViewModel"), None)
```

`walk()` is a ten line recursive generator in `youtube_email_finder.py`; the object's path changes between layouts, so searching by key is safer than a fixed path.

**Node.js**

```javascript
const m = html.match(/var ytInitialData = (\{.*?\});<\/script>/s);
const about = m && findKey(JSON.parse(m[1]), "aboutChannelViewModel");
```

**cURL**

```bash
grep -o '"aboutChannelViewModel":{"description":"[^"]*' about.html | head -1
```

The shell version relies on `description` being the first key, which held on every page we fetched. A description that contains an escaped quote will be cut short there; the Python and Node versions parse real JSON and do not have that problem.

Each external link is wrapped in a `youtube.com/redirect?...&q=<real url>` command. `link_target()` in the Python file unwraps `q`, so the links column holds `http://twitter.com/MKBHD`, not a redirect token.

## Step 4: pull the YouTube channel email out of the text

**Python**

```python
EMAIL_RE = re.compile(
    r"(?<![\w.+-])([a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9_])?@(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24})(?![\w-])",
    re.I,
)
emails = find_emails(about["description"])   # NFKC first, lowercase, drops logo@2x.png style hits
```

**Node.js**

```javascript
for (const m of text.normalize("NFKC").matchAll(EMAIL_RE)) { /* same pattern, same filters */ }
```

**cURL**

```bash
grep -oiE '[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}' | tr 'A-Z' 'a-z' | sort -u
```

Running all three on @mkbhd:

```
$ bash examples/youtube_email_steps.sh @mkbhd
credits: 1
description: MKBHD: Quality Tech Videos | YouTuber | Geek | Consumer Electronics | Tech Head | Internet Personality!  business@MKBHD.com  NYC
email: business@mkbhd.com
gated email button: present (sign in required, not used)
```

```
$ node examples/youtube_email_finder.js @mkbhd @DylansHomeEspressoBar @DrEspresso
{"handle":"@mkbhd","status":"ok","channelId":"UCBJycsmduvYEL83R_U4JriQ","subscribers":"21.4M subscribers","emailButtonGated":true,"emails":["business@mkbhd.com"]}
{"handle":"@DylansHomeEspressoBar","status":"ok","channelId":"UCYD9bgxgud3EpDTuemsBONg","subscribers":"28.3K subscribers","emailButtonGated":false,"emails":[]}
{"handle":"@DrEspresso","status":"ok","channelId":"UCdwX2NE2jYWkhfm-5_vhQvw","subscribers":"616 subscribers","emailButtonGated":false,"emails":["craigcoffeecontact@gmail.com"]}
2/3 channels with an email in About, 3 credits
```

## Step 5: video descriptions as a fallback

Some creators leave the About text short and put "business inquiries" under each video instead. The Python finder handles that with `--videos N`: when About has no address, it takes video ids from the About page, asks ScrapingBee's `youtube/metadata` endpoint for each, and scans the `description` field. Per the docs the endpoint takes `video_id` (required) and `tag` (optional); `video_id` is the one we exercised live.

**Python**

```python
meta = bee.call("youtube/metadata", {"video_id": vid}).json()
if meta.get("channel_id") == channel.channel_id:      # the About page also lists other channels' videos
    found += find_emails(meta.get("description", ""))
```

**cURL**

```bash
curl -s -G "https://app.scrapingbee.com/api/v1/youtube/metadata" \
  -H "Authorization: Bearer $SCRAPINGBEE_API_KEY" \
  --data-urlencode "video_id=q0aFOxT6TNw"
```

The response for that id, trimmed:

```json
{
  "video_id": "q0aFOxT6TNw",
  "title": "iPhone 17 Pro Review: Paradox in a Box!",
  "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
  "uploader": "Marques Brownlee",
  "uploader_id": "@mkbhd",
  "upload_date": "20250926",
  "view_count": 7534941,
  "duration": 741,
  "description": "The two-sided existence of the iPhone 17 Pro/Pro Max\n\nProtect your iPhone 17 Pro with dbrand at ...",
  "formats": ["... 54 entries ..."]
}
```

Top level keys returned: `age_limit`, `categories`, `channel_id`, `channel_url`, `comment_count`, `description`, `duration`, `formats`, `is_live`, `like_count`, `tags`, `thumbnails`, `title`, `upload_date`, `uploader`, `uploader_id`, `uploader_url`, `video_id`, `view_count`. The `channel_id` check matters: the About page HTML lists many video ids, and not all of them belong to the channel.

The fallback costs 5 credits per video, so it is off by default.

## YouTube channel email finder results

Two samples, run with `--videos 1`:

```bash
python examples/youtube_email_finder.py --search "home espresso" --max 20 --videos 1 --csv espresso.csv
python examples/youtube_email_finder.py @mkbhd @LinusTechTips @MrBeast @veritasium @MarkRober \
  @jameshoffmann @NileRed @kurzgesagt @tomscottgo --videos 1 --csv known.csv
```

```
@mkbhd                     ok       21.4M subscribers   tries 3  gated=True  about business@mkbhd.com
@LinusTechTips             ok       16.9M subscribers   tries 1  gated=True  about partnerships@linusmediagroup.com
@MrBeast                   ok       524M subscribers    tries 2  gated=True  video chucky@mrbeastbusiness.com
@veritasium                ok       21.4M subscribers   tries 1  gated=True        -
@MarkRober                 ok       85M subscribers     tries 2  gated=True        -
@jameshoffmann             ok       2.57M subscribers   tries 1  gated=True        -
@NileRed                   ok       11M subscribers     tries 1  gated=True        -
@kurzgesagt                ok       25.8M subscribers   tries 1  gated=True        -
@tomscottgo                ok       6.75M subscribers   tries 2  gated=False       -

9/9 About pages read. Email in About text: 2. Only in a video description: 1. Gated email button present: 8. Credits: 49
```

| Sample | Channels read | Email in About text | Only in a video description | Gated button present |
|---|---|---|---|---|
| "home espresso" search, 20 channels, mostly small | 20 | 2 | 0 | 8 |
| 9 large channels picked by name | 9 | 2 | 1 | 8 |

Four plain text addresses and one from a video, out of 29 channels. That is the honest yield of public text: most large creators route business mail through the gated button, and most small ones publish no address at all. If you need a contact for a channel that shows neither, the channel's own website (listed in `links`) is the next public place to look.

## Costs

| Call | Credits |
|---|---|
| `youtube/search` (one page of up to 20 channels) | 5 |
| About page, `render_js=false` | 1 |
| About page with JavaScript on (ScrapingBee's default) | 5 |
| `youtube/metadata`, per video | 5 |
| The 20 channel espresso run above, with one video each | 100 |
| The nine named channels, retries included | 49 |

New accounts start with 1,000 free credits; plan sizes are on the [pricing page](https://www.scrapingbee.com/pricing/). Requests that fail with a 5xx are not billed, so the retry loops cost nothing when they fire on those.

## Using the addresses responsibly

These are addresses creators published so that brands could reach them. Treat them that way: keep the source URL and the date with each address, write one relevant message rather than a sequence, and stop when asked. In the EU and UK an email address tied to a person is personal data under the GDPR even when it was public; in the US, commercial email falls under CAN-SPAM. This is not legal advice. YouTube's terms of service apply to the site itself.

## More ScrapingBee scrapers for creator research

[YouTube scraper API](https://www.scrapingbee.com/scrapers/youtube-video-scraper-api/), [YouTube comment scraper](https://www.scrapingbee.com/scrapers/youtube-comment-scraper-api/), [YouTube transcript scraper](https://www.scrapingbee.com/scrapers/youtube-transcript-scraper-api/), [TikTok email scraper](https://www.scrapingbee.com/scrapers/tiktok-email-scraper-api/), [Linktree profile details](https://www.scrapingbee.com/scrapers/linktree-profile-details-api/), [contact info scraper](https://www.scrapingbee.com/scrapers/contact-info-api/).

Reference: [YouTube API docs](https://www.scrapingbee.com/documentation/youtube/), [HTML API parameters](https://www.scrapingbee.com/documentation/), [YouTube feature page](https://www.scrapingbee.com/features/youtube/).

## FAQ

**How do I find a YouTube channel's email address?**
Read the channel's About description, where many creators type a business address. Fetching `/@handle/about` through ScrapingBee costs 1 credit, and the address sits in `aboutChannelViewModel.description`.

**Can a YouTube email finder get the address behind "View email address"?**
No. That button needs a Google sign in. This repo records that the button exists and does not try to open it.

**Is there a YouTube channel email finder for many channels at once?**
`youtube_email_finder.py --search "<topic>"` discovers up to 20 channels per search call and checks each About page. You can also pass any number of handles or `UC` channel ids as arguments.

**Does ScrapingBee's YouTube API return channel emails?**
Not directly. Its `youtube/metadata` endpoint returns a video's description, and the finder scans that text as a fallback.

**Why does my About page have no data?**
Some responses come back without `aboutChannelViewModel`. Request the page again; one more request fixed every case in our runs except a handle that did not exist.

## License

MIT. See [LICENSE](LICENSE).
