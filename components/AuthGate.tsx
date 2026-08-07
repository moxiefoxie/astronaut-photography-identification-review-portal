"use client";

import { useEffect, useState } from "react";
import type { User } from "@supabase/supabase-js";
import { getSupabaseBrowserClient, isSupabaseConfigured } from "@/lib/supabase/client";

export function AuthGate({ children }: { children: (user: User | null) => React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setLoading(false);
      return;
    }
    client.auth.getUser().then(({ data }) => {
      setUser(data.user);
      setLoading(false);
    });
    const { data } = client.auth.onAuthStateChange((_event, session) => setUser(session?.user ?? null));
    return () => data.subscription.unsubscribe();
  }, []);

  if (loading) return <main className="centered"><div className="spinner" />Loading review workspace…</main>;
  if (!isSupabaseConfigured()) return <>{children(null)}</>;
  if (user) return <>{children(user)}</>;

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const client = getSupabaseBrowserClient()!;
    const { error: authError } = await client.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: window.location.href },
    });
    if (authError) setError(authError.message);
    else setSent(true);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="eyebrow">NASA OCEAN REVIEW</div>
        <h1>Review Earth from orbit.</h1>
        <p>Sign in with your team email. We’ll send a secure magic link—no password required.</p>
        {sent ? (
          <div className="success-box">Check your inbox for the sign-in link.</div>
        ) : (
          <form onSubmit={signIn}>
            <label htmlFor="email">Email address</label>
            <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@organization.org" />
            <button className="primary" type="submit">Send magic link</button>
            {error && <p className="error">{error}</p>}
          </form>
        )}
      </section>
    </main>
  );
}
