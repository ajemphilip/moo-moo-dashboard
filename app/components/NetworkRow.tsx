import type { WifiNetwork } from "@/lib/api";

interface Props {
  network: WifiNetwork;
  onClick: () => void;
  selected?: boolean;
}

export function NetworkRow({ network, onClick, selected }: Props) {
  const bars = signalBars(network.signal);
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center justify-between gap-3 px-4 py-4 rounded-moo bg-white border min-h-[56px] text-left transition ${
        selected ? "border-moo-accent ring-2 ring-moo-accent/30" : "border-moo-ink/10"
      }`}
    >
      <div className="flex flex-col gap-0.5 min-w-0">
        <span className="text-base font-medium truncate">{network.ssid}</span>
        <span className="text-xs text-moo-ink/60">
          {network.secured ? "Password required" : "Open network"}
        </span>
      </div>
      <SignalIcon bars={bars} />
    </button>
  );
}

function signalBars(signal: number): 1 | 2 | 3 | 4 {
  if (signal >= 75) return 4;
  if (signal >= 50) return 3;
  if (signal >= 25) return 2;
  return 1;
}

function SignalIcon({ bars }: { bars: 1 | 2 | 3 | 4 }) {
  return (
    <div className="flex items-end gap-0.5 h-5" aria-label={`signal ${bars} of 4`}>
      {[1, 2, 3, 4].map((i) => (
        <div
          key={i}
          className={`w-1.5 rounded-sm ${i <= bars ? "bg-moo-accent" : "bg-moo-ink/15"}`}
          style={{ height: `${i * 25}%` }}
        />
      ))}
    </div>
  );
}
