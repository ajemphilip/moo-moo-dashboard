"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError } from "@/lib/api";

export default function ApiKeyPage() {
  const router = useRouter();
  const [key, setKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSave = async () => {
    setSubmitting(true); setError(null);
    try {
      await api.postConfig({ openai_api_key: key.trim() });
      router.replace("/preferences");
    } catch (err) {
      setError(err instanceof PiApiError ? err.message : "Could not save key.");
      setSubmitting(false);
    }
  };

  return (
    <StepLayout
      step={3} totalSteps={6}
      title="Add your OpenAI key"
      subtitle="Moo uses your OpenAI account to generate stories. Keys stay on the device — they never leave your home network."
      back="/wifi"
    >
      <div className="moo-card flex flex-col gap-3">
        <label htmlFor="api-key" className="text-sm font-medium">OpenAI API key</label>
        <input
          id="api-key" type="password" autoComplete="off" spellCheck={false}
          placeholder="sk-..." className="moo-input font-mono text-sm"
          value={key} onChange={(e) => setKey(e.target.value)}
        />
        <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer" className="text-sm text-moo-accent underline">
          How do I get a key?
        </a>
      </div>
      <div className="moo-card flex flex-col gap-2">
        <span className="text-sm font-medium">No key? Use Moo Local instead</span>
        <span className="text-sm text-moo-ink/70">
          Moo can use a computer on your home network for speech and stories —
          free, private and without internet.
        </span>
        <Button href="/ai?setup=1" variant="secondary">
          Use a computer on my network
        </Button>
      </div>
      {error && (
        <div className="rounded-moo bg-red-50 border border-red-200 p-3 text-sm text-red-800">
          {error}
        </div>
      )}
      <Button onClick={handleSave} disabled={!key.startsWith("sk-") || submitting} className="mt-auto">
        {submitting ? "Checking..." : "Save and continue"}
      </Button>
    </StepLayout>
  );
}
