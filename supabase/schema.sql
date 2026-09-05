-- PAYSHIELD — Supabase schema
--
-- Matches the frozen spec's Part 10 data architecture, hardened per
-- the P0 engineering pass. Note the explicit separation between the
-- live pipeline tables below and `injected_anomalies` (SYNTHETIC,
-- evaluation-only, never read by the live detector code path — only
-- joined against results afterward by the eval harness).
--
-- This file is the single source of truth for the schema — run it
-- against a fresh Supabase project (SQL editor, or `psql`) to recreate
-- everything from scratch. Idempotent: every statement uses
-- `if not exists` / `on conflict` so re-running it is safe.

create extension if not exists "uuid-ossp";

-- Normalized events. One row per payment.failed OR payment.captured
-- webhook (or constructed-mode synthetic record — see `source`
-- column). We ingest BOTH outcomes because a failure count alone has
-- no denominator; captured events are what make `current_rate` a
-- real ratio instead of an approximation.
create table if not exists events (
  event_id uuid primary key default uuid_generate_v4(),           -- DERIVED
  payment_id text not null,                                       -- REAL
  outcome text not null check (outcome in ('failed', 'captured')), -- DERIVED
  created_at timestamptz not null,                                -- REAL
  method text not null,                                           -- REAL
  bank text,                                                      -- REAL
  vpa text,                                                       -- REAL
  vpa_derived_bank_label text,                                    -- INFERRED
  card_issuer text,                                                -- REAL
  card_network text,                                              -- REAL
  error_code text,                                                -- REAL
  error_source text,                                              -- REAL
  error_reason text,                                              -- REAL
  error_step text,                                                -- REAL
  amount integer not null,                                        -- REAL (paise)
  segment_primary text not null,                                  -- DERIVED (method:error_source)
  segment_bank text,                                               -- REAL (netbanking only)
  ingested_at timestamptz not null default now(),                 -- DERIVED
  source text not null check (source in ('live', 'constructed')), -- DERIVED, disclosed
  razorpay_event_id text                                          -- REAL, x-razorpay-event-id
  -- header value, when present. Nullable because constructed/demo
  -- events have no real Razorpay delivery to key off of.
);

create index if not exists idx_events_segment_created
  on events (segment_primary, created_at desc);

