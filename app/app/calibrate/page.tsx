"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/Button";
import { Spinner } from "@/components/Spinner";
import { StepLayout } from "@/components/StepLayout";
import { api, PiApiError, type CalibrateStart } from "@/lib/api";

type Phase = "loading" | "motors" | "buttons" | "saving" | "done" | "error";

// Braille dot positions 0-5 shown to the user as 1-6, laid out:
//   1 4
//   2 5
//   3 6
const DOT_GRID: number[][] = [
  [0, 3],
  [1, 4],
  [2, 5],
];

interface MotorEntry {
  dot: number | null; // 0-5
  reversed: boolean;
}

function errMsg(e: unknown): string {
  return e instanceof PiApiError ? e.message : "Something went wrong.";
}

export default function CalibratePage() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [start, setStart] = useState<CalibrateStart | null>(null);
  const [motors, setMotors] = useState<MotorEntry[]>(
    Array.from({ length: 6 }, () => ({ dot: null, reversed: false }))
  );
  const [buttonMap, setButtonMap] = useState<(number | null)[]>(Array(6).fill(null));
  const [pulsing, setPulsing] = useState<number | null>(null);
  const [listening, setListening] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .calibrateStart()
      .then((s) => {
        if (cancelled) return;
        setStart(s);
        setPhase("motors");
      })
      .catch((e) => {
        if (cancelled) return;
        setError(errMsg(e));
        setPhase("error");
      });
    return () => {
      cancelled = true;
      api.calibrateStop().catch(() => {});
    };
  }, []);

  const pulse = async (motor: number) => {
    setPulsing(motor);
    setError(null);
    try {
      await api.calibratePulse(motor);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setPulsing(null);
    }
  };

  const setDot = (motor: number, dot: number) => {
    setMotors((prev) =>
      prev.map((m, i) => {
        if (i === motor) return { ...m, dot: m.dot === dot ? null : dot };
        if (m.dot === dot) return { ...m, dot: null }; // dot is unique
        return m;
      })
    );
  };

  const toggleReversed = (motor: number) => {
    setMotors((prev) =>
      prev.map((m, i) => (i === motor ? { ...m, reversed: !m.reversed } : m))
    );
  };

  const listenForButton = async (dot: number) => {
    const rawMotor = motors.findIndex((m) => m.dot === dot);
    if (rawMotor < 0) {
      setError("Finish matching motors first.");
      return;
    }
    setListening(dot);
    setError(null);
    try {
      await api.calibratePulse(rawMotor, "up"); // raise the pin so it's visible
      await api.calibrateButton(); // drain any stale press
      const deadline = Date.now() + 12000;
      let found: number | null = null;
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 600));
        const res = await api.calibrateButton();
        if (res.button !== null && res.button !== undefined) {
          found = res.button;
          break;
        }
      }
      await api.calibratePulse(rawMotor, "down"); // lower it again
      if (found !== null) {
        const captured = found;
        setButtonMap((prev) => prev.map((b, i) => (i === dot ? captured : b)));
      } else {
        setError("No button detected — try again.");
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setListening(null);
    }
  };

  const motorsDone =
    motors.every((m) => m.dot !== null) &&
    new Set(motors.map((m) => m.dot)).size === 6;
  const buttonsDone =
    buttonMap.every((b) => b !== null) && new Set(buttonMap).size === 6;

  // motor_pins ordered by dot position; swap the pair if reversed.
  const buildMotorPins = (): number[][] => {
    const motorPins: number[][] = [];
    for (let dot = 0; dot < 6; dot++) {
      const m = motors.findIndex((e) => e.dot === dot);
      const pair = start!.motor_pins[m];
      motorPins.push(motors[m].reversed ? [pair[1], pair[0]] : [pair[0], pair[1]]);
    }
    return motorPins;
  };

  const save = async () => {
    if (!start) return;
    setPhase("saving");
    setError(null);
    try {
      const buttonPins: number[] = [];
      for (let dot = 0; dot < 6; dot++) {
        buttonPins.push(start.button_pins[buttonMap[dot] as number]);
      }
      await api.calibrateSave(buildMotorPins(), buttonPins);
      setPhase("done");
    } catch (e) {
      setError(errMsg(e));
      setPhase("buttons");
    }
  };

  // Save motor calibration only — leaves button_pins at their current values.
  const saveMotorsOnly = async () => {
    if (!start) return;
    setPhase("saving");
    setError(null);
    try {
      await api.calibrateSave(buildMotorPins(), start.button_pins);
      setPhase("done");
    } catch (e) {
      setError(errMsg(e));
      setPhase("motors");
    }
  };

  // ---------------------------------------------------------------- render

  if (phase === "loading") {
    return (
      <StepLayout title="Calibrate Moo" subtitle="Starting calibration...">
        <Spinner label="Waking up the Braille cell..." />
      </StepLayout>
    );
  }

  if (phase === "error") {
    return (
      <StepLayout title="Calibration" subtitle="Couldn't start." back="/dashboard">
        <p className="text-red-600 text-sm">{error}</p>
        <Button href="/dashboard" className="mt-auto">Back to dashboard</Button>
      </StepLayout>
    );
  }

  if (phase === "saving") {
    return (
      <StepLayout title="Calibrate Moo" subtitle="Saving...">
        <Spinner label="Writing the pin map..." />
      </StepLayout>
    );
  }

  if (phase === "done") {
    return (
      <StepLayout title="Calibration saved" subtitle="Moo's Braille cell is mapped.">
        <div className="moo-card text-center py-8">
          <div className="text-5xl mb-2">✅</div>
          <p className="text-moo-ink/80">Motors and buttons are matched to their dots.</p>
        </div>
        <Button href="/dashboard" className="mt-auto">Back to dashboard</Button>
      </StepLayout>
    );
  }

  if (phase === "motors") {
    return (
      <StepLayout
        step={1}
        totalSteps={2}
        title="Match motors to dots"
        subtitle="Pulse each motor, watch which dot moves, and tell us which one."
        back="/dashboard"
      >
        {motors.map((m, motor) => (
          <div key={motor} className="moo-card flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <span className="font-semibold">Motor {motor}</span>
              <button
                type="button"
                onClick={() => pulse(motor)}
                disabled={pulsing !== null}
                className="moo-button-secondary px-4 py-2 text-base"
              >
                {pulsing === motor ? "Moving…" : "Pulse"}
              </button>
            </div>
            <div className="text-sm text-moo-ink/60">Which dot moved?</div>
            <div className="flex gap-3">
              {DOT_GRID[0] /* render the 2x3 grid */ && (
                <div className="grid grid-rows-3 grid-flow-col gap-2">
                  {DOT_GRID.flatMap((row) =>
                    row.map((dot) => {
                      const takenByOther = motors.some(
                        (e, i) => i !== motor && e.dot === dot
                      );
                      const selected = m.dot === dot;
                      return (
                        <button
                          key={dot}
                          type="button"
                          disabled={takenByOther}
                          onClick={() => setDot(motor, dot)}
                          className={`w-11 h-11 rounded-full border text-sm font-semibold transition ${
                            selected
                              ? "bg-moo-accent text-white border-moo-accent"
                              : takenByOther
                                ? "bg-moo-ink/5 text-moo-ink/30 border-moo-ink/10"
                                : "bg-white border-moo-ink/20"
                          }`}
                        >
                          {dot + 1}
                        </button>
                      );
                    })
                  )}
                </div>
              )}
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={m.reversed}
                  onChange={() => toggleReversed(motor)}
                  className="w-5 h-5 accent-moo-accent"
                />
                Moved down first (reversed)
              </label>
            </div>
          </div>
        ))}
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <div className="mt-auto flex flex-col gap-2">
          <Button
            onClick={() => { setError(null); setPhase("buttons"); }}
            disabled={!motorsDone}
          >
            {motorsDone ? "Next: match buttons" : "Assign all 6 dots first"}
          </Button>
          <Button variant="secondary" onClick={saveMotorsOnly} disabled={!motorsDone}>
            Save motors only — skip buttons
          </Button>
        </div>
      </StepLayout>
    );
  }

  // phase === "buttons"
  return (
    <StepLayout
      step={2}
      totalSteps={2}
      title="Match buttons to dots"
      subtitle="Tap a dot — its pin raises. Press the button under that raised pin."
      back="/dashboard"
    >
      {[0, 1, 2, 3, 4, 5].map((dot) => (
        <div key={dot} className="moo-card flex items-center justify-between">
          <span className="font-semibold">Dot {dot + 1}</span>
          <div className="flex items-center gap-3">
            {buttonMap[dot] !== null && (
              <span className="text-emerald-700 text-sm">✓ captured</span>
            )}
            <button
              type="button"
              onClick={() => listenForButton(dot)}
              disabled={listening !== null}
              className="moo-button-secondary px-4 py-2 text-base"
            >
              {listening === dot
                ? "Press the raised pin's button…"
                : buttonMap[dot] !== null
                  ? "Redo"
                  : "Raise pin & listen"}
            </button>
          </div>
        </div>
      ))}
      {error && <p className="text-red-600 text-sm">{error}</p>}
      <div className="mt-auto flex flex-col gap-2">
        <Button onClick={save} disabled={!buttonsDone}>
          {buttonsDone ? "Save calibration" : "Capture all 6 buttons first"}
        </Button>
        <Button variant="secondary" onClick={() => { setError(null); setPhase("motors"); }}>
          Back to motors
        </Button>
      </div>
    </StepLayout>
  );
}
