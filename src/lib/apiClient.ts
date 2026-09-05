/**
 * PAYSHIELD — API client
 *
 * The dashboard talks to the FastAPI backend (backend/app/main.py)
 * directly — the intelligence engine, decision engine, recovery-policy
 * layer, and webhook ingestion all live in Python. `NEXT_PUBLIC_API_BASE_URL`
 * and `NEXT_PUBLIC_SUPABASE_*` are the only "public" env vars this
 * project has — none of them are secrets (the anon key is designed to
 * be public; RLS is what actually protects data - see
 * supabase/migrations/002_multi_tenancy_and_rls.sql). The FastAPI
 * backend holds every real secret server-side.
 */

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

/** Attaches a Bearer token when one is provided (i.e. when auth is in
 * use) - a no-op extra header when it isn't, so every existing fetch
 * call keeps working unchanged in demo mode (no session, no token). */
export function authHeaders(accessToken: string | null | undefined): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}
