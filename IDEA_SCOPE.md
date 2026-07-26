# IDEA_SCOPE — Boli

## One-sentence product

Boli is a WhatsApp-first procurement agent that turns an SMB's multilingual text or voice requirement into a structured sourcing case, discovers relevant vendors, makes their future bids comparable, and prepares the final commercial document for authorised human approval.

## Product thesis

SMB buyers should not need to learn a procurement application before they can procure something. WhatsApp is the interaction layer; a canonical procurement case is the system of record.

The system may accept broad products, recurring services, and projects, but its intelligence is explicit:

- Fully supported categories use a reviewed category pack.
- Partially supported categories receive generic sourcing and buyer-defined comparison criteria.
- High-risk or unfamiliar categories remain research-only.

## User

An owner, operator, finance manager, or administration manager at an Indian SMB who currently searches, calls, messages, and compares vendors manually.

## Repeated job

Convert an informal business need into a defensible vendor decision without chasing multiple suppliers across calls, WhatsApp messages, websites, PDFs, and spreadsheets.

## Interaction channel

WhatsApp is the primary buyer interface for:

- Requirement intake.
- Voice notes.
- Clarification.
- Search commands.
- Shortlisting.
- Approval.
- Status updates.

An internal web view exists only for auditability and complex comparisons, not as the buyer's required daily workflow.

## Hard input

A multilingual or code-mixed request containing incomplete specifications, business names, quantities, locations, budgets, deadlines, and informal commercial preferences.

Later stages add heterogeneous vendor responses: voice notes, text messages, PDFs, and quotation images.

## Final state by product stage

### Current vertical slice

A structured procurement case and a local vendor shortlist returned in WhatsApp.

### Hackathon-complete target

An approved vendor selection and a source-grounded agreement draft ready for authorised signature.

### Long-term target

A complete procurement case with RFQ, vendor evidence, comparable bids, approval, PO/agreement, performance history, and renewal context.

## Sarvam parameter

Voice Experience.

Sarvam is load-bearing for messy Indian-language and code-mixed voice intake, names, numbers, corrections, and concise follow-up questions.

## Supporting capabilities

- Sarvam structured chat output for canonical procurement intake.
- Google Places for local discovery.
- Later: general web search for national suppliers.
- Later: document extraction for quotation PDFs.
- Later: e-sign provider for authorised execution.

## Creativity thesis

One commercial truth across many informal conversations. The agent does not merely summarize vendor messages; it maps each statement to a canonical requirement and marks it sourced, missing, contradicted, or inferred.

## Delight thesis

The buyer speaks naturally, receives only the most useful clarification, and later sees hidden exclusions before approving an apparently cheap quotation.

## Memory boundary

Boli may remember:

- Company profile and locations.
- Procurement policies.
- Approved and rejected vendors.
- Corrections and buyer preferences.
- Previous procurement cases and contract decisions.

Boli must not infer authority to approve, sign, spend, or negotiate beyond explicit policy and per-case human confirmation.

## Current MVP requirements

- WhatsApp text and voice-note intake.
- Webhook authenticity and idempotency.
- Sarvam transcription and structured extraction.
- Persistent procurement-case state.
- One clarification at a time.
- Dynamic location and category search query.
- Google Places and mock search adapters.
- Shortlist returned in WhatsApp.
- Human-visible failure messages.

## Current non-goals

- Autonomous negotiation.
- Automatic vendor outreach.
- Binding contract formation.
- Payments or credit.
- Accounting integration.
- Permanent scraping of Maps or directory data.
- Claims that discovered vendors are verified or qualified.
- Reliable comparison of arbitrary categories without a category pack.

## Demo proof

1. A buyer sends a Hindi/Hinglish text or voice requirement.
2. The agent preserves names, quantities, and location.
3. It notices one missing commercial field and asks a concise question.
4. The buyer corrects or completes the requirement.
5. The agent searches for local vendors.
6. It returns a clear shortlist through WhatsApp.
7. The canonical case is visible through the case API.

## Next single action

Connect a Meta test number and run the supplied sample procurement request through a real WhatsApp webhook, first with mock search and then Google Places.
