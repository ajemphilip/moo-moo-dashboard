"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/Button";
import { GetTheApp } from "@/components/GetTheApp";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { discoverPi } from "@/lib/pi";
import { restoreBluetoothConnection } from "@/lib/bluetooth";
import { getTransportKind } from "@/lib/transport";
import { api } from "@/lib/api";

type Probe = "loading" | "found-unconfigured" | "found-configured" | "missing";

export default function Welcome() {
  const [probe, setProbe] = useState<Probe>("loading");
  // Where "Continue setup" leads: on Moo's own wifi, the choice of home wifi or no internet.
  const [nextStep, setNextStep] = useState("/wifi");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const bluetooth = getTransportKind() === "bluetooth"
        ? await restoreBluetoothConnection()
        : null;
      const base = bluetooth ? "bluetooth" : await discoverPi();
      if (cancelled) return;
      if (!base) { setProbe("missing"); return; }
      try {
        const status = await api.status();
        if (cancelled) return;
        setNextStep(!status.wifi_connected && getTransportKind() !== "bluetooth" ? "/network" : "/wifi");
        setProbe(status.configured ? "found-configured" : "found-unconfigured");
      } catch {
        if (!cancelled) setProbe("missing");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <StepLayout title="Welcome to Moo" subtitle="A gentle storyteller for little hands and ears.">
      <div className="moo-card flex flex-col items-center gap-3 text-center">
        <div className="w-24 h-24 rounded-full bg-moo-warm/40 flex items-center justify-center text-5xl">
          🐮
        </div>
        {probe === "loading" && <Spinner label="Looking for your Moo..." />}
        {probe === "missing" && (
          <>
            <p className="text-moo-ink/80">
              We couldn&apos;t find your Moo. Let&apos;s connect to it first.
            </p>
            <Button href="/connect" className="w-full mt-2">Set up a new Moo</Button>
          </>
        )}
        {probe === "found-unconfigured" && (
          <>
            <p className="text-moo-ink/80">Found your Moo. Let&apos;s get it ready.</p>
            <Button href={nextStep} className="w-full mt-2">Continue setup</Button>
          </>
        )}
        {probe === "found-configured" && (
          <>
            <p className="text-moo-ink/80">Your Moo is already set up.</p>
            <Button href="/dashboard" className="w-full mt-2">Open dashboard</Button>
          </>
        )}
      </div>
      <GetTheApp />
    </StepLayout>
  );
}
