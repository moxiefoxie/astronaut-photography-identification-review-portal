"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { demoRun } from "@/lib/demo-data";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { AuthGate } from "./AuthGate";

export function LatestRunRedirect() {
  return <AuthGate>{() => <RedirectToLatestRun />}</AuthGate>;
}

function RedirectToLatestRun() {
  const router = useRouter();
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function openLatestRun() {
      const client = getSupabaseBrowserClient();
      if (!client) {
        router.replace(`/review/${demoRun.id}`);
        return;
      }

      const { data, error: runError } = await client
        .from("runs")
        .select("id")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();

      if (!active) return;
      if (runError) {
        setError(runError.message);
      } else if (data?.id) {
        router.replace(`/review/${data.id}`);
      } else {
        setError("No review runs have been published yet.");
      }
    }

    void openLatestRun();
    return () => { active = false; };
  }, [router]);

  return (
    <main className="centered latest-run-shell">
      {error ? (
        <>
          <h1>Nothing to review yet</h1>
          <p className="error">{error}</p>
          <Link className="primary button-link" href="/runs">View run archive</Link>
        </>
      ) : (
        <><div className="spinner" />Opening the newest review run…</>
      )}
    </main>
  );
}
