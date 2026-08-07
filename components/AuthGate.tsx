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
    let active = true;
    const { data: listener } = client.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      setUser(session?.user ?? null);
      setLoading(false);
    });

    async function initializeSession() {
      const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ""));
      const accessToken = fragment.get("access_token");
      const refreshToken = fragment.get("refresh_token");
      const initialSession = await client!.auth.getSession();
      let session = initialSession.data.session;
      let sessionError: Error | null = initialSession.error;

      if (!session && accessToken && refreshToken) {
        const explicitSession = await client!.auth.setSession({
          access_token: accessToken,
          refresh_token: refreshToken,
        });
        session = explicitSession.data.session;
        sessionError = explicitSession.error;
      }

      if (!active) return;
      if (sessionError) setError(sessionError.message);
      setUser(session?.user ?? null);
      setLoading(false);
      if (session && window.location.hash) {
        window.history.replaceState(null, document.title, `${window.location.pathname}${window.location.search}`);
      }
    }

    void initializeSession();
    return () => {
      active = false;
      listener.subscription.unsubscribe();
    };
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
      options: { emailRedirectTo: window.location.origin },
    });
    if (authError) setError(authError.message);
    else setSent(true);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div className="eyebrow">ASTRONAUT PHOTOGRAPHY IDENTIFICATION REVIEW PORTAL</div>
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
