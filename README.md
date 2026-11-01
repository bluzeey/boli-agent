# Boli Procurement Agent

Boli is a WhatsApp-first sourcing agent for SMBs. A buyer describes a business need in text or a voice note. Boli converts it into a structured procurement case, asks for missing information, searches for local vendors, and returns a shortlist inside WhatsApp.

This repository implements the first vertical slice:

```text
WhatsApp text/voice note
        -> verified Meta webhook
        -> Sarvam transcription (voice only)
        -> Sarvam structured requirement extraction
        -> procurement case state
        -> Google Places Text Search
        -> vendor shortlist sent back through WhatsApp
```

The production vision extends the same case into vendor outreach, bid completion, quote normalization, human approval, and contract preparation. Those later stages are intentionally not hidden inside the current MVP.

## What is implemented

- Meta WhatsApp Cloud API webhook verification.
- `x-hub-signature-256` validation.
- Idempotent inbound-message handling.
- Text, interactive reply, and voice-note ingestion.
- WhatsApp media download.
- Sarvam Saaras v3 transcription for voice notes.
- Sarvam structured-output parsing into a procurement requirement.
- A deterministic fallback parser for local development.
- Persistent conversations and procurement cases.
- Clarification flow when the search location is missing.
- Google Places Text Search adapter.
- Mock search adapter for local demos.
- Search results formatted and returned through WhatsApp.
- Transient vendor candidates persisted with position and expiry.
- Buyer shortlist selection (`1, 3, 4`) over WhatsApp.
- Shortlist confirmation and clearing.
- Canonical, versioned RFQ generation from the case.
- Controlled vendor outreach: durable vendor records with consent, suppression,
  and rate limiting; RFQ sent to consented vendors on approval.
- Vendor-facing RFQ message (distinct from the buyer-facing preview).
- Outreach-approval gate and per-vendor send/delivery status.
- Inbound vendor-reply detection: vendor replies are linked to the right case
  and vendor, marked *responded*, with the buyer notified.
- Structured quote-field extraction from vendor replies (price, tax, lead time,
  payment terms, exclusions) via Sarvam, with a deterministic heuristic fallback.
- Bid comparison + recommendation (lowest complete effective cost, exclusion
  flags) delivered on WhatsApp.
- Bid-completeness follow-ups and winner selection.
- Draft purchase-order document generation on buyer approval.
- Buyer `status` / `compare` / `select` / `followup` / `approve` commands and a
  `collecting_responses` guard (unrecognized text no longer silently closes the case).
- Celery/Redis worker path so the webhook can acknowledge quickly.
- Read-only procurement-case API plus shortlist/RFQ/document endpoints.
- Docker Compose for API, worker, PostgreSQL, and Redis.
- Alembic migrations.
- Unit + route-level tests for signatures, payload parsing, requirement intake,
  formatting, selection, RFQ generation, the shortlist flow, outreach, vendor
  replies, quote extraction, comparison, and both webhook routes.

## Current scope boundary

The current product extracts vendor quotes, compares bids, recommends a winner,
and drafts a purchase order. It does **not** yet:

- Extract quotes from PDF/image replies (text and voice replies are extracted).
- Present a web bid room (comparison is delivered on WhatsApp).
- E-sign the document (it is a draft for human review/signature).
- Claim that a search result is qualified.
- Negotiate prices or terms, or track performance/renewals.
- Persist Google Places content as a permanent vendor database.

## Local setup

### 1. Configure environment

```bash
cp .env.example .env
```

For a local browser demo, use:

```env
WHATSAPP_PROVIDER=twilio
PROCESS_INLINE=true
SEARCH_PROVIDER=mock
ALLOW_OUTREACH=false
```

Open `/chat` on the same backend origin. Browser assistant replies are stored in
the transcript without sending WhatsApp messages. Leave `SARVAM_API_KEY` unset
for the local heuristic parser; use mock search to avoid search-provider calls.

`ALLOW_OUTREACH=false` blocks **all external WhatsApp sends and read receipts**,
including buyer replies, vendor acknowledgements and follow-ups. It preserves
browser transcript replies. It does not disable inbound processing, Sarvam or
Google Places calls when those integrations are configured. Enable external
WhatsApp messaging explicitly only when intended.

### 2. Install and run

```bash
make install
make migrate
make dev
```

Health check:

```bash
curl http://localhost:8000/health
```

`/health` is a static liveness check; it does not verify database migrations,
Redis or worker readiness. The deployment command in `railway.toml` requires
`python -m alembic upgrade head` to succeed before starting Gunicorn. A failed
migration stops startup. Run migrations before a manually launched development
server as shown above.

### 3. Exercise webhook handling offline

Unsigned webhook curl demos, including `scripts/sample_twilio_webhook.sh`, are
now rejected. Missing signing credentials also reject callbacks; there is no
unsigned development bypass. Use the signed synthetic callback tests instead:

```bash
python -m pytest tests/test_webhook_routes.py tests/test_twilio_delivery.py
```

The tests force disposable SQLite settings before app imports, ignore dotenv
files, use synthetic clients/signatures and block real HTTP/socket traffic.
They never send provider messages.

### 4. Run tests

```bash
make test
```

## Docker setup

```bash
cp .env.example .env
make up
```

For Docker, use the PostgreSQL and Redis URLs already present in `.env.example`, and set `PROCESS_INLINE=false`.

## Twilio WhatsApp setup

