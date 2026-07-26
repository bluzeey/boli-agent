# Boli — User Guide

This guide explains how to set up Boli, which external services you must
connect, how to run it locally and in Docker, and how to use the WhatsApp flow
and REST API.

It covers the **current implemented scope**: buyer intake → vendor search →
numbered shortlist → buyer selection → RFQ generation → outreach-approval gate.

---

## 1. What is implemented right now

A buyer sends a WhatsApp text or voice note. Boli:

1. Verifies the Meta webhook and deduplicates inbound messages.
2. Transcribes voice notes with Sarvam (text is used directly).
3. Extracts a structured procurement requirement.
4. Asks one clarifying question if the location is missing.
5. Searches Google Places (or a mock provider) and returns a **numbered
   shortlist** in WhatsApp.
6. Accepts a reply like `1, 3, 4`, echoes the selected vendors, and asks for
   confirmation.
7. On confirmation, generates a **canonical, versioned RFQ** and shows it.
8. Asks the buyer to approve outreach. On approval, the case reaches
   `outreach_approved` — a hard gate.

### Current boundary (important)

At `outreach_approved` Boli **stops**. It does **not** yet:

- Send any message to a vendor. No vendor is ever contacted in this milestone.
- Collect quotations, compare bids, or recommend a winner.
- Negotiate, sign, or commit spend.

Those are subsequent milestones. The outreach-approval gate is the hand-off
point for the next milestone (controlled vendor outreach).

---

## 2. Prerequisites

- Python 3.11+ (3.12 used in Docker).
- A terminal with `make`, or run the commands manually.
- For real WhatsApp/Sarvam/Google: accounts and API keys (see Section 5).
- For the quick local mock path: nothing except Python.

---

## 3. Quick start — local mock flow (no credentials)

This runs end-to-end with a fake search provider and dry-run WhatsApp output.
No Meta, Sarvam, Google, Redis, or Postgres credentials are required.

```bash
# 1. Create environment
cp .env.example .env
# Keep defaults: PROCESS_INLINE=true, SEARCH_PROVIDER=mock

# 2. Install (creates a venv)
python3 -m venv .venv
source .venv/bin/activate
make install        # pip install -e '.[dev]'

# 3. (Optional) apply DB schema. For dev SQLite the app also creates tables
#    on startup, but running migrations is the canonical step:
make migrate

# 4. Run the API
make dev            # uvicorn app.main:app --reload
```

In another terminal, simulate an inbound WhatsApp webhook:

```bash
curl -X POST http://localhost:8000/webhooks/whatsapp \
  -H 'Content-Type: application/json' \
  --data @scripts/sample_webhook.json
```

You will see the dry-run WhatsApp reply (a 5-vendor numbered shortlist) printed
in the API terminal.

---

## 4. The buyer journey (WhatsApp replies)

Each step is a separate WhatsApp message from the buyer. With mock search,
 replies appear as dry-run logs in the API terminal.

| Buyer sends | Boli replies | Case status after |
|---|---|---|
| `Find commercial pest control vendors in Jaipur` | Numbered shortlist (5 vendors) | `shortlist_ready` |
| `1, 3` | Echo of vendors 1 & 3 + "reply yes/no" | `awaiting_shortlist_confirmation` |
| `yes` | The canonical RFQ + "reply approve to authorize outreach" | `rfq_ready` |
| `approve` | "Outreach approved — no vendors contacted yet" | `outreach_approved` |

Other accepted replies:

- `no` at the confirmation step → clears the selection, returns to
  `shortlist_ready`.
- `no` at the RFQ step → returns to `shortlist_ready` to re-select.
- `new search` at `shortlist_ready` → closes the case; the next message starts a
  fresh case.
- Any new requirement text at `shortlist_ready` → closes the old case and starts
  a new one.
- Out-of-range numbers (e.g. `9` when only 5 exist) → an error with the valid
  range.

---

## 5. Connecting the real services

Set these in `.env` (copy from `.env.example`).

### 5.1 Meta WhatsApp Cloud API

Boli receives messages via webhook and sends replies via the phone-number
`/messages` endpoint.

1. Create a Meta app with a **WhatsApp Business Account** and a phone number.
2. Set the webhook callback URL to a public HTTPS endpoint:

   ```
   https://YOUR_HOST/webhooks/whatsapp
   ```

   During development use a tunnel (e.g. `ngrok http 8000` or a Cloudflare
   tunnel) to expose `localhost:8000`.

