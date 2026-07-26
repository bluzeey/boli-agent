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
- Celery/Redis worker path so the webhook can acknowledge quickly.
- Read-only procurement-case API.
- Docker Compose for API, worker, PostgreSQL, and Redis.
- Unit tests for signatures, payload parsing, requirement intake, formatting, and orchestration.

## Current scope boundary

The current product stops at a vendor shortlist. It does **not** yet:

- Contact vendors automatically.
- Claim that a search result is qualified.
- Collect quotations.
- Recommend a final vendor.
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
PROCESS_INLINE=true
SEARCH_PROVIDER=mock
```

This requires no Meta, Sarvam, Google, Redis, or PostgreSQL credentials. Outbound WhatsApp messages are logged as dry-run messages.

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

```bash
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -H 'Content-Type: application/json' \
  --data @scripts/sample_webhook.json
```

The terminal will show the WhatsApp reply in dry-run mode.

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

## Meta WhatsApp Cloud API setup

1. Create a Meta app with a WhatsApp Business Account and phone number.
2. Set the webhook callback to:

   ```text
   https://YOUR_HOST/webhooks/whatsapp
   ```

3. Use the same value for Meta's verification token and `WHATSAPP_VERIFY_TOKEN`.
4. Subscribe the app to the WABA `messages` field.
5. Configure:

   ```env
   WHATSAPP_ACCESS_TOKEN=
   WHATSAPP_APP_SECRET=
   WHATSAPP_PHONE_NUMBER_ID=
   WHATSAPP_GRAPH_VERSION=
   ```

Use a publicly reachable HTTPS endpoint. During development, a secure tunnel can expose the local server.

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

## Repository structure

```text
app/
  api/                 FastAPI routes
  integrations/        WhatsApp and Sarvam clients
  search/              Search-provider abstraction and adapters
  services/            Procurement orchestration and webhook processing
  config.py             Environment configuration
  db.py                 SQLModel engine and sessions
  models.py             Conversation, message, case, and search-run models
  worker.py             Celery worker
IMPLEMENTATION_PLAN.md   Full roadmap and acceptance tests
IDEA_SCOPE.md            Product and demo scope lock
```

## Immediate next milestone

Implement buyer shortlist selection and vendor-contact preparation:

1. Save ephemeral result references against the procurement case.
2. Accept replies such as `1, 3, 4` in WhatsApp.
3. Require buyer confirmation before vendor outreach.
4. Generate a canonical RFQ from the case.
5. Create a vendor-contact queue without sending cold messages automatically.
