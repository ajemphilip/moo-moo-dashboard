"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError } from "@/lib/api";

const VOICES = [
  { id: "sage", label: "Sage — calm & gentle" },
  { id: "alloy", label: "Alloy — warm & even" },
  { id: "fable", label: "Fable — playful" },
  { id: "shimmer", label: "Shimmer — bright" },
  { id: "nova", label: "Nova — friendly" },
  { id: "echo", label: "Echo — soft" },
];

const AGES = [
  { id: "0-3", label: "0–3 years" },
  { id: "3-5", label: "3–5 years" },
  { id: "5-8", label: "5–8 years" },
] as const;

export default function PreferencesPage() {
  const router = useRouter();
  const [voice, setVoice] = useState("sage");
  const [age, setAge] = useState<(typeof AGES)[number]["id"]>("3-5");
  const [volume, setVolume] = useState(70);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async (next: "/audio") => {
    setSaving(true); setError(null);
    try {
      await api.postConfig({ preferences: { tts_voice: voice, age_range: age, volume, language: "en" } });
      router.replace(next);
    } catch (err) {
      setError(err instanceof PiApiError ? err.message : "Could not save.");
      setSaving(false);
    }
  };

  return (
    <StepLayout step={4} totalSteps={6} title="A few preferences" subtitle="You can change these later." back="/apikey">
      <div className="moo-card flex flex-col gap-2">
        <label htmlFor="voice" className="text-sm font-medium">Storytelling voice</label>
        <select id="voice" className="moo-input" value={voice} onChange={(e) => setVoice(e.target.value)}>
          {VOICES.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
        </select>
      </div>
      <div className="moo-card flex flex-col gap-3">
        <span className="text-sm font-medium">Age range</span>
        <div className="flex gap-2">
          {AGES.map((a) => (
            <button
              key={a.id} type="button" onClick={() => setAge(a.id)}
              className={`flex-1 py-3 rounded-moo border min-h-[48px] text-sm font-medium transition ${
                age === a.id ? "border-moo-accent bg-moo-accent/10" : "border-moo-ink/10 bg-white"
              }`}
            >{a.label}</button>
          ))}
        </div>
      </div>
      <div className="moo-card flex flex-col gap-2">
        <label htmlFor="vol" className="text-sm font-medium flex justify-between">
          <span>Volume</span>
          <span className="text-moo-ink/60">{volume}%</span>
        </label>
        <input id="vol" type="range" min={0} max={100} value={volume}
          onChange={(e) => setVolume(Number(e.target.value))} className="w-full accent-moo-accent" />
      </div>
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <div className="mt-auto flex flex-col gap-2">
        <Button onClick={() => save("/audio")} disabled={saving}>
          {saving ? "Saving..." : "Save and continue"}
        </Button>
        <Button href="/audio" variant="secondary">Skip for now</Button>
      </div>
    </StepLayout>
  );
}
