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
- Outreach-approval gate (`outreach_approved`) — no vendor is contacted.
- Celery/Redis worker path so the webhook can acknowledge quickly.
- Read-only procurement-case API plus shortlist/RFQ endpoints.
- Docker Compose for API, worker, PostgreSQL, and Redis.
- Alembic migrations.
- Unit tests for signatures, payload parsing, requirement intake, formatting,
  selection, RFQ generation, and the shortlist flow.

## Current scope boundary

The current product stops at the outreach-approval gate. After the buyer
approves outreach (`outreach_approved`), Boli does **not** yet:

- Send any message to a vendor (no vendor is ever contacted in this milestone).
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
| GET | `/api/cases/{case_id}/candidates` | List vendor candidates with selection state |
| POST | `/api/cases/{case_id}/shortlist` | Record a buyer selection (`{"selection":[1,3]}`) |
| POST | `/api/cases/{case_id}/rfq` | Generate (or regenerate) the RFQ |
| GET | `/api/cases/{case_id}/rfq` | Read the latest RFQ |
| POST | `/api/cases/{case_id}/rfq/approve` | Approve the outreach gate |

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

Controlled vendor outreach. The current milestone ends at `outreach_approved` —
no vendor is contacted. The next milestone picks up there:

1. Add a vendor-contact queue with consent, template, and rate controls.
2. Send the approved RFQ to the selected vendors through WhatsApp and email.
3. Track delivery and response status per vendor.
4. Do not enable autonomous cold outreach until messaging consent, template,
   rate, and anti-spam controls are reviewed.

See `USER_GUIDE.md` for the credentials and setup needed before that, and
`IMPLEMENTATION_PLAN.md` Milestone 4 for the full build tasks.