Use `WHATSAPP_PROVIDER=twilio`. Configure these names securely for intentional
provider use: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_WHATSAPP_FROM`, and `APP_BASE_URL`.

`APP_BASE_URL` must be the exact canonical external HTTPS backend origin,
including a non-default port if used, without a path prefix or query. Register
`<canonical-origin>/webhooks/twilio/whatsapp` for inbound POSTs and, when delivery
callbacks are used, `<canonical-origin>/webhooks/twilio/status` for status POSTs.
The application reconstructs these exact URLs for signature verification.
Avoid redirects, callback query parameters and proxy path rewrites.
`TWILIO_AUTH_TOKEN` is required to validate both routes; missing or invalid
`X-Twilio-Signature` returns 401 before processing or delivery-state updates.

Vendor RFQ sends persist the Twilio response's outbound MessageSid in
`VendorResponse.twilio_message_sid`. Status callbacks match that exact stored SID,
never the recipient phone or newest case. The nullable, unique field is added by
migration `b6e39f8a2c10`; existing rows stay uncorrelated, with no guessed backfill.
Unmatched callbacks, including callbacks received before the send's SID is
committed, are acknowledged without mutation. No deferred reconciliation is
implemented. A resend retires the previous SID before making another attempt.

Delivery cannot regress to sent or failed, and a delivery callback cannot
overwrite a vendor reply or other terminal business state. A delivered callback
may supersede an earlier failed result for the same SID; duplicate events do
not rewrite timestamps. Transient database errors return 503.

Keep `ALLOW_OUTREACH=false` for non-sending checks. With it off, external
WhatsApp buyer replies and read receipts are also suppressed; browser replies
continue. `PROCESS_INLINE=false` queues provider inbound processing, while browser
messages are handled synchronously; it is not a sending kill switch.

## Meta WhatsApp Cloud API setup

Use `WHATSAPP_PROVIDER=meta` and register
`<canonical-origin>/webhooks/whatsapp` on the same canonical HTTPS backend origin.
Configure `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`,
`WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and
`WHATSAPP_GRAPH_VERSION` securely. Subscribe to the `messages` webhook field.

Verification GET requests require the configured verification token; no default
token fallback exists. Event POSTs require the configured app secret and a valid
`X-Hub-Signature-256` over the unchanged raw body. Missing credentials never
bypass validation. Preserve the signature headers and raw body through the proxy.

Operator access policy remains a separate deployment decision.

## Sarvam setup

Set:

```env
SARVAM_API_KEY=
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_STT_MODEL=saaras:v3
SARVAM_STT_MODE=codemix
```

Structured requirement and quote extraction explicitly disable reasoning and cap
the answer at 2048 tokens. Sarvam documents that default reasoning can consume
the completion budget and leave visible content empty. Missing or malformed
answers now produce a controlled error without including raw model output.
See [Sarvam's Chat Completion guide](https://docs.sarvam.ai/api/token-factory/chat-completions).

The current REST speech-to-text path is intended for short WhatsApp voice notes. Longer recordings should move to Sarvam's batch API.

## Search setup

For real local-business search:

```env
SEARCH_PROVIDER=google_places
GOOGLE_PLACES_API_KEY=
```

The adapter uses Google Places Text Search (New) with a field mask. Google Places content has storage, display, attribution, and caching restrictions. The MVP persists the query and result count, not a permanent copy of the returned place records. Store durable vendor information only after it is independently supplied or confirmed by the vendor, and retain the Google Place ID as the external reference.

## Core endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Service health |
| GET | `/webhooks/whatsapp` | Meta webhook verification |
| POST | `/webhooks/whatsapp` | Receive WhatsApp events |
| GET | `/api/cases/{case_id}` | Inspect canonical procurement state |
| GET | `/api/cases/{case_id}/candidates` | List vendor candidates with selection state |
| POST | `/api/cases/{case_id}/shortlist` | Record a buyer selection (`{"selection":[1,3]}`) |
| POST | `/api/cases/{case_id}/rfq` | Generate (or regenerate) the RFQ |
| GET | `/api/cases/{case_id}/rfq` | Read the latest RFQ |
| POST | `/api/cases/{case_id}/rfq/approve` | Approve the RFQ and prepare outreach |
| POST | `/api/cases/{case_id}/outreach` | Send the RFQ to consented vendors |
| GET | `/api/cases/{case_id}/vendors` | List vendors with outreach status |
| POST | `/api/cases/{case_id}/vendors/{vendor_id}/consent` | Grant/revoke vendor consent |
| GET | `/api/cases/{case_id}/responses` | List per-vendor outreach status + extracted quote fields |
| GET | `/api/cases/{case_id}/document` | Read the generated draft purchase-order document |

See `USER_GUIDE.md` for the full setup, credentials checklist, and end-to-end
examples.

## Repository structure

```text
app/
  api/                 FastAPI routes
  categories/          Category-pack contract, registry, and generic RFQ pack
  integrations/        WhatsApp and Sarvam clients
  search/              Search-provider abstraction and adapters
  services/            Orchestration, webhook processing, selection parser, RFQ
  config.py            Environment configuration
  db.py                SQLAlchemy engine and sessions
  models.py            Conversation, message, case, search-run, candidate, RFQ
  worker.py            Celery worker
alembic/               Alembic migrations
USER_GUIDE.md           Setup, credentials, and API usage
IMPLEMENTATION_PLAN.md   Full roadmap and acceptance tests
IDEA_SCOPE.md            Product and demo scope lock
```

## Next milestone

The procurement loop now runs end-to-end on WhatsApp (search → RFQ → outreach →
quote extraction → comparison → recommendation → draft PO document). Remaining
work that needs "way more":

1. PDF/image quotation ingestion (OCR/vision extraction).
2. An internal React bid-room web view.
3. E-sign integration so the draft document can be executed.
4. Vendor performance, invoice reconciliation, and renewal memory.

See `USER_GUIDE.md` for the credentials and setup, and `IMPLEMENTATION_PLAN.md`
for the full roadmap.
