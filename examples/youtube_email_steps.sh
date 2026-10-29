#!/usr/bin/env bash
# The same YouTube email lookup with nothing but curl, grep and sed.
#   export SCRAPINGBEE_API_KEY=...
#   bash youtube_email_steps.sh "home espresso"      # step 1, discovery
#   bash youtube_email_steps.sh @mkbhd                # steps 2 to 4 for one channel
set -euo pipefail

: "${SCRAPINGBEE_API_KEY:?set SCRAPINGBEE_API_KEY first}"
API="https://app.scrapingbee.com/api/v1"
AUTH="Authorization: Bearer ${SCRAPINGBEE_API_KEY}"
arg="${1:?give a search query or an @handle}"

if [[ "$arg" != @* ]]; then
  # Step 1: channel search, 5 credits. Handles sit in canonicalBaseUrl.
  curl -s -G "$API/youtube/search" -H "$AUTH" \
    --data-urlencode "search=$arg" \
    --data-urlencode "type=channel" \
    | grep -oE 'canonicalBaseUrl\\":\\"/@[^\\]+' | sed 's/.*\/@/@/' | sort -u
  exit 0
fi

# Step 2: the About page as plain HTML, 1 credit
page=$(mktemp)
curl -s -G "$API/" -H "$AUTH" \
  --data-urlencode "url=https://www.youtube.com/${arg}/about" \
  --data-urlencode "render_js=false" \
  -D "$page.headers" -o "$page"
echo "credits: $(grep -i '^spb-cost' "$page.headers" | tr -d '\r' | cut -d' ' -f2)"

# Step 3: cut out the aboutChannelViewModel object and its description
about=$(grep -o '"aboutChannelViewModel":{"description":"[^"]*' "$page" | head -1 || true)
if [[ -z "$about" ]]; then
  echo "no About data on the page"; exit 1
fi
echo "description: ${about#*\"description\":\"}" | sed 's/\\n/ /g' | cut -c1-160

# Step 4: emails in the description, and whether the gated button exists
echo "$about" | sed 's/\\n/ /g' \
  | grep -oiE '[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}' \
  | tr 'A-Z' 'a-z' | sort -u | sed 's/^/email: /' || echo "email: none in About text"
if grep -q '"signInForBusinessEmail"' "$page"; then
  echo "gated email button: present (sign in required, not used)"
fi

rm -f "$page" "$page.headers"
