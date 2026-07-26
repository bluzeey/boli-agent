#!/usr/bin/env bash
# Simulate inbound WhatsApp messages through the Twilio webhook (default provider).
#
# Requires the API running locally: make dev
# Requires: WHATSAPP_PROVIDER=twilio, PROCESS_INLINE=true, SEARCH_PROVIDER=mock
# (Outbound messages are dry-run logged when Twilio credentials are absent.)
#
# Usage: bash scripts/sample_twilio_webhook.sh [BASE_URL]
set -euo pipefail

BASE="${1:-http://localhost:8000}"
FROM="whatsapp:%2B919999999999"  # whatsapp:+919999999999 (URL-encoded)

send() {
  local sid="$1" body="$2"
  curl -s -X POST "$BASE/webhooks/twilio/whatsapp" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data "MessageSid=${sid}&From=${FROM}&Body=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$body")&NumMedia=0" \
    >/dev/null
  echo "sent: ${body}"
}

echo "Simulating the buyer journey via the Twilio webhook..."
send "demo-001" "Find commercial pest control vendors in Jaipur"
send "demo-002" "1, 3"
send "demo-003" "yes"
send "demo-004" "approve"
echo ""
echo "Done. Check the API terminal for the dry-run WhatsApp replies"
echo "(shortlist -> selection echo -> RFQ -> outreach summary)."
