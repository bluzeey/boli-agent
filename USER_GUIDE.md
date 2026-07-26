# Boli — User Guide

This guide explains how to set up Boli, which external services you must
connect, how to run it locally and in Docker, and how to use the WhatsApp flow
and REST API.

It covers the **current implemented scope**: buyer intake → vendor search →
numbered shortlist → buyer selection → RFQ generation → outreach-approval gate.

---

## 1. What is implemented right now

A buyer sends a WhatsApp text or voice note. Boli:

1. Receives the message via a WhatsApp provider webhook — **Twilio Sandbox**
   (default, for prototyping) or the Meta Cloud API — with signature validation
   and deduplication.
2. Transcribes voice notes with Sarvam (text is used directly).
3. Extracts a structured procurement requirement.
4. Asks one clarifying question if the location is missing.
5. Searches Google Places (or a mock provider) and returns a **numbered
   shortlist** in WhatsApp.
6. Accepts a reply like `1, 3, 4`, echoes the selected vendors, and asks for
   confirmation.
7. On confirmation, generates a **canonical, versioned RFQ** and shows it.
8. Asks the buyer to approve outreach. On approval, Boli sends the RFQ to the
   selected vendor leads that have **contact consent** (mock/test vendors are
   pre-consented; discovered vendors are cold and skipped until consent is
   granted), then moves to `collecting_responses`.
9. When a vendor replies (text or voice note), Boli links it to the right case
   and vendor, marks the vendor as *responded*, acknowledges the vendor, and
   notifies the buyer.

### Current boundary (important)

Boli captures vendor replies (raw text / transcript) and tracks who responded,
but it does **not** yet:

- Extract structured quote fields (price, tax, lead time, payment terms) from
  replies — replies are stored verbatim.
- Compare bids, detect exclusions, or recommend a winner.
- Negotiate, sign, or commit spend.

Those are subsequent milestones (quotation ingestion, bid comparison).

---

## 2. Prerequisites

- Python 3.11+ (3.12 used in Docker).
- A terminal with `make`, or run the commands manually.
- For real WhatsApp/Sarvam/Google: accounts and API keys (see Section 5). The
  default prototype path uses the **Twilio Sandbox**; the Meta Cloud API is an
  alternate for production.
- For the quick local mock path: nothing except Python.

---

## 3. Quick start — local mock flow (no credentials)

This runs end-to-end with a fake search provider and dry-run WhatsApp output.
No Twilio, Sarvam, Google, Redis, or Postgres credentials are required.

```bash
# 1. Create environment
cp .env.example .env
# Keep defaults: WHATSAPP_PROVIDER=twilio, PROCESS_INLINE=true, SEARCH_PROVIDER=mock

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

In another terminal, simulate an inbound **Twilio** WhatsApp webhook (the
default provider). Twilio posts form-encoded data:

```bash
curl -X POST http://localhost:8000/webhooks/twilio/whatsapp \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data 'MessageSid=demo-001&From=whatsapp%3A%2B919999999999&Body=Find+pest+control+vendors+in+Jaipur&NumMedia=0'
```

You will see the dry-run WhatsApp reply (a 5-vendor numbered shortlist) printed
in the API terminal. (The Meta Cloud API route at `/webhooks/whatsapp` is also
available if you set `WHATSAPP_PROVIDER=meta`.)

---

## 4. The buyer journey (WhatsApp replies)

Each step is a separate WhatsApp message from the buyer. With mock search,
 replies appear as dry-run logs in the API terminal.

| Buyer sends | Boli replies | Case status after |
|---|---|---|
| `Find commercial pest control vendors in Jaipur` | Numbered shortlist (5 vendors) | `shortlist_ready` |
| `1, 3` | Echo of vendors 1 & 3 + "reply yes/no" | `awaiting_shortlist_confirmation` |
| `yes` | The canonical RFQ + "reply approve to authorize outreach" | `rfq_ready` |
| `approve` | "Outreach approved" + sends RFQ to consented vendors + summary | `collecting_responses` |

After outreach, the buyer can manage the case at `collecting_responses`:

- `status` → shows how many RFQs were sent, how many vendors responded, and any
  skipped/failed.
- `consent 2` → grants buyer-confirmed consent to the vendor at original
  shortlist position 2 and re-queues it.
- `resend` → re-runs outreach for any queued vendors (e.g. after granting
  consent).
- `new search` (or any new requirement explicitly) → closes the case and starts
  fresh. Unrecognized text no longer silently closes the case — it replies with
  a hint of available commands.

When a vendor replies, the buyer is automatically notified ("Vendor X replied")
and can reply `status` to see progress.

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

### 5.1 Twilio WhatsApp Sandbox (default, recommended for prototyping)

Twilio Sandbox lets you test the full WhatsApp flow without registering a
production WhatsApp sender or completing the Meta onboarding. Boli receives
messages via a Twilio webhook and sends replies via the Twilio REST API.

```env
WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=<your-twilio-auth-token>
TWILIO_WHATSAPP_FROM=+14155238886
APP_BASE_URL=https://YOUR_NGROK_URL.ngrok-free.app
```

Setup steps:

1. **Activate the Sandbox** in Twilio: Messaging → Try it out → Send a WhatsApp
   message → Activate. Twilio shows a shared WhatsApp number and a join code
   (e.g. `join example-word`). Send that join code from your personal WhatsApp
   number. Each tester must join before the bot can message them.
2. **Configure the project** with the env vars above. The Sandbox number may
   differ — use the exact number shown in your Twilio console.
3. **Expose your local server** with a tunnel:
   ```bash
   ngrok http 8000
   ```
   Set `APP_BASE_URL` to the ngrok HTTPS URL (Twilio signs the full URL it posts
   to, so this must match).
4. **Add the webhook** in the Twilio Sandbox settings: set
   "When a message comes in" to
   `https://YOUR_NGROK_URL.ngrok-free.app/webhooks/twilio/whatsapp`, HTTP POST,
   and save.
