"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, networkApi, PiApiError } from "@/lib/api";

// The Moo is on its own wifi. The parent either sets up the home wifi, or keeps
// using the Moo here without internet, with this computer's Moo Local doing the AI.
export default function NetworkPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = useQuery({ queryKey: ["network"], queryFn: networkApi.get, refetchInterval: 8000 });

  const goOffline = async () => {
    setBusy(true); setError(null);
    try {
      await networkApi.setOffline(true);
      const status = await api.status();
      router.replace(status.configured ? "/dashboard" : "/ai");
    } catch (e) {
      setError(e instanceof PiApiError ? e.message : "Could not switch to using it without internet.");
      setBusy(false);
    }
  };

  return (
    <StepLayout title="How should Moo connect?" subtitle="Moo is on its own wifi right now." back="/">
      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">Use Moo without internet</h2>
        <p className="text-sm text-moo-ink/70">
          Stay on Moo&apos;s wifi. Moo Local on this computer tells the stories, listens and speaks, so nothing needs the internet.
          The computer stops asking to join another wifi.
        </p>
        {state.isPending && <Spinner label="Checking Moo Local..." />}
        {state.data && !state.data.ready && (
          <>
            <ul className="text-sm text-amber-800 list-disc pl-5">
              {state.data.missing.map((m) => <li key={m}>{m}</li>)}
            </ul>
            <Button href="/ai" variant="secondary">Open the AI engine screen</Button>
          </>
        )}
        {state.data?.ready && (
          <Button onClick={goOffline} disabled={busy}>
            {busy ? "Switching..." : state.data.offline ? "Keep using it without internet" : "Use it without internet"}
          </Button>
        )}
        {state.isError && <p className="text-sm text-red-600">Could not ask the Moo. Is this device on Moo&apos;s wifi?</p>}
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>

      <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-moo-ink/50">
        <span className="h-px flex-1 bg-moo-ink/10" />or<span className="h-px flex-1 bg-moo-ink/10" />
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">Put Moo on your home wifi</h2>
        <p className="text-sm text-moo-ink/70">Needed for the cloud engine, or to use a computer on your home network.</p>
        <Button href="/wifi" variant="secondary">Set up Wi-Fi</Button>
      </div>
    </StepLayout>
  );
}
