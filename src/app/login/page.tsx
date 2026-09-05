"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/useAuth";

export default function LoginPage() {
  const router = useRouter();
  const { session, loading, authAvailable, signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setSubmitting(true);
    try {
      if (mode === "signin") {
        const { error } = await signIn(email, password);
        if (error) setError(error);
        else router.replace("/");
      } else {
        const { error } = await signUp(email, password);
        if (error) setError(error);
        else setInfo("Account created. If email confirmation is enabled on this project, check your inbox before signing in.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--text-tertiary)]">
        Loading…
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-sm flex-col justify-center px-4">
      <div className="mb-6 text-center">
        <h1 className="font-display text-xl font-semibold text-[var(--text-primary)]">PayShield</h1>
        <p className="mt-1 text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
          Autonomous Payment Reliability Agent
        </p>
      </div>

      {!authAvailable ? (
        <div className="card px-5 py-5 text-center text-sm text-[var(--text-secondary)]">
          Authentication isn&apos;t configured for this deployment (missing
          NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY). The dashboard is
          reachable directly at{" "}
          <Link href="/" className="text-[var(--flow-teal)] underline">
            the home page
          </Link>
          .
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="card space-y-3 px-5 py-5">
          <div className="flex gap-1 text-xs">
            <button
              type="button"
              onClick={() => setMode("signin")}
              className={`rounded px-2 py-1 ${mode === "signin" ? "bg-[var(--ink-800)] text-[var(--text-primary)]" : "text-[var(--text-tertiary)]"}`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => setMode("signup")}
              className={`rounded px-2 py-1 ${mode === "signup" ? "bg-[var(--ink-800)] text-[var(--text-primary)]" : "text-[var(--text-tertiary)]"}`}
            >
              Sign up
            </button>
          </div>

          <label className="block text-xs text-[var(--text-tertiary)]">
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border hairline bg-[var(--ink-900)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--flow-teal-dim)]"
              autoComplete="email"
            />
          </label>

          <label className="block text-xs text-[var(--text-tertiary)]">
            Password
            <input
              type="password"
              required
              minLength={6}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border hairline bg-[var(--ink-900)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-[var(--flow-teal-dim)]"
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
            />
          </label>

          {error && <p className="text-xs text-[var(--flow-coral)]">{error}</p>}
          {info && <p className="text-xs text-[var(--flow-teal)]">{info}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded border hairline-strong bg-[var(--ink-800)] px-3 py-2 text-sm font-medium text-[var(--text-primary)] transition-colors hover:border-[var(--flow-teal-dim)] disabled:opacity-50"
          >
            {submitting ? "Working…" : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>
      )}

      <p className="mt-4 text-center text-xs text-[var(--text-tertiary)]">
        Merchant-isolated data, secured by Supabase Auth + Row Level Security.
      </p>
    </div>
  );
}
