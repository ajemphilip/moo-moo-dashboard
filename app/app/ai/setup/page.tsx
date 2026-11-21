"use client";

// Adding a local machine: a computer at home that does Moo's AI work.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError } from "@/lib/api";
import { stopThisDevice } from "@/lib/lendEngine";

export default function AiSetupPage() {
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const host = typeof window !== "undefined" ? window.location.hostname : "moo.local";
  const installLine = `curl -fsSL http://${host}:8000/local-ai/install.sh | bash`;

  const addByAddress = async () => {
    // "192.168.1.20", "mac.local:8765" or a full address all work.
    let a = address.trim().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (!a) return;
    if (!a.includes(":")) a += ":8765";
    setBusy(true);
    setError(null);
    try {
      stopThisDevice();
      await api.aiUpdate({ mode: "local", local_url: `http://${a}/v1`, local_name: a.split(":")[0] });
      router.replace("/ai");
    } catch (e) {
      setError(e instanceof PiApiError ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepLayout
      title="Set up a local machine"
      subtitle="A computer at home that does Moo's listening, stories and voice."
      back="/ai"
    >
      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">1 · A Mac with Apple silicon</h2>
        <p className="text-sm text-moo-ink/80">
          The best quality. Open Terminal on that Mac, paste this line and press Return. It installs Moo Local (about
          5 GB), downloads a story model and the voices, and starts it. The Mac then appears under Local machines.
        </p>
        <code className="block break-all rounded-moo bg-moo-ink/5 p-2 text-xs">{installLine}</code>
        <Button
          variant="secondary"
          onClick={() => navigator.clipboard?.writeText(installLine).then(() => setCopied(true)).catch(() => {})}
        >
          {copied ? "Copied" : "Copy the line"}
        </Button>
        <p className="text-xs text-moo-ink/60">
          After the Mac restarts, Moo Local has to be started again. The installer prints the command for that.
        </p>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">2 · Any other computer, tablet or phone</h2>
        <p className="text-sm text-moo-ink/80">
          Nothing to install. Open the Moo website on that device, go to AI engine and tap This device. Its browser
          then does the work, and the device appears under Local machines for as long as the page stays open.
        </p>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">3 · A machine that is not found by itself</h2>
        <p className="text-sm text-moo-ink/80">
          If a computer runs Moo Local but does not show up, type its address, for example 192.168.1.20.
        </p>
        <div className="flex gap-2">
          <input
            className="moo-input flex-1"
            placeholder="Address of the computer"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            inputMode="url"
            autoCapitalize="none"
          />
          <Button variant="secondary" onClick={addByAddress} disabled={busy || !address.trim()}>
            Add
          </Button>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
      </div>

      <Button onClick={() => router.replace("/ai")} variant="secondary" className="mt-auto">
        Back to AI engine
      </Button>
    </StepLayout>
  );
}
