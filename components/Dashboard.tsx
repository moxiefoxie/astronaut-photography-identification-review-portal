"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Run } from "@/lib/types";
import { demoRun } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { AuthGate } from "./AuthGate";
import { PortalNav } from "./PortalNav";

export function Dashboard() {
  return <AuthGate>{(user) => <DashboardContent userEmail={user?.email ?? null} />}</AuthGate>;
}

function DashboardContent({ userEmail }: { userEmail: string | null }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setRuns([demoRun]);
      setLoading(false);
      return;
    }
    client.from("runs").select("*").order("created_at", { ascending: false }).then(({ data }) => {
      setRuns((data as Run[]) ?? []);
      setLoading(false);
    });
    const channel = client.channel("runs-dashboard").on(
      "postgres_changes",
      { event: "*", schema: "public", table: "runs" },
      () => client.from("runs").select("*").order("created_at", { ascending: false }).then(({ data }) => setRuns((data as Run[]) ?? [])),
    ).subscribe();
    return () => { client.removeChannel(channel); };
  }, []);

  return (
    <main className="dashboard-shell">
      <PortalNav userEmail={userEmail} />
      <header className="topbar">
        <div><div className="eyebrow">ASTRONAUT PHOTOGRAPHY IDENTIFICATION REVIEW PORTAL</div><h1>Review runs</h1></div>
        <div className="topbar-actions">
          {!getSupabaseBrowserClient() && <span className="demo-pill">Local demo</span>}
        </div>
      </header>
      <section className="dashboard-intro">
        <h2>Find the signal in a living image stream.</h2>
        <p>Open a run while inference is still producing results. New candidates appear here in real time.</p>
      </section>
      {loading ? <div className="spinner" /> : (
        <section className="run-grid">
          {runs.map((run) => {
            const denominator = run.expected_count || run.processed_count || 1;
            const progress = Math.min(100, Math.round((run.processed_count / denominator) * 100));
            return (
              <article className="run-card" key={run.id}>
                <div className="run-card-head"><span className={`status ${run.status}`}>{run.status}</span><span>{new Date(run.created_at).toLocaleDateString()}</span></div>
                <h3>{run.name}</h3><p>{run.description}</p>
                <div className="progress"><i style={{ width: `${progress}%` }} /></div>
                <div className="run-stats"><strong>{run.inserted_count.toLocaleString()}</strong> candidates <span>{progress}% processed</span></div>
                <Link className="primary button-link" href={`/review/${run.id}`}>Open review</Link>
              </article>
            );
          })}
        </section>
      )}
    </main>
  );
}
