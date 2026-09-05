/**
 * PAYSHIELD — browser Supabase client (Auth ONLY)
 *
 * This is the one legitimate exception to "the frontend never talks
 * to Supabase directly" — Supabase Auth is specifically designed to
 * be used from the browser with the public `anon` key, which carries
 * no data access on its own (Row Level Security, see
 * supabase/migrations/002_multi_tenancy_and_rls.sql, denies anon
 * access to every merchant-facing table by default). This client is
 * never used to query application data — only to sign in/up and read
 * the current session, whose access token is then sent to the FastAPI
 * backend as a Bearer header for it to verify server-side (see
 * core/auth.py).
 *
 * If NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY aren't
 * set, this returns null and the app falls back to demo mode (no
 * login required) rather than crashing - see useAuth.ts.
 */

import { createClient, SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null | undefined;

export function getSupabaseAuthClient(): SupabaseClient | null {
  if (client !== undefined) return client;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !anonKey) {
    client = null;
    return client;
  }

  client = createClient(url, anonKey, {
    auth: { persistSession: true, autoRefreshToken: true },
  });
  return client;
}

/** Auth is only actually required when BOTH the frontend opts in
 * (NEXT_PUBLIC_REQUIRE_AUTH=true) AND a Supabase Auth client could be
 * constructed. Defaults to false - today's existing, working demo
 * stays exactly as it is unless this is deliberately turned on. */
export const AUTH_REQUIRED = process.env.NEXT_PUBLIC_REQUIRE_AUTH === "true";
