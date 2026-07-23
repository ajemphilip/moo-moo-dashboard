"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/Button";
import { NetworkRow } from "@/components/NetworkRow";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type WifiNetwork } from "@/lib/api";
import { waitForPi } from "@/lib/pi";
import { getTransportKind } from "@/lib/transport";

export default function WifiPage() {
  const router = useRouter();
  const [selected, setSelected] = useState<WifiNetwork | null>(null);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [handoff, setHandoff] = useState(false);

  const scan = useQuery({
    queryKey: ["wifi-scan"],
    queryFn: api.scanWifi,
    refetchInterval: handoff ? false : 12000,
  });

  const handleConnect = async () => {
    if (!selected) return;
    if (selected.secured && password.length === 0) {
      setError("Password required"); return;
    }
    setSubmitting(true); setError(null);
    try {
      await api.connectWifi(selected.ssid, password);
      setHandoff(true);
      const connected = getTransportKind() === "bluetooth"
        ? await waitForBluetoothWifi(75000)
        : !!(await waitForPi(75000));
      if (!connected) {
        setError(getTransportKind() === "bluetooth"
          ? "Moo did not join the selected wifi. Check the password and try again."
          : "Couldn't find your Moo on the new network. Make sure your device has rejoined your home wifi, then try again.");
        setHandoff(false); setSubmitting(false); return;
      }
      router.replace("/apikey");
    } catch (err) {
      setError(err instanceof PiApiError ? err.message : "Could not connect.");
      setHandoff(false); setSubmitting(false);
    }
  };

  if (handoff) {
    return (
      <StepLayout title="Connecting Moo..." subtitle="Your Moo is joining your home wifi. This usually takes 20–60 seconds.">
        <Spinner label="Looking for your Moo on the home network..." />
        {getTransportKind() !== "bluetooth" && (
          <p className="text-sm text-moo-ink/70 text-center">
            Switch this device back to your home wifi if it doesn&apos;t happen automatically.
          </p>
        )}
      </StepLayout>
    );
  }

  return (
    <StepLayout step={2} totalSteps={6} title="Pick your wifi" subtitle="Choose the same network the rest of your house uses." back="/connect">
      {scan.isPending && <Spinner label="Scanning for networks..." />}
      {scan.isError && <p className="text-red-600 text-sm">Could not load networks. Pull to refresh.</p>}
      {scan.data && (
        <div className="flex flex-col gap-2">
          {scan.data.networks.length === 0 && (
            <p className="text-moo-ink/70 text-sm">No networks found yet — give it a moment.</p>
          )}
          {scan.data.networks.map((n) => (
            <NetworkRow
              key={n.ssid}
              network={n}
              selected={selected?.ssid === n.ssid}
              onClick={() => { setSelected(n); setPassword(""); setError(null); }}
            />
          ))}
        </div>
      )}
      {selected?.secured && (
        <div className="moo-card flex flex-col gap-2">
          <label className="text-sm font-medium" htmlFor="wifi-pw">Password for {selected.ssid}</label>
          <input
            id="wifi-pw" type="password" autoComplete="off" className="moo-input"
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
        </div>
      )}
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <Button onClick={handleConnect} disabled={!selected || submitting} className="mt-auto">
        {submitting ? "Connecting..." : "Connect"}
      </Button>
    </StepLayout>
  );
}

async function waitForBluetoothWifi(timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await api.status()).wifi_connected) return true;
    } catch {
      // The Pi may briefly restart networking; keep the BLE connection attempt alive.
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  return false;
}
