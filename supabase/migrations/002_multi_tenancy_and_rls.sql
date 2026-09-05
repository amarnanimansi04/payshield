-- PAYSHIELD — multi-tenancy + Row Level Security migration
--
-- PURELY ADDITIVE. Nothing in this file drops, renames, or changes
-- the type/nullability of any existing column, and it does NOT touch
-- any existing primary key or unique constraint that the working
-- upsert logic (agent.py, aggregator.py) depends on for its
-- `on_conflict` targets. That was a deliberate choice: composite-key
-- merchant isolation at the constraint level (e.g. agent_state's PK
-- becoming (merchant_id, segment)) is the technically "complete"
-- version of this, but it changes conflict-resolution behavior on
-- every upsert in the codebase — real risk to touch under time
-- pressure, for a hackathon that runs a single demo merchant today.
-- Documented explicitly as a follow-up in README.md rather than done
-- silently. Every new column below has a DEFAULT, so every existing
-- row (and every existing INSERT/UPSERT statement that doesn't
-- mention merchant_id) continues to work completely unchanged.
--
-- Run this AFTER supabase/schema.sql, against the same project.
-- Idempotent — safe to re-run.

-- =====================================================================
-- MERCHANTS
-- =====================================================================

create table if not exists merchants (
  id text primary key,              -- e.g. 'merchant_demo_001'
  name text not null,
  created_at timestamptz not null default now()
);

-- Maps a Supabase Auth user to the one merchant they belong to. Kept
-- deliberately simple (one merchant per user) — the schema shape
-- (a separate mapping table, not a column on auth.users) is what
-- lets this grow into many-users-per-merchant or many-merchants-per-
-- user later without another migration.
create table if not exists merchant_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  merchant_id text not null references merchants(id),
  created_at timestamptz not null default now()
);

create index if not exists idx_merchant_users_merchant on merchant_users (merchant_id);

-- The one demo merchant every existing (and new, unless told
-- otherwise) row belongs to. This is what makes today's single-tenant
-- demo data valid multi-tenant data for free: it already belongs to
-- exactly one real merchant record.
insert into merchants (id, name)
values ('merchant_demo_001', 'PayShield Demo Merchant')
on conflict (id) do nothing;

-- =====================================================================
-- merchant_id on every merchant-facing table (all NOT NULL with a
-- DEFAULT, so existing rows backfill automatically and existing
-- INSERT/UPSERT statements that don't mention merchant_id keep working
-- exactly as they do today).
-- =====================================================================

alter table events add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_events_merchant on events (merchant_id);

alter table segment_windows add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_segment_windows_merchant on segment_windows (merchant_id);

alter table agent_state add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_agent_state_merchant on agent_state (merchant_id);

alter table agent_state_history add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_agent_state_history_merchant on agent_state_history (merchant_id);

alter table recovery_policy add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_recovery_policy_merchant on recovery_policy (merchant_id);

alter table alerts add column if not exists merchant_id text not null default 'merchant_demo_001' references merchants(id);
create index if not exists idx_alerts_merchant on alerts (merchant_id);

-- injected_anomalies / evaluation_runs are deliberately NOT given a
-- merchant_id: they belong to the evaluation harness (scripts/
-- evaluate.ts / app/evaluation/benchmarks.py), never queried per-
-- merchant by the live app, and were already structurally separate
-- from the merchant-facing tables above by design.

-- =====================================================================
-- ROW LEVEL SECURITY
--
-- The FastAPI backend authenticates to Supabase with the
-- service_role key, which BYPASSES RLS entirely, by design, on every
-- table, always — so nothing below changes the backend's own access
-- at all. What RLS adds is a real, independent safety net: if the
-- `anon` or an `authenticated` (logged-in, non-service-role) key were
-- ever used to query these tables directly, it would see ONLY the
-- merchant(s) that user is mapped to via merchant_users, and nothing
-- else — never all merchants' data, never nothing filtered.
-- =====================================================================

alter table events enable row level security;
alter table segment_windows enable row level security;
alter table agent_state enable row level security;
alter table agent_state_history enable row level security;
alter table recovery_policy enable row level security;
alter table alerts enable row level security;

-- No policy is created for the `anon` role on any table above — with
-- RLS enabled and zero matching policies, anon access is denied by
-- default. This is what "no anonymous access to merchant data" means
-- concretely in Postgres: absence of a permissive policy, not a
-- special denial rule.

-- Authenticated users may SELECT only rows belonging to a merchant
-- they're mapped to. No INSERT/UPDATE/DELETE policy is created for
-- `authenticated` on any of these tables: only the backend (via
-- service_role) ever writes to them, so authenticated users get
-- read-only access at the database level even if they queried these
-- tables directly.
-- Postgres has no `create policy if not exists` — DROP + CREATE is the
-- standard idempotent pattern (drop is a no-op if the policy doesn't exist).
drop policy if exists "merchant read own events" on events;
create policy "merchant read own events" on events
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));

drop policy if exists "merchant read own segment_windows" on segment_windows;
create policy "merchant read own segment_windows" on segment_windows
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));

drop policy if exists "merchant read own agent_state" on agent_state;
create policy "merchant read own agent_state" on agent_state
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));

drop policy if exists "merchant read own agent_state_history" on agent_state_history;
create policy "merchant read own agent_state_history" on agent_state_history
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));

drop policy if exists "merchant read own recovery_policy" on recovery_policy;
create policy "merchant read own recovery_policy" on recovery_policy
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));

drop policy if exists "merchant read own alerts" on alerts;
create policy "merchant read own alerts" on alerts
  for select to authenticated
  using (merchant_id in (select merchant_id from merchant_users where user_id = auth.uid()));