-- Idempotency, layer 1: a given Razorpay webhook DELIVERY should only
-- ever be processed once, even on retry (Razorpay retries on any
-- non-2xx response). This is the PRIMARY idempotency key (frozen spec
-- P0 #8) — a real Razorpay event carries a unique x-razorpay-event-id
-- per delivery attempt group.
create unique index if not exists idx_events_razorpay_event_id_unique
  on events (razorpay_event_id) where razorpay_event_id is not null;

-- Idempotency, layer 2 (defense in depth, kept per the spec's "don't
-- remove useful payment-level safeguards"): a given (payment_id,
-- outcome) pair should only ever produce one stored row. This also
-- catches constructed/demo events (which have no razorpay_event_id)
-- and any real event whose event-id header was somehow missing.
create unique index if not exists idx_events_payment_outcome_unique
  on events (payment_id, outcome);

-- Per-segment, per-CANONICAL-window aggregation (frozen spec P0 #2 —
-- every completed detection window gets persisted here, not just kept
-- in memory). Written by the scheduled detection cycle (GitHub
-- Actions -> Vercel endpoint), read by the detector and the dashboard.
-- `current_rate` is NULL for an empty window (zero attempts) — an
-- empty window is NO DATA, never a silent 0%/"healthy" reading
-- (frozen spec P0 #5).
create table if not exists segment_windows (
  id uuid primary key default uuid_generate_v4(),
  segment text not null,
  window_start timestamptz not null,   -- canonical, epoch-aligned
  window_end timestamptz not null,     -- canonical, epoch-aligned
  event_count integer not null,        -- total attempts (captured + failed)
  failure_count integer not null,
  current_rate double precision,       -- null = no data, NOT 0%
  total_transaction_amount bigint not null,   -- paise, ALL attempts —
  -- the correct denominator for any "% of total system volume" metric
  failed_transaction_amount bigint not null,  -- paise, failed only
  baseline_rate double precision,      -- [DERIVED/STATISTICAL] EWMA
  -- baseline this window was tested against, set only for the window
  -- actually evaluated by the detector that cycle
  sigma_deviation double precision,    -- [DERIVED/STATISTICAL]
  is_significant boolean,              -- [DERIVED/STATISTICAL]
  data_source text not null default 'live' check (data_source in ('live', 'constructed', 'mixed')),
  created_at timestamptz not null default now()
);

create index if not exists idx_segment_windows_segment_time
  on segment_windows (segment, window_end desc);

-- Idempotency: re-running the detection cycle on unchanged data
-- upserts the SAME row for a given (segment, window_end), never a
-- duplicate (frozen spec P0 #2/#6).
create unique index if not exists idx_segment_windows_segment_window_unique
  on segment_windows (segment, window_end);

-- Agent state per segment — the single source of truth the dashboard
-- reads, and the root of the audit trail (agent_state_history)
-- required by the frozen spec's security/audit requirements.
-- `last_window_end` is the idempotency guard (frozen spec P0 #6):
-- if a new detection cycle's window_end for this segment matches this
-- value, nothing new has happened and the cycle skips re-processing.
create table if not exists agent_state (
  segment text primary key,
  state text not null check (
    state in ('NORMAL','MONITORING','ISOLATED_DEGRADATION','SYSTEMIC_DEGRADATION','ESCALATED','RESOLVED')
  ),
  entered_at timestamptz not null,
  consecutive_significant_windows integer not null default 0,
  good_streak integer not null default 0,
  evidence jsonb,
  action_taken text[] not null default '{}',
  last_window_end timestamptz,
  updated_at timestamptz not null default now()
);

-- Full audit trail: every cycle a segment is actually (re-)evaluated
-- (i.e. NOT skipped by the idempotency guard) appends one row here,
-- including the previous state, the reasoning, and the canonical
-- window as a correlation ID — so "why did the agent do this" always
-- has a deterministic, inspectable answer (frozen spec P0 #18).
create table if not exists agent_state_history (
  id uuid primary key default uuid_generate_v4(),
  segment text not null,
  state text not null,
  previous_state text,
  case_classification text not null,
  reasoning text not null,
  actions text[] not null default '{}',
  detection jsonb,
  window_end timestamptz,   -- correlation ID: which canonical window
  -- this decision was based on
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_state_history_segment_time
  on agent_state_history (segment, created_at desc);

-- PayShield's OWN internal recovery-policy layer (frozen spec P0 #3).
-- This does NOT control, pause, or touch Razorpay's native retry
-- engine — no such API exists. It tracks whether PAYSHIELD's own
-- downstream recovery workflow is currently allowed to initiate for a
-- given segment. One row per segment, always current; the "before /
-- after" story a judge can be shown lives in this table's `updated_at`
-- / `reason` plus the corresponding agent_state_history rows.
create table if not exists recovery_policy (
  segment text primary key,
  status text not null check (status in ('ACTIVE', 'SUPPRESSED')),
  reason text not null,
  related_state text not null,
  updated_at timestamptz not null default now()
);

-- Merchant-facing alerts (the LLM-generated text lives here — see
-- frozen spec Part 6/14: the LLM is invoked ONLY here, only on a new
-- state transition, never in the detection/decision path). `source`
-- discloses whether the text came from the LLM or the deterministic
-- template fallback (used if Groq isn't configured or the call fails).
create table if not exists alerts (
  id uuid primary key default uuid_generate_v4(),
  segment text not null,
  state text not null,
  alert_text text not null,
  source text not null default 'template' check (source in ('llm', 'template')),
  blast_radius jsonb not null,
  created_at timestamptz not null default now()
);

-- =====================================================================
-- EVALUATION-ONLY tables. Structurally separate from the tables above.
-- The live detector/decision-engine code path (src/lib/detector.ts,
-- src/lib/decisionEngine.ts) must NEVER query these tables. They are
-- only read by scripts/evaluate.ts, which joins them against
-- detection results AFTER THE FACT to compute precision/recall.
-- =====================================================================

create table if not exists injected_anomalies (
  id uuid primary key default uuid_generate_v4(),
  batch_label text not null,          -- e.g. "eval_run_2026_09_01_A"
  segment text not null,
  window_start timestamptz not null,
  window_end timestamptz not null,
  magnitude text not null check (magnitude in ('none','small','medium','large')),
  note text,
  created_at timestamptz not null default now()
);

create table if not exists evaluation_runs (
  id uuid primary key default uuid_generate_v4(),
  batch_label text not null,
  precision double precision,
  recall double precision,
  f1 double precision,
  false_positive_rate double precision,
  mean_time_to_detection_seconds double precision,
  baseline_a_correct boolean,
  baseline_b_correct boolean,
  baseline_c_correct boolean,
  payshield_correct boolean,
  notes text,
  created_at timestamptz not null default now()
);
