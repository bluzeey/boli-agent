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
- Celery/Redis worker path so the webhook can acknowledge quickly.
- Read-only procurement-case API plus shortlist/RFQ endpoints.
- Docker Compose for API, worker, PostgreSQL, and Redis.
- Alembic migrations.
- Unit tests for signatures, payload parsing, requirement intake, formatting,
  selection, RFQ generation, and the shortlist flow.

## Current scope boundary

The current product sends the RFQ to consented vendors after buyer approval and
then stops at `collecting_responses`. It does **not** yet:

- Ingest or parse vendor quotations (text, voice, PDF, image).
- Claim that a search result is qualified.
- Compare bids, detect exclusions, or recommend a final vendor.
- Negotiate prices or terms.
- Sign or create a legally binding agreement.
- Persist Google Places content as a permanent vendor database.

## Local setup

### 1. Configure environment

```bash
cp .env.example .env
```

For a local dry run, keep:

```env
WHATSAPP_PROVIDER=twilio
PROCESS_INLINE=true
SEARCH_PROVIDER=mock
```

This requires no Twilio, Sarvam, Google, Redis, or PostgreSQL credentials. Outbound WhatsApp messages are logged as dry-run messages.

### 2. Install and run

```bash
make install
make dev
```

Health check:

```bash
curl http://localhost:8000/health
```

### 3. Simulate a WhatsApp webhook

The default provider is Twilio, which posts form-encoded data:

```bash
curl -X POST http://localhost:8000/webhooks/twilio/whatsapp \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'MessageSid=demo-001&From=whatsapp%3A%2B919999999999&Body=Find+pest+control+vendors+in+Jaipur&NumMedia=0'
```

The terminal will show the WhatsApp reply in dry-run mode. (The Meta Cloud API
route at `/webhooks/whatsapp` is also available when `WHATSAPP_PROVIDER=meta`.)

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

## Twilio WhatsApp Sandbox setup (default, prototype)

The default `WHATSAPP_PROVIDER=twilio` path uses the Twilio Sandbox so you can
test the full WhatsApp flow without the Meta onboarding. See `USER_GUIDE.md`
Section 5.1 for the complete walkthrough. In short:

1. Activate the WhatsApp Sandbox in your Twilio console and join it from your
   personal WhatsApp number.
2. Configure in `.env`:

   ```env
   WHATSAPP_PROVIDER=twilio
   TWILIO_ACCOUNT_SID=
   TWILIO_AUTH_TOKEN=
   TWILIO_WHATSAPP_FROM=+14155238886
   APP_BASE_URL=https://YOUR_NGROK_URL.ngrok-free.app
   ```

3. Expose the local server (`ngrok http 8000`) and point the Twilio Sandbox
   webhook to `https://YOUR_HOST/webhooks/twilio/whatsapp` (HTTP POST).
4. Send a requirement from your joined WhatsApp number.

`TWILIO_AUTH_TOKEN` validates the `X-Twilio-Signature` header (skipped when
empty, for dev). Empty Twilio credentials → dry-run mode (messages logged).

## Meta WhatsApp Cloud API setup (production alternate)

The direct Meta integration is preserved for production. Set
`WHATSAPP_PROVIDER=meta` and configure:

1. Create a Meta app with a WhatsApp Business Account and phone number.
2. Set the webhook callback to:

   ```text
   https://YOUR_HOST/webhooks/whatsapp
   ```

3. Use the same value for Meta's verification token and `WHATSAPP_VERIFY_TOKEN`.
4. Subscribe the app to the WABA `messages` field.
5. Configure:

   ```env
   WHATSAPP_PROVIDER=meta
   WHATSAPP_ACCESS_TOKEN=
   WHATSAPP_APP_SECRET=
   WHATSAPP_PHONE_NUMBER_ID=
   WHATSAPP_GRAPH_VERSION=
   ```

These Meta env vars are intentionally omitted from `.env.example`; add them
only when switching to the Meta provider. Use a publicly reachable HTTPS
endpoint. During development, a secure tunnel can expose the local server.

## Sarvam setup

Set:

```env
SARVAM_API_KEY=
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_STT_MODEL=saaras:v3
SARVAM_STT_MODE=codemix
```

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
| GET | `/api/cases/{case_id}/responses` | List per-vendor outreach status |

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

Quotation ingestion. The current milestone ends at `collecting_responses` — the
RFQ has been sent to consented vendors. The next milestone:

1. Receive vendor responses in text, voice, PDF, and image.
2. Link each response to the correct case and vendor.
3. Extract generic commercial fields with evidence references.
4. Mark fields sourced, inferred, missing, or contradicted.

See `USER_GUIDE.md` for the credentials and setup, and `IMPLEMENTATION_PLAN.md`
Milestone 5 for the full build tasks.
