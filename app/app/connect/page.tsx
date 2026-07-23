"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { discoverPi } from "@/lib/pi";
import { api } from "@/lib/api";
import { bluetoothSupported, connectBluetooth, setBluetoothPin } from "@/lib/bluetooth";

export default function ConnectPage() {
  const router = useRouter();
  const githubPages = process.env.NEXT_PUBLIC_GITHUB_PAGES === "true";
  const [polling, setPolling] = useState(false);
  const [bluetoothBusy, setBluetoothBusy] = useState(false);
  const [bluetoothError, setBluetoothError] = useState<string | null>(null);
  const [setupCode, setSetupCode] = useState("");
  const [supportsBluetooth, setSupportsBluetooth] = useState<boolean | null>(null);

  useEffect(() => {
    setSupportsBluetooth(bluetoothSupported());
  }, []);

  const useBluetooth = async () => {
    setBluetoothBusy(true);
    setBluetoothError(null);
    try {
      setBluetoothPin(setupCode);
      await connectBluetooth();
      const status = await api.status();
      router.replace(status.configured ? "/dashboard" : "/wifi");
    } catch (error) {
      setBluetoothError(error instanceof Error ? error.message : "Could not connect over Bluetooth.");
      setBluetoothBusy(false);
    }
  };

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: number | undefined;

    const tick = async () => {
      const base = await discoverPi();
      if (cancelled) return;
      if (base) { router.replace("/wifi"); return; }
      timer = window.setTimeout(tick, 2000);
    };

    void tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [polling, router]);

  return (
    <StepLayout
      step={1} totalSteps={6}
      title="Connect to your Moo"
      subtitle="Use Bluetooth without leaving your current wifi, or use Moo's temporary wifi network."
      back="/"
    >
      <div className="moo-card flex flex-col gap-3">
        <div>
          <h2 className="font-semibold">Bluetooth — stay on your wifi</h2>
          <p className="text-sm text-moo-ink/70 mt-1">
            Best for laptop setup. Chrome or Edge will ask you to choose a nearby Moo.
          </p>
        </div>
        <label className="text-sm font-medium" htmlFor="setup-code">
          Setup code <span className="font-normal text-moo-ink/60">(only if printed on your Moo)</span>
        </label>
        <input
          id="setup-code"
          inputMode="numeric"
          maxLength={6}
          className="moo-input font-mono"
          placeholder="Optional 6-digit code"
          value={setupCode}
          onChange={(event) => setSetupCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
        />
        <Button onClick={useBluetooth} disabled={bluetoothBusy || supportsBluetooth !== true}>
          {bluetoothBusy ? "Connecting..." : "Find Moo with Bluetooth"}
        </Button>
        {supportsBluetooth === false && (
          <p className="text-sm text-amber-700">Bluetooth setup is not supported by this browser. Use Chrome or Edge.</p>
        )}
        {bluetoothError && <p className="text-sm text-red-600">{bluetoothError}</p>}
      </div>

      <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-moo-ink/50">
        <span className="h-px flex-1 bg-moo-ink/10" />or<span className="h-px flex-1 bg-moo-ink/10" />
      </div>

      <div className="moo-card flex flex-col gap-3">
        <div>
          <h2 className="font-semibold">Temporary Moo wifi</h2>
          <p className="text-sm text-moo-ink/70 mt-1">The original setup method remains available.</p>
        </div>
        <ol className="flex flex-col gap-3">
          <Step n={1} text="Open this device's Wi-Fi settings." />
          <Step n={2} text={<>Choose the network starting with <strong>moo-</strong>.</>} />
          <Step n={3} text="Return here after it connects." />
        </ol>
        {polling ? (
          <Spinner label="Looking for your Moo..." />
        ) : (
          <Button variant="secondary" onClick={() => {
            if (githubPages) window.location.assign("http://10.41.0.1:8000");
            else setPolling(true);
          }}>
            I&apos;m connected to Moo wifi
          </Button>
        )}
      </div>
    </StepLayout>
  );
}

function Step({ n, text }: { n: number; text: React.ReactNode }) {
  return (
    <li className="flex items-start gap-3 py-1">
      <span className="shrink-0 w-7 h-7 rounded-full bg-moo-warm/60 flex items-center justify-center font-semibold">
        {n}
      </span>
      <span className="leading-snug">{text}</span>
    </li>
  );
}