5. **Test the chat**: from your joined WhatsApp number, send
   `Find packaging vendors in Jaipur`. With `SEARCH_PROVIDER=mock` you get an
   immediate sample shortlist; switch to `google_places` for live search.

Notes:

- `TWILIO_AUTH_TOKEN` is required to validate the `X-Twilio-Signature` header.
  If it is empty, signature validation is skipped (dev convenience — never leave
  it empty in production).
- When `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN`/`TWILIO_WHATSAPP_FROM` are
  empty, Boli runs in **dry-run mode**: outbound messages are logged instead of
  sent.
- **Sandbox limitations**: every tester must join via the code; joined sessions
  expire periodically; it uses a shared Twilio number; outside the 24-hour
  user-initiated messaging window, outbound messages require approved templates.
  This is adequate for the hackathon. Move to the direct Meta Cloud API
  (Section 5.5) only when the procurement workflow is production-ready.

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

### 5.4 Vendor outreach (consent, channels, rate limits)

Outreach is the controlled path from RFQ approval to vendors receiving the RFQ.
It is gated by **per-vendor consent** plus a global kill-switch.

**Consent model (the primary safety gate):**

- Each durable `Vendor` record has `contact_consent`. A vendor is contacted
  only if consent is `true`, `opted_out` is `false`, and its phone/email is not
  on the suppression list.
- Mock/test vendors are pre-consented (`pre_consented_test`) so the dev and
  acceptance flow sends to them immediately.
- Vendors discovered via Google Places are **cold** (`contact_consent=false`)
  and are **never contacted automatically**. They are recorded as
  `skipped_cold`. Grant consent via WhatsApp (`consent <position>`) or the API,
  then `resend`.
- Consent persists across cases (a vendor contacted in one case is recognised
  in the next, deduped by external ID / Place ID).

**Global kill-switch:**

```env
ALLOW_OUTREACH=true        # set false to disable all sends instantly
OUTBOUND_RATE_DELAY_SECONDS=2.0
MAX_OUTREACH_PER_BATCH=20
OUTREACH_CHANNEL=whatsapp
```

**Channels:** WhatsApp sends via the Meta Cloud API (dry-run logged when
`WHATSAPP_ACCESS_TOKEN`/`WHATSAPP_PHONE_NUMBER_ID` are empty). Email is wired as
a stub interface only — real SMTP sending is a later addition.

