"use client";

import { useEffect, useState, useCallback } from "react";
import type { Session } from "@supabase/supabase-js";
import { getSupabaseAuthClient, AUTH_REQUIRED } from "./supabaseClient";

export interface AuthState {
  /** true while the initial session check is in flight */
  loading: boolean;
  session: Session | null;
  /** null if a Supabase Auth client couldn't be constructed (env vars
   * not set) - in that case auth is unavailable regardless of
   * AUTH_REQUIRED, and the caller should treat the app as demo-mode. */
  authAvailable: boolean;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signUp: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
}

export function useAuth(): AuthState {
  const client = getSupabaseAuthClient();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!client) {
      // No async system to synchronize with here — there's simply no
      // client, so there's no session that will ever resolve.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(false);
      return;
    }
    client.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: listener } = client.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
    });
    return () => listener.subscription.unsubscribe();
  }, [client]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (!client) return { error: "Auth is not configured for this deployment." };
      const { error } = await client.auth.signInWithPassword({ email, password });
      return { error: error?.message ?? null };
    },
    [client]
  );

  const signUp = useCallback(
    async (email: string, password: string) => {
      if (!client) return { error: "Auth is not configured for this deployment." };
      const { error } = await client.auth.signUp({ email, password });
      return { error: error?.message ?? null };
    },
    [client]
  );

  const signOut = useCallback(async () => {
    if (!client) return;
    await client.auth.signOut();
  }, [client]);

  return { loading, session, authAvailable: client !== null, signIn, signUp, signOut };
}

export { AUTH_REQUIRED };
