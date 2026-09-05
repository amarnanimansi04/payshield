# PAYSHIELD — Project Brief for Cowork

> **Migration note:** this document predates a backend migration from
> Next.js API routes (TypeScript) to a FastAPI backend (Python). The
> product concept, architecture shape, and every design decision below
> are UNCHANGED — only the implementation language of the backend
> moved. See [`README.md`](./README.md) for the current, accurate
> file-by-file map. Where this document says `src/lib/detector.ts`,
> read it as `backend/app/services/detector.py`, and so on for the rest
> of the former `src/lib/*` modules.

Read this file first, in full, before doing anything else in this project.

## What this is

PayShield is a submission for the Razorpay AI Buildathon, Track 3 (AI Revenue
Recovery). It is a **payment degradation intelligence and response-policy
agent**: instead of diagnosing and recovering one failed payment at a time
(which is what every "obvious" idea in this track does, and what Razorpay's
own shipped Agent Studio already does), PayShield aggregates failures across a
rolling time window per payment segment (method, and bank for netbanking),
statistically detects when a segment is deviating from *its own* recent
baseline (not a fixed global threshold), diagnoses the likely cause using
Razorpay's real `error_source` field, and automatically suppresses its own
downstream recovery actions for that segment while alerting the merchant —
then automatically resumes normal behavior once the segment recovers.

**One-line pitch:** "When a bank has a bad twenty minutes, PayShield is the one
system that notices it's one problem, not fifty."

## How we got here (read this to understand WHY, not just WHAT)

This idea went through four full rounds of research and adversarial
red-teaming before any code was written. Each round found something real and
changed the design. This matters because if you (Cowork) are asked to modify
something, you need to know WHY it's shaped the way it is, not just what it
currently does.

1. **Track selection round:** compared all 5 Razorpay Buildathon tracks.
   Chose Track 3 (AI Revenue Recovery) over Track 2 (AI Risk Manager) and
   others because it had the best combination of real, provable pain (NPCI
   data on UPI AutoPay failure rates), Razorpay relevance, and differentiation
   ceiling.

2. **First project idea ("Mauka"):** predict the best time to retry a failed
   subscription payment. Red-teamed and found this doesn't work: (a)
   Razorpay's Subscriptions API only supports a fixed T+3-day daily retry —
   there's no API to set a custom retry timestamp, so the core mechanism was
   technically infeasible; (b) "smart payment retry" is an established,
   crowded global SaaS category (Zuora Smart Retry, FlyCode, Redux); (c) most
   damningly, Razorpay's own Agent Studio **already ships** a "Subscription
   Recovery Agent" whose own marketing copy says it "analyzes failed
   payments, applies smarter retry logic, and triggers targeted customer
   nudges... retries at optimal times." Building this would be building a
   worse clone of Razorpay's own shipped product.

3. **Pivot to PayShield (payment degradation detection):** instead of
   individual-payment recovery, detect when MANY individual failures are
   actually ONE systemic problem (a bank/route/method degrading). This is a
   different unit of analysis (a population of transactions over a window,
   not a single payment) with no evidence Razorpay has shipped anything like
   it. Verified this survives every attack: novelty, Razorpay overlap,
   technical feasibility, "isn't this just an if/else engine," "why not just
   a threshold."

