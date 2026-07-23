"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type AudioDevices, type MicTestResult } from "@/lib/api";

type Load = "loading" | "ready" | "none" | "error";

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function AudioPage() {
  const router = useRouter();
  const [load, setLoad] = useState<Load>("loading");
  const [devices, setDevices] = useState<AudioDevices | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [speakerBusy, setSpeakerBusy] = useState(false);
  const [speakerOk, setSpeakerOk] = useState(false);
  const [micBusy, setMicBusy] = useState(false);
  const [micResult, setMicResult] = useState<MicTestResult | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .audioDevices()
      .then((d) => {
        if (cancelled) return;
        setDevices(d);
        setLoad(d.devices.length === 0 ? "none" : "ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(errMsg(e));
        setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const inIdx = devices?.suggested_input ?? null;
  const outIdx = devices?.suggested_output ?? null;
  const inDev = devices?.devices.find((d) => d.index === inIdx);
  const outDev = devices?.devices.find((d) => d.index === outIdx);

  const playTest = async () => {
    setSpeakerBusy(true);
    setError(null);
    try {
      await api.testSpeaker(outIdx);
      setSpeakerOk(true);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setSpeakerBusy(false);
    }
  };

  const testMic = async () => {
    setMicBusy(true);
    setError(null);
    setMicResult(null);
    try {
      const res = await api.testMic(inIdx, outIdx);
      setMicResult(res);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setMicBusy(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.postConfig({ audio: { input_index: inIdx, output_index: outIdx } });
      router.replace("/done");
    } catch (e) {
      setError(errMsg(e));
      setSaving(false);
    }
  };

  if (load === "loading") {
    return (
      <StepLayout step={5} totalSteps={6} title="Sound check" back="/preferences">
        <Spinner label="Finding the speaker and microphone..." />
      </StepLayout>
    );
  }

  if (load === "error") {
    return (
      <StepLayout step={5} totalSteps={6} title="Sound check" back="/preferences">
        <p className="text-red-600 text-sm">{error}</p>
        <Button href="/done" variant="secondary" className="mt-auto">Skip for now</Button>
      </StepLayout>
    );
  }

  if (load === "none") {
    return (
      <StepLayout
        step={5}
        totalSteps={6}
        title="Sound check"
        subtitle="No audio device found."
        back="/preferences"
      >
        <div className="moo-card text-sm text-moo-ink/80">
          Plug the USB microphone/speaker into the Moo, then reload this page.
        </div>
        <div className="mt-auto flex flex-col gap-2">
          <Button onClick={() => location.reload()}>Reload</Button>
          <Button href="/done" variant="secondary">Skip for now</Button>
        </div>
      </StepLayout>
    );
  }

  return (
    <StepLayout
      step={5}
      totalSteps={6}
      title="Sound check"
      subtitle="Confirm the USB speaker and microphone work."
      back="/preferences"
    >
      <div className="moo-card flex flex-col gap-1">
        <span className="text-sm font-medium">Using these devices:</span>
        <span className="text-sm text-moo-ink/70">
          🔊 {outDev ? outDev.name : "no speaker found"}
        </span>
        <span className="text-sm text-moo-ink/70">
          🎙️ {inDev ? inDev.name : "no microphone found"}
        </span>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">1. Test the speaker</h2>
        <p className="text-sm text-moo-ink/70">Plays a short sound. Listen for it.</p>
        <button
          type="button"
          onClick={playTest}
          disabled={speakerBusy || outIdx === null}
          className="moo-button-secondary"
        >
          {speakerBusy ? "Playing..." : "Play test sound"}
        </button>
        {speakerOk && (
          <p className="text-sm text-emerald-700">Played. Did you hear it?</p>
        )}
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">2. Test the microphone</h2>
        <p className="text-sm text-moo-ink/70">
          Records 3 seconds, then plays it back. Say something after you tap.
        </p>
        <button
          type="button"
          onClick={testMic}
          disabled={micBusy || inIdx === null}
          className="moo-button-secondary"
        >
          {micBusy ? "Listening (3s)..." : "Test microphone"}
        </button>
        {micResult && (
          <p
            className={`text-sm ${
              micResult.heard ? "text-emerald-700" : "text-red-600"
            }`}
          >
            {micResult.heard
              ? `Heard you clearly (level ${micResult.peak_level}). Mic works.`
              : `Too quiet (level ${micResult.peak_level}) — check the mic and try again.`}
          </p>
        )}
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}

      <div className="mt-auto flex flex-col gap-2">
        <Button onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save and finish"}
        </Button>
        <Button href="/done" variant="secondary">Skip for now</Button>
      </div>
    </StepLayout>
  );
}
