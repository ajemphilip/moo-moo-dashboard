"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/Button";
import { networkApi, PiApiError, wifiJoinApi, type Status } from "@/lib/api";

// The dashboard's Wi-Fi card: which wifi the Moo is on, a button to set up the
// home wifi, and, on the Moo's own wifi, using it without internet with this
// computer's Moo Local doing the AI.
export function WifiCard({ status }: { status: Status | undefined }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onOwnWifi = !!status && !status.wifi_connected;
  const network = useQuery({
    queryKey: ["network"],
    queryFn: networkApi.get,
    enabled: onOwnWifi,
    refetchInterval: onOwnWifi ? 10000 : false,
  });

  const setOffline = async (offline: boolean) => {
    setBusy(true); setError(null);
    try {
      await networkApi.setOffline(offline);
      await qc.invalidateQueries({ queryKey: ["network"] });
      await qc.invalidateQueries({ queryKey: ["status"] });
    } catch (e) {
      setError(e instanceof PiApiError ? e.message : "Could not change it.");
    } finally {
      setBusy(false);
    }
  };

  const join = useQuery({ queryKey: ["wifi-join"], queryFn: wifiJoinApi.last, refetchInterval: 5000 });
  const offline = network.data?.offline ?? status?.offline ?? false;
  // A join that failed shows while Moo is back on its own wifi; a join under way shows anywhere.
  const joinNote = join.data?.joining
    ? { tone: "text-moo-ink/70", text: join.data.message }
    : onOwnWifi && join.data?.ok === false && join.data.message
      ? { tone: "text-red-700", text: join.data.message }
      : null;

  return (
    <div className="moo-card flex flex-col gap-2">
      <h2 className="font-semibold">Wi-Fi</h2>
      <p className="text-sm text-moo-ink/70">
        {!status
          ? "Checking..."
          : status.wifi_connected
            ? `On ${status.wifi_ssid ?? "your wifi"}.`
            : offline
              ? "On Moo's own wifi, without internet. Moo Local on the computer does the stories, listening and voice."
              : "On Moo's own wifi. No internet."}
      </p>

      {joinNote && <p className={`text-sm ${joinNote.tone}`}>{joinNote.text}</p>}

      {onOwnWifi && network.data && !network.data.offline && (
        network.data.ready ? (
          <Button onClick={() => setOffline(true)} disabled={busy}>
            {busy ? "Switching..." : "Use it without internet"}
          </Button>
        ) : (
          <div className="text-sm text-moo-ink/70">
            <p>To use Moo without internet:</p>
            <ul className="list-disc pl-5 text-amber-800">
              {network.data.missing.map((m) => <li key={m}>{m}</li>)}
            </ul>
          </div>
        )
      )}
      {onOwnWifi && offline && (
        <Button variant="secondary" onClick={() => setOffline(false)} disabled={busy}>
          {busy ? "Switching..." : "Stop using it without internet"}
        </Button>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}

      <Button href="/wifi" variant="secondary">
        {status?.wifi_connected ? "Change Wi-Fi" : "Set up Wi-Fi"}
      </Button>
    </div>
  );
}
