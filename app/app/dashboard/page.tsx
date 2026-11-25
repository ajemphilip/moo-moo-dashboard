"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/Button";
import { GetTheApp } from "@/components/GetTheApp";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { WifiCard } from "@/components/WifiCard";
import { api, PiApiError, type BrailleTestResult } from "@/lib/api";
import { getTransportKind } from "@/lib/transport";

export default function DashboardPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [confirmReset, setConfirmReset] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5000 });
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.aiGet });

  const restart = useMutation({
    mutationFn: api.restart,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["status"] }),
    onError: (err) => setError(err instanceof PiApiError ? err.message : "Restart failed."),
  });

  const [brailleTestResult, setBrailleTestResult] = useState<BrailleTestResult | null>(null);
  const brailleTest = useMutation({
    mutationFn: (motorIndex: number | null) => api.testBraille(motorIndex),
    onSuccess: (res) => { setBrailleTestResult(res); setError(null); },
    onError: (err) => {
      setBrailleTestResult(null);
      setError(err instanceof PiApiError ? err.message : "Braille test failed.");
    },
  });

  const reset = useMutation({
    mutationFn: api.reset,
    onSuccess: () => router.replace("/connect"),
    onError: (err) => setError(err instanceof PiApiError ? err.message : "Reset failed."),
  });

  return (
    <StepLayout title="Your Moo" subtitle="Manage settings and check on your device.">
      <WifiCard status={status.data} />

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">Test the Braille cell</h2>
        <p className="text-sm text-moo-ink/70">
          Pulses each motor up and back down to check the wiring. The assistant pauses for about 10 seconds.
        </p>
        <div className="flex flex-wrap gap-2 mt-1">
          <button type="button" onClick={() => brailleTest.mutate(null)} disabled={brailleTest.isPending}
            className="moo-button-secondary px-4 py-3 text-base">
            {brailleTest.isPending && brailleTest.variables === null ? "Testing all..." : "Test all"}
          </button>
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <button key={i} type="button" onClick={() => brailleTest.mutate(i)} disabled={brailleTest.isPending}
              className="moo-button-secondary px-4 py-3 text-base min-w-[3rem]" aria-label={`Test motor ${i}`}>
              {brailleTest.isPending && brailleTest.variables === i ? "…" : i}
            </button>
          ))}
        </div>
        {brailleTestResult && (
          <p className="text-sm text-emerald-700">
            {brailleTestResult.status === "dry_run"
              ? "Dry-run (no hardware detected)"
              : `Tested motor${brailleTestResult.motors_tested.length > 1 ? "s" : ""} ${brailleTestResult.motors_tested.join(", ")} in ${brailleTestResult.duration_seconds}s.`}
          </p>
        )}
        <Button href="/calibrate" variant="secondary" className="mt-1">
          Calibrate motors &amp; buttons
        </Button>
        <Button href="/buttons" variant="secondary">
          Button check
        </Button>
        <Button href="/audio" variant="secondary">
          Sound check (speaker &amp; mic)
        </Button>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">AI engine</h2>
        <p className="text-sm text-moo-ink/70">
          {ai.data?.mode === "local"
            ? `Local — ${ai.data.local_name ?? "a computer on your network"}. No internet or key needed.`
            : ai.data?.mode === "browser"
              ? "A phone or laptop does the work in its browser."
              : "Cloud — OpenAI, with your key."}
        </p>
        <Button href="/ai" variant="secondary" className="mt-1">
          Choose engine, quality and voice
        </Button>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">Play with Moo</h2>
        <p className="text-sm text-moo-ink/70">
          Activities for your child — practising letters and telling stories
          together.
        </p>
        <Button href="/letters" variant="secondary" className="mt-1">
          Letter practice
        </Button>
        <Button href="/story" variant="secondary">
          Story time
        </Button>
        <Button href="/games" variant="secondary">
          Word games
        </Button>
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      {/* Settings: the Moo's status, its preferences, restart and reset. */}
      <div className="moo-card flex flex-col gap-2 mt-auto">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Settings</h2>
          <StatusPill running={status.data?.assistant_running ?? false} />
        </div>
        {status.isPending ? <Spinner /> : status.data ? (
          <dl className="text-sm grid grid-cols-2 gap-y-1.5 mt-1">
            <dt className="text-moo-ink/60">Wi-Fi</dt><dd className="text-right">{status.data.wifi_ssid ?? (status.data.offline ? "Moo's own, no internet" : "—")}</dd>
            <dt className="text-moo-ink/60">Device</dt><dd className="text-right font-mono">{status.data.device_id}</dd>
            <dt className="text-moo-ink/60">Version</dt><dd className="text-right">{status.data.version}</dd>
            <dt className="text-moo-ink/60">Connection</dt><dd className="text-right capitalize">{getTransportKind()}</dd>
          </dl>
        ) : <p className="text-red-600 text-sm">Could not reach Moo.</p>}
        <Button href="/preferences" variant="secondary" className="mt-1">Preferences: age, voice, volume</Button>
        <Button variant="secondary" onClick={() => restart.mutate()} disabled={restart.isPending}>
          {restart.isPending ? "Restarting..." : "Restart device"}
        </Button>
        {confirmReset ? (
          <div className="moo-card flex flex-col gap-2">
            <p className="text-sm">
              Factory reset will erase the wifi password and OpenAI key. You&apos;ll need to set up the Moo again.
            </p>
            <div className="flex gap-2">
              <button type="button" onClick={() => setConfirmReset(false)} className="flex-1 moo-button-secondary">Cancel</button>
              <button type="button" onClick={() => reset.mutate()} disabled={reset.isPending}
                className="flex-1 rounded-moo bg-red-600 text-white font-semibold px-6 py-4 text-lg min-h-[48px] active:scale-[0.98] transition disabled:opacity-50">
                {reset.isPending ? "Resetting..." : "Reset"}
              </button>
            </div>
          </div>
        ) : (
          <Button variant="secondary" onClick={() => setConfirmReset(true)}>Factory reset</Button>
        )}
      </div>

      <GetTheApp />
    </StepLayout>
  );
}

function StatusPill({ running }: { running: boolean }) {
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
      running ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
    }`}>
      {running ? "Listening" : "Idle"}
    </span>
  );
}