**Vendor-facing message:** the RFQ sent to vendors is *different* from the
buyer-facing preview. It is a polite outbound request ("A buyer would like to
request a quotation…") with the requirement, deadline, and the quote fields to
include.

**WhatsApp policy note (real deployment):** Meta requires an approved message
template to message a number outside the 24-hour customer-service window, and
cold marketing messages are against policy. Boli enforces consent in code, but
the operator is responsible for ensuring vendors have opted in and that an
approved template is used where required. Keep `ALLOW_OUTREACH=false` until
messaging consent, template, rate, and anti-spam controls have been reviewed.

### 5.5 Meta WhatsApp Cloud API (production alternate)

The direct Meta integration is preserved for production use once you outgrow
the Twilio Sandbox. Set `WHATSAPP_PROVIDER=meta` and configure:

```env
WHATSAPP_PROVIDER=meta
WHATSAPP_VERIFY_TOKEN=<your-choose-a-token>
WHATSAPP_ACCESS_TOKEN=<permanent-or-temporary-token>
WHATSAPP_APP_SECRET=<app-secret-for-signature-check>
WHATSAPP_PHONE_NUMBER_ID=<phone-number-id>
WHATSAPP_GRAPH_VERSION=v20.0
```

- Webhook URL: `https://YOUR_HOST/webhooks/whatsapp` (Meta route).
- Subscribe the app to the `messages` webhook field in the Meta dashboard.
- `WHATSAPP_APP_SECRET` validates the `x-hub-signature-256` header (skipped if
  empty). Empty access token / phone-number ID → dry-run mode.
- These Meta env vars are intentionally omitted from `.env.example`; add them
  only when switching to the Meta provider.

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
| POST | `/api/cases/{id}/rfq/approve` | Approve the RFQ and prepare outreach (queued) |
| POST | `/api/cases/{id}/outreach` | Send the RFQ to consented vendors |
| GET | `/api/cases/{id}/vendors` | List vendors with outreach status |
| POST | `/api/cases/{id}/vendors/{vendor_id}/consent` | Grant/revoke vendor consent |
| GET | `/api/cases/{id}/responses` | List per-vendor outreach status (queued/sent/failed/skipped) |

### Example: select vendors and generate an RFQ via the API

```bash
# After a search has produced candidates (status = shortlist_ready):

# 1. Select vendors 1 and 3
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/shortlist \
  -H 'Content-Type: application/json' \
  -d '{"selection": [1, 3]}'

# 2. Generate the RFQ from the selection
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/rfq

# 3. Approve the RFQ and prepare outreach (vendors + queued responses)
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/rfq/approve

# 4. Send the RFQ to consented vendors
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/outreach

# 5. (Optional) grant consent to a cold vendor, then re-send
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/vendors/<VENDOR_ID>/consent \
  -H 'Content-Type: application/json' \
  -d '{"consent": true, "source": "buyer_confirmed"}'
curl -X POST http://localhost:8000/api/cases/<CASE_ID>/outreach
```

### Example: inspect outreach status

```bash
# Per-vendor outreach status
curl http://localhost:8000/api/cases/<CASE_ID>/vendors

# Detailed per-vendor response rows (sent_at, status, last_error)
curl http://localhost:8000/api/cases/<CASE_ID>/responses
```

---

## 9. Simulating the full multi-turn flow locally

This script sends four simulated **Twilio** WhatsApp messages and prints the
case status. Requires `PROCESS_INLINE=true` and `SEARCH_PROVIDER=mock`.

```bash
python3 - <<'PY'
import urllib.parse, urllib.request

BASE = "http://localhost:8000"

def send(msg_sid, body):
    form = urllib.parse.urlencode({
        "MessageSid": msg_sid,
        "From": "whatsapp:+919999999999",
        "Body": body,
        "NumMedia": "0",
    })
    req = urllib.request.Request(
        f"{BASE}/webhooks/twilio/whatsapp",
        data=form.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
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
| `ALLOW_OUTREACH` | `true` | Global outreach kill-switch (per-vendor consent is the primary gate) |
| `OUTBOUND_RATE_DELAY_SECONDS` | `2.0` | Delay between outbound sends (rate limiting) |
| `MAX_OUTREACH_PER_BATCH` | `20` | Max sends per outreach run |
| `OUTREACH_CHANNEL` | `whatsapp` | Outbound channel (email is a stub) |
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

**Prototype (Twilio Sandbox):**

- [ ] Twilio account + activated WhatsApp Sandbox (join code for each tester).
- [ ] `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM` in `.env`.
- [ ] Public HTTPS endpoint for the webhook (ngrok in dev); set `APP_BASE_URL`.
- [ ] Twilio Sandbox webhook → `https://YOUR_HOST/webhooks/twilio/whatsapp` (POST).
- [ ] Sarvam API key (STT + structured chat) — optional, heuristic fallback works without it.
- [ ] Google Places API key + enable Places API (New) — only for real search.
- [ ] Run `make migrate` against the persistent database.

**Production (later):**

- [ ] Switch `WHATSAPP_PROVIDER=meta`; add the Meta Cloud API env vars (Section 5.5).
- [ ] Meta WhatsApp Business app + phone number (webhook, verify token, access
      token, app secret, phone number ID, graph version).
- [ ] PostgreSQL + Redis (for Docker / production) — or SQLite for dev.

After the prototype items are connected, the flow in Section 4 works
end-to-end on real WhatsApp via the Sandbox, including vendor outreach.
