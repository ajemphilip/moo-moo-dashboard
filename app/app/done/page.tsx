import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";

export default function DonePage() {
  return (
    <StepLayout step={6} totalSteps={6} title="Your Moo is ready" subtitle="Press the button on the side and ask for a story.">
      <div className="moo-card flex flex-col items-center gap-3 text-center py-10">
        <div className="w-24 h-24 rounded-full bg-moo-warm/40 flex items-center justify-center text-5xl">🌟</div>
        <p className="text-moo-ink/80">
          Try saying <em>&quot;tell me a story about a brave little rabbit&quot;</em>.
        </p>
      </div>
      <div className="moo-card flex flex-col gap-2 text-sm text-moo-ink/80">
        <h2 className="font-semibold text-moo-ink">A few tips</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Sit close to the device when speaking.</li>
          <li>Each story includes one word for spelling practice.</li>
          <li>Open this app any time to change settings.</li>
        </ul>
      </div>
      <Button href="/dashboard" className="mt-auto">Open dashboard</Button>
    </StepLayout>
  );
}
