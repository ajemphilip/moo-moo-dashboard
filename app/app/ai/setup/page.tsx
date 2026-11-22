"use client";

// Adding a local machine: a computer at home that does Moo's AI work.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError } from "@/lib/api";
import { stopThisDevice } from "@/lib/lendEngine";
import { mooBase } from "@/lib/mooBase";

type System = "windows" | "mac" | "linux";

const SYSTEMS: { id: System; label: string; file: string }[] = [
  { id: "windows", label: "Windows", file: "Install Moo Local.cmd" },
  { id: "mac", label: "Mac", file: "Install Moo Local.command" },
  { id: "linux", label: "Linux", file: "Install Moo Local.sh" },
];

// What the visitor's computer is; a phone or tablet gets null.
function detectSystem(): System | null {
  if (typeof navigator === "undefined") return null;
  const nav = navigator as Navigator & { userAgentData?: { platform?: string; mobile?: boolean } };
  const ua = navigator.userAgent;
  if (nav.userAgentData?.mobile || /Android|iPhone|iPad|iPod/i.test(ua)) return null;
  // An iPad asking for the desktop site says "Macintosh" but has a touch screen.
  if (/Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return null;
  const p = (nav.userAgentData?.platform || navigator.platform || ua).toLowerCase();
  if (p.includes("win")) return "windows";
  if (p.includes("mac")) return "mac";
  if (p.includes("linux") || p.includes("x11") || p.includes("cros")) return "linux";
  return null;
}

function HowToOpen({ system }: { system: System }) {
  if (system === "mac")
    return (
      <p className="text-xs text-moo-ink/70">
        Open <b>Install Moo Local</b> from Downloads. Safari unzips it by itself; in another browser, double-click the
        zip first. The file is not signed, so the Mac asks once: if it says it cannot check the file, open System
        Settings → Privacy &amp; Security and choose <b>Open Anyway</b> (on older macOS, right-click the file and choose
        Open). A Terminal window shows the progress; nothing has to be typed.
      </p>
    );
  if (system === "windows")
    return (
      <p className="text-xs text-moo-ink/70">
        Open <b>Install Moo Local.cmd</b> from Downloads. If the browser warns about the file, choose Keep. The file is
        not signed, so Windows SmartScreen asks once: choose <b>More info</b> → <b>Run anyway</b>. Near the end Windows
        asks for a Yes, so the Moo can reach this computer through the firewall.
      </p>
    );
  return (
    <p className="text-xs text-moo-ink/70">
      In Downloads, right-click <b>Install Moo Local.sh</b> → Properties → allow it to run as a program, then open it
      (choose Run if asked). Ollama&apos;s own installer asks for this computer&apos;s password once.
    </p>
  );
}

export default function AiSetupPage() {
  const router = useRouter();
  const [copied, setCopied] = useState(false);
  const [address, setAddress] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detected, setDetected] = useState<System | null>(null);
  const [chosen, setChosen] = useState<System>("windows");
  const [base, setBase] = useState({ links: "", http: "http://moo.local:8000" });

  useEffect(() => {
    const s = detectSystem();
    setDetected(s);
    if (s) setChosen(s);
    setBase(mooBase());
  }, []);

  const installLine = `curl -fsSL ${base.http}/local-ai/install.sh | bash`;
  const href = (s: System) => `${base.links}/local-ai/download/${s}`;
  const main = SYSTEMS.find((s) => s.id === chosen)!;
  const others = SYSTEMS.filter((s) => s.id !== chosen);

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
        <h2 className="font-semibold">1 · A computer: Windows, Mac or Linux</h2>
        <p className="text-sm text-moo-ink/80">
          The best quality. On that computer, download Moo Local and open the file. It installs Moo Local into your
          home folder (about 2 GB of programs, so allow some time) and starts it. It then starts by itself whenever
          someone logs in, and the computer appears under Devices. No AI model is downloaded yet: choose the story,
          listening and voice models afterwards under Models on the AI engine screen. A model too big for that
          computer is refused, with a smaller one to try.
        </p>
        {detected === null && (
          <p className="text-xs text-moo-ink/70">
            This looks like a phone or tablet. Open this page on the computer that should do the work, or choose
            its system below.
          </p>
        )}
        <a className="moo-button text-center" href={href(main.id)} download>
          Download for {main.label}
        </a>
        <p className="text-xs text-moo-ink/70">
          Also for{" "}
          {others.map((s, i) => (
            <span key={s.id}>
              {i > 0 && " · "}
              <a className="underline" href={href(s.id)} download onClick={() => setChosen(s.id)}>
                {s.label}
              </a>
            </span>
          ))}
        </p>
        <HowToOpen system={chosen} />
        <details className="text-xs text-moo-ink/70">
          <summary className="cursor-pointer">Or with a Terminal line (Mac or Linux)</summary>
          <div className="mt-2 flex flex-col gap-2">
            <p>Paste this line into Terminal and press Return. It does the same as the download.</p>
            <code className="block break-all rounded-moo bg-moo-ink/5 p-2">{installLine}</code>
            <Button
              variant="secondary"
              onClick={() => navigator.clipboard?.writeText(installLine).then(() => setCopied(true)).catch(() => {})}
            >
              {copied ? "Copied" : "Copy the line"}
            </Button>
          </div>
        </details>
      </div>

      <div className="moo-card flex flex-col gap-2">
        <h2 className="font-semibold">2 · Any device, nothing to install</h2>
        <p className="text-sm text-moo-ink/80">
          Open the Moo website on that computer, tablet or phone, go to AI engine and tap This device. Its browser
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