3. In the Meta dashboard, subscribe the app to the `messages` webhook field.
4. Configure these env vars (use the same verify token in Meta and in `.env`):

   ```env
   WHATSAPP_VERIFY_TOKEN=<your-choose-a-token>
   WHATSAPP_ACCESS_TOKEN=<permanent-or-temporary-token>
   WHATSAPP_APP_SECRET=<app-secret-for-signature-check>
   WHATSAPP_PHONE_NUMBER_ID=<phone-number-id>
   WHATSAPP_GRAPH_VERSION=v20.0
   ```

Notes:

- `WHATSAPP_APP_SECRET` is required to validate the `x-hub-signature-256`
  header. If it is empty, signature validation is skipped (dev convenience —
  never leave it empty in production).
- When `WHATSAPP_ACCESS_TOKEN` or `WHATSAPP_PHONE_NUMBER_ID` are empty, Boli
  runs in **dry-run mode**: outbound messages are logged instead of sent.

### 5.2 Sarvam (speech-to-text + structured extraction)

```env
SARVAM_API_KEY=<your-key>
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_STT_MODEL=saaras:v3
SARVAM_STT_MODE=codemix
```

- `saaras:v3` transcribes WhatsApp voice notes (Hindi, English, code-mixed).
- `sarvam-105b` produces structured procurement requirements via JSON-schema
  constrained output.
- If `SARVAM_API_KEY` is empty, Boli falls back to a deterministic heuristic
  extractor so you can still test text intake locally.

### 5.3 Google Places (real local vendor search)

```env
SEARCH_PROVIDER=google_places
GOOGLE_PLACES_API_KEY=<your-key>
SEARCH_RESULT_LIMIT=5
```

- Enable the **Places API (New)** in Google Cloud and create an API key.
- The adapter uses Text Search with a field mask requesting only the fields it
  displays. It persists the query and result count plus each candidate's Place
  ID as the durable external reference; display fields are stored transiently
  with an expiry (`GOOGLE_RESULT_CACHE_MINUTES`, default 30).
- Leave `SEARCH_PROVIDER=mock` for local demos with no key.

---

## 6. Database and migrations

Boli supports SQLite (dev) and PostgreSQL (Docker/production).

### Dev (SQLite)

```env
DATABASE_URL=sqlite:///./boli.db
```

The app creates tables on startup via `create_all`. For a clean schema you can
also run `make migrate`.

### Docker / production (PostgreSQL)

```env
DATABASE_URL=postgresql+psycopg://boli:boli@localhost:5432/boli
```

Use Alembic to manage schema evolution:

```bash
make migrate        # alembic upgrade head
```

To create a new migration after changing models:

```bash
alembic revision --autogenerate -m "describe change"
make migrate
```

> Note: run `make migrate` **before** starting the app against a fresh
> persistent database, so the `alembic_version` table is seeded correctly.

---

## 7. Running in Docker

Docker Compose starts the API, a Celery worker, PostgreSQL, and Redis.

```bash
cp .env.example .env
# For Docker, set: PROCESS_INLINE=false, SEARCH_PROVIDER=mock or google_places,
# DATABASE_URL=postgresql+psycopg://boli:boli@postgres:5432/boli

make up            # docker compose up --build
make migrate       # apply migrations to the Postgres container (in another terminal)
```

Endpoints:

- API: http://localhost:8000
- Health: http://localhost:8000/health

To stop: `make down`.

When `PROCESS_INLINE=false`, inbound webhooks are queued to the Celery worker
(via Redis) instead of being processed synchronously. This is the recommended
mode for anything beyond local testing.

---

## 8. REST API reference

All endpoints are prefixed with nothing (mounted at root). JSON in/out.

### Health

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service health |

### WhatsApp webhook

| Method | Path | Purpose |
|---|---|---|
| GET | `/webhooks/whatsapp` | Meta webhook verification |
| POST | `/webhooks/whatsapp` | Receive WhatsApp events |

### Cases

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/cases/{id}` | Read canonical case state |
| GET | `/api/cases/{id}/candidates` | List vendor candidates (with `selected`/`confirmed`/`expired`) |
| POST | `/api/cases/{id}/shortlist` | Record a buyer selection |
| POST | `/api/cases/{id}/rfq` | Generate (or regenerate) the RFQ |
| GET | `/api/cases/{id}/rfq` | Read the latest RFQ |
| POST | `/api/cases/{id}/rfq/approve` | Approve outreach (the gate) |

### Example: select vendors and generate an RFQ via the API

```bash
# After a search has produced candidates (status = shortlist_ready):