4. **Final technical audit + freeze:** verified real Razorpay Payment API
   fields (method, bank, error_source, error_code are REAL and reliable;
   BIN/gateway/route are NOT exposed to merchants; UPI bank identity is only
   inferable from the VPA suffix, which is a real field but OUR inference,
   not Razorpay's). Corrected two overreach claims: PayShield CANNOT pause
   Razorpay's native retry engine (no API for that) and CANNOT force a
   payment method switch (no API for that either) — it can only suppress ITS
   OWN downstream actions and alert the merchant, which is real and honest.
   Removed Payment Links from the core build (30/business test-mode cap,
   unnecessary complexity) — kept only as a one-sentence future-capability
   mention.

**The single most important discipline running through this whole project:**
every claim must be honestly tagged as REAL (a genuine Razorpay API field),
DERIVED (computed by us from real fields, zero invention), INFERRED (derived,
but involves a judgment call — must be disclosed wherever shown), or
SYNTHETIC (data we generated for evaluation, structurally kept OUT of the
live detector's input, only joined against results afterward to score
precision/recall). Do not blur these categories when adding anything new.

## What's already built and verified (as of this handoff)

All of the following live in this folder and have been type-checked, built,
and run successfully:

- `src/lib/types.ts` — the full data model, every field tagged per the
  provenance system above.
- `src/lib/segment.ts` — segmentation logic. Groups by `method` (and
  `method:bank` for netbanking) because that's the only grouping that has a
  real denominator (both successful and failed payments carry `method`;
  `error_source` only exists on failures, so it CANNOT be part of the
  grouping key — it's computed as a distribution WITHIN a flagged segment
  instead, for the "Why panel").
- `src/lib/detector.ts` — EWMA adaptive baseline + sample-size-aware z-score
  significance test + a normal-CDF-derived statistical significance score
  (never called "confidence") + a disclosed linear
  trend projection. **Verified against a hand-built two-segment scenario**
  (`scripts/verify-detector.ts`) proving it correctly flags a real anomaly in
  a high-volume segment while correctly ignoring ordinary noise in a
  low-volume segment — and that two naive threshold baselines get this wrong.
- `src/lib/decisionEngine.ts` — the five-case state machine (NORMAL /
  MONITORING / ISOLATED_DEGRADATION / SYSTEMIC_DEGRADATION / ESCALATED /
  RESOLVED), fully deterministic, with anti-flapping hysteresis on
  resolution. **All 5 cases verified** in `scripts/verify-decision-engine.ts`.
- `src/lib/generator.ts` — synthetic batch generator, grounded in Razorpay's
  REAL documented error taxonomy (`RAZORPAY_ERROR_TAXONOMY`), producing BOTH
  `captured` and `failed` events so rates have a genuine denominator.
  Verified in `scripts/verify-generator.ts`.
- `src/lib/webhookSignature.ts` — HMAC signature verification with
  constant-time comparison. Verified in `scripts/verify-webhook-signature.ts`.
- `src/lib/aggregate.ts` — bridges real Supabase event rows into the
  detector's input format, computing TRUE rates (failures / total attempts),
  not an approximation.
- `src/lib/alertText.ts` — the ONLY place an LLM (Groq) is called anywhere in
  this codebase, strictly for generating human-readable alert text
  downstream of a decision already made deterministically. Has a templated
  fallback if the Groq call fails or isn't configured.
- `src/lib/agentLoop.ts` — the full orchestration: aggregate → detect →
  decide → act → write state → audit log → (conditionally) generate alert.
- `src/app/api/webhooks/razorpay/route.ts` — event-driven webhook ingestion
  with signature verification and idempotent upsert.
- `src/app/api/cron/detect/route.ts` — the scheduled detection-cycle
  endpoint, secret-protected, called by GitHub Actions.
- `src/app/api/dashboard/state/route.ts` — read-only endpoint the dashboard
  polls (browser never touches Supabase directly — no anon key used
  anywhere).
- `src/app/page.tsx` + `src/components/*` — the three-section dashboard
  (top health strip with a live "flow line" sparkline per segment, middle
  hero alert card with the Why panel and blast radius, bottom audit log +
  evaluation tabs). Dark control-room visual design, deliberately NOT a
  generic SaaS card kit — see the design tokens in `src/app/globals.css`.
  Uses system font stacks (no Google Fonts network dependency, to avoid
  demo-day fragility).
- `supabase/schema.sql` — the full schema, with evaluation tables
  (`injected_anomalies`, `evaluation_runs`) structurally separated from the
  live pipeline tables.
- `.github/workflows/detect-cycle.yml` — the GitHub Actions scheduler
  (5-minute cadence, manually triggerable for demo pacing).
- `scripts/evaluate.ts` — the evaluation harness: runs a 13-scenario battery
  (magnitude sweep, duration sweep, sample-size sweep, false-positive control
  periods on both high- and low-volume segments) with KNOWN injected ground
  truth, scores PayShield against three naive baselines (global fixed
  threshold, global rolling average, segment fixed threshold), and
  best-effort writes a summary row to Supabase if configured.

Run `npm run verify:all` to re-confirm all four core modules still pass.
Run `npm run evaluate` to run the full evaluation battery (works standalone,
no Supabase required, though it'll write results there if configured).

## What is NOT done yet — this is where you (Cowork) come in

1. **Phase 0 (manual, on the human's Razorpay account, not yet done as of
   this handoff):** log into the Razorpay test-mode Dashboard and check
   whether a SPECIFIC `error_code`/decline reason can be forced when
   simulating a failed payment, or only generic pass/fail. This determines
   whether the live pipeline uses real Razorpay-triggered test transactions
   or the `generator.ts` "constructed" mode (which is already built and
   ready either way — nothing is blocked on this, but the human needs to do
   this check and report back what they found).
2. **Supabase project setup:** create a free Supabase project, run
   `supabase/schema.sql` against it, and set `SUPABASE_URL` +
   `SUPABASE_SERVICE_ROLE_KEY` as environment variables (see
   `.env.example` — create this file if it doesn't exist yet, listing every
   required env var: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`,
   `GROQ_API_KEY`, `CRON_SECRET`).
3. **Razorpay test account setup:** create test-mode API keys, register the
   webhook (once deployed to Vercel, since Razorpay rejects localhost URLs)
   subscribed to `payment.failed` and `payment.captured`.
4. **Manual webhook verification procedure** (do this by hand once, exactly
   as described, before trusting the automated pipeline) — create a test
   payment, force a failure, confirm the webhook arrives, inspect the raw
   payload fields with your own eyes, confirm signature verification works,
   confirm the row lands correctly in Supabase.
5. **Deployment to Vercel** — connect this repo, set all env vars, deploy,
   get the live URL, wire that URL into the GitHub Actions workflow's
   `PAYSHIELD_APP_URL` secret (along with `CRON_SECRET` matching what's set on
   Vercel).
6. **Demo rehearsal** — walk through the exact 5-minute script in the frozen
   spec (setup → normal activity → inject anomaly → detection moment →
   explain why → take action → evaluation/proof), timed, with a recorded
   backup in case live demo has issues.
7. **A proper README.md** for the repo root (currently doesn't exist) —
   should explain the project, link to this brief for the full rationale,
   and give quick-start instructions.

## Ground rules for anything you build or change from here

- **Never blur REAL / DERIVED / INFERRED / SYNTHETIC.** If you add a new
  field or data source, tag it immediately, in code comments, the way
  everything else in this codebase already is.
- **Never let synthetic evaluation ground truth (injected_anomalies table,
  or any "did we inject an anomaly here" flag) leak into the live detector's
  input.** It's read only by `scripts/evaluate.ts`, after the fact.
- **The LLM (Groq) only ever generates alert TEXT, downstream of a decision
  already made by deterministic/statistical code.** Never let an LLM call
  decide whether an action fires, what action fires, or touch any financial
  API directly. This is a hard rule, not a style preference — it's the
  answer to a specific, anticipated judge question ("where does the LLM
  never have authority").
- **Do not re-introduce Payment Links as a core mechanism.** It's
  deliberately excluded from the MVP (30/business test-mode cap, and PayShield
  was never going to be able to force a method switch anyway) — mentioned
  only as a one-sentence future capability in the pitch.
- **Do not claim PayShield controls Razorpay's native retry engine or a
  customer's payment method choice.** Both are explicitly, verifiably false
  claims — no such API exists. PayShield suppresses its OWN downstream
  actions and alerts the merchant. That's the real, honest capability set.
- **₹0 budget is a hard constraint.** Every service in this stack (Vercel,
  Supabase, GitHub Actions, Groq, Razorpay test mode) has been verified
  free-tier, no-credit-card-required. Do not introduce a paid dependency
  without flagging it explicitly and finding a free alternative first.
- **Run `npm run verify:all` after any change to `src/lib/detector.ts`,
  `decisionEngine.ts`, or `generator.ts`.** These three are the highest-risk
  modules and were built and verified in that order, before any surrounding
  infrastructure, specifically so a regression would be caught immediately.
- **Keep the architecture simple.** No new microservices, no message
  queues, no Kubernetes. This is a hackathon build on a free-tier stack —
  the existing Vercel + Supabase + GitHub Actions shape was deliberately
  chosen over more "production-grade" options because it's the simplest
  thing that actually works for a live demo.

## If you get stuck or something doesn't match this brief

Ask the human. Do not silently invent a new claim, a new data source, or a
new architectural piece without checking it against the discipline above
first — the entire value of this project, across four rounds of review, has
been catching exactly this kind of scope creep before it reached the demo.
