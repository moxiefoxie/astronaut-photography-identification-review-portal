"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Run } from "@/lib/types";
import { demoRun } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";

export function PortalNav({ currentRunId, userEmail }: { currentRunId?: string; userEmail?: string | null }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setRuns([demoRun]);
      return;
    }

    let active = true;
    const loadRuns = async () => {
      const { data } = await client.from("runs").select("*").order("created_at", { ascending: false });
      if (active) setRuns((data as Run[]) ?? []);
    };
    void loadRuns();

    const channel = client.channel("portal-run-menu").on(
      "postgres_changes",
      { event: "*", schema: "public", table: "runs" },
      () => { void loadRuns(); },
    ).subscribe();

    return () => {
      active = false;
      void client.removeChannel(channel);
    };
  }, []);

  async function signOut() {
    const client = getSupabaseBrowserClient();
    if (!client) return;
    await client.auth.signOut();
    router.replace("/");
  }

  return (
    <nav className="portal-nav" aria-label="Review portal">
      <Link className="portal-brand" href="/">
        <span>NASA</span>
        <strong>Astronaut Photography Identification Review Portal</strong>
      </Link>
      <div className="portal-nav-actions">
        <label className="run-menu">
          <span>Run</span>
          <select
            aria-label="Select review run"
            value={currentRunId ?? ""}
            onChange={(event) => event.target.value && router.push(`/review/${event.target.value}`)}
          >
            {!currentRunId && <option value="">Choose a run</option>}
            {runs.map((run, index) => (
              <option key={run.id} value={run.id}>{index === 0 ? "Latest · " : ""}{run.name}</option>
            ))}
          </select>
        </label>
        <Link className={`portal-link ${pathname === "/catalog" ? "active" : ""}`} href="/catalog">AI catalog</Link>
        <Link className={`portal-link ${pathname === "/gallery" ? "active" : ""}`} href="/gallery">My tags</Link>
        <Link className={`portal-link ${pathname === "/runs" ? "active" : ""}`} href="/runs">All runs</Link>
        {userEmail && <button className="portal-signout" title={`Signed in as ${userEmail}`} onClick={signOut}>Sign out</button>}
      </div>
    </nav>
  );
}
