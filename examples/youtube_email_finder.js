// YouTube email scraper, Node 18+ version of the Python pipeline.
// Steps: search channels -> About page -> parse ytInitialData -> regex.
//
//   export SCRAPINGBEE_API_KEY=...
//   node youtube_email_finder.js @mkbhd @DylansHomeEspressoBar
//   node youtube_email_finder.js --search "home espresso"

const API = "https://app.scrapingbee.com/api/v1/";
const KEY = process.env.SCRAPINGBEE_API_KEY;
const EMAIL_RE = /(?<![\w.+-])([a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9_])?@(?:[a-z0-9-]{1,63}\.)+[a-z]{2,24})(?![\w-])/gi;

let credits = 0;

async function bee(endpoint, params) {
  const url = `${API}${endpoint}?${new URLSearchParams(params)}`;
  for (let i = 1; i <= 3; i++) {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${KEY}` } });
    if (res.status >= 500 && i < 3) {
      await new Promise((r) => setTimeout(r, 2000 * i)); // 5xx is not billed
      continue;
    }
    credits += Number(res.headers.get("spb-cost") || 0);
    return res;
  }
}

// Step 1: youtube/search with type=channel. "results" is a JSON string.
async function searchChannels(query) {
  const res = await bee("youtube/search", { search: query, type: "channel" });
  const items = JSON.parse((await res.json()).results || "[]");
  return items
    .filter((it) => it.channelId)
    .map((it) => it.navigationEndpoint?.browseEndpoint?.canonicalBaseUrl?.replace("/", "") || it.channelId);
}

function emails(text) {
  const out = [];
  for (const m of (text || "").normalize("NFKC").matchAll(EMAIL_RE)) {
    const e = m[1].toLowerCase();
    if (!/\.(png|jpe?g|gif|webp|svg)$/.test(e) && !out.includes(e)) out.push(e);
  }
  return out;
}

function findKey(node, key) {
  if (node && typeof node === "object") {
    if (key in node) return node[key];
    for (const v of Object.values(node)) {
      const hit = findKey(v, key);
      if (hit !== undefined) return hit;
    }
  }
  return undefined;
}

// Steps 2 to 4: About page (1 credit), ytInitialData, regex on description
async function aboutEmails(handle) {
  const path = handle.startsWith("UC") ? `channel/${handle}` : `@${handle.replace(/^@/, "")}`;
  const res = await bee("", { url: `https://www.youtube.com/${path}/about`, render_js: "false" });
  if (!res.ok) return { handle, status: `error_${res.status}` };
  const m = (await res.text()).match(/var ytInitialData = (\{.*?\});<\/script>/s);
  const about = m && findKey(JSON.parse(m[1]), "aboutChannelViewModel");
  if (!about) return { handle, status: "no_about" };
  return {
    handle,
    status: "ok",
    channelId: about.channelId,
    subscribers: about.subscriberCountText,
    emailButtonGated: "signInForBusinessEmail" in about,
    emails: emails(about.description),
  };
}

async function main() {
  const args = process.argv.slice(2);
  if (!KEY || args.length === 0) {
    console.error('usage: node youtube_email_finder.js <@handle ...> | --search "query"');
    process.exit(1);
  }
  const handles = args[0] === "--search" ? await searchChannels(args.slice(1).join(" ")) : args;
  let withEmail = 0;
  for (const h of handles) {
    const row = await aboutEmails(h);
    if (row.emails?.length) withEmail++;
    console.log(JSON.stringify(row));
  }
  console.error(`${withEmail}/${handles.length} channels with an email in About, ${credits} credits`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