# 1. Select vendors 1 and 3
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/shortlist \
  -H 'Content-Type: application/json' \
  -d '{"selection": [1, 3]}'

# 2. Generate the RFQ from the selection
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/rfq

# 3. Read the generated RFQ
curl http://localhost:8000/api/cases/<CASE_ID>/rfq

# 4. Approve outreach (reaches outreach_approved; no vendor is contacted)
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/rfq/approve
```

### Example: list candidates

```bash
curl http://localhost:8000/api/cases/<CASE_ID>/candidates
```

Response fields: `position`, `name`, `phone`, `address`, `rating`,
`review_count`, `source_url`, `selected`, `confirmed`, `expired`.

---

## 9. Simulating the full multi-turn flow locally

This script sends four simulated WhatsApp messages and prints the case status.
Requires `PROCESS_INLINE=true` and `SEARCH_PROVIDER=mock`.

```bash
python3 - <<'PY'
import json, urllib.request

BASE = "http://localhost:8000"
SENDER = "919999999999"

def send(msg_id, body):
    payload = {"entry":[{"id":"1","changes":[{"value":{"messages":[
        {"id":msg_id,"from":SENDER,"type":"text","text":{"body":body}}]}}]}]}
    req = urllib.request.Request(
        f"{BASE}/webhooks/whatsapp",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()

send("w1", "Find commercial pest control vendors in Jaipur")
send("w2", "1, 3")
send("w3", "yes")
send("w4", "approve")
print("Done. Check the API terminal for dry-run WhatsApp replies.")
PY
```

The API terminal will show: the shortlist → the selection echo → the RFQ → the
outreach-approved gate message.

---

## 10. Configuration reference

All values are in `.env` (see `.env.example`). Key options:

| Variable | Default | Purpose |
|---|---|---|
| `PROCESS_INLINE` | `true` | Process webhooks synchronously (true) or via Celery (false) |
| `SEARCH_PROVIDER` | `mock` | `mock` or `google_places` |
| `SEARCH_RESULT_LIMIT` | `5` | Max vendors returned per search |
| `GOOGLE_RESULT_CACHE_MINUTES` | `30` | Transient candidate expiry (data-policy) |
| `MAX_MESSAGE_CHARS` | `4000` | Outbound WhatsApp message truncation |
| `MAX_AUDIO_BYTES` | `12000000` | Voice-note download size cap |
| `DATABASE_URL` | `sqlite:///./boli.db` | SQLAlchemy URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker/backend |

---

## 11. Troubleshooting

- **Webhook returns 403 on GET verify** — `WHATSAPP_VERIFY_TOKEN` doesn't match
  the token configured in the Meta dashboard.
- **Signature validation fails** — `WHATSAPP_APP_SECRET` is wrong or missing.
  (When empty, validation is skipped — dev only.)
- **No outbound WhatsApp messages sent** — `WHATSAPP_ACCESS_TOKEN` or
  `WHATSAPP_PHONE_NUMBER_ID` empty → dry-run mode (messages logged only).
- **Voice notes not transcribed** — `SARVAM_API_KEY` missing → STT unavailable;
  text intake still works via the heuristic fallback.
- **Search returns nothing real** — `SEARCH_PROVIDER` is `mock`; set to
  `google_places` and provide `GOOGLE_PLACES_API_KEY`.
- **`alembic upgrade head` errors "table already exists"** — the app's
  `create_all` ran first and created tables without the `alembic_version` row.
  Drop the database (or the volume) and run `make migrate` before starting the
  app.

---

## 12. What to connect — checklist

- [ ] Meta WhatsApp Business app + phone number (webhook, verify token, access
      token, app secret, phone number ID, graph version).
- [ ] Public HTTPS endpoint for the webhook (tunnel in dev).
- [ ] Sarvam API key (STT + structured chat).
- [ ] Google Places API key + enable Places API (New) — only for real search.
- [ ] PostgreSQL + Redis (for Docker / production) — or SQLite for dev.
- [ ] Run `make migrate` against the persistent database.

After these are connected, the flow in Section 4 works end-to-end on real
WhatsApp, up to (but not including) vendor outreach.
