"use client";

// The three word games as the phone shows them. Used by the story screen and
// the word games screen. The Pi sends the game state, this only draws it.

// Dot positions 0-5 shown as 1-6, the same layout as calibration:
//   1 4
//   2 5
//   3 6
const DOT_GRID: number[][] = [
  [0, 3],
  [1, 4],
  [2, 5],
];

export interface WordGameState {
  phase: string;
  spelling_word: string | null;
  game_mode?: "spell" | "name" | "build" | null;
  build_raised?: number[];
  build_target?: number[];
  spell_letter: string | null;
  spell_index: number | null;
  spell_total: number | null;
  spell_dots_left: number | null;
  spell_awaiting: boolean;
}

export function WordGame({ state }: { state: WordGameState | null }) {
  if (!state) return null;
  const phase = state.phase;
  return (
    <>
      {/* Name the letter */}
      {state.game_mode === "name" && state.spell_letter && (
        <div className="moo-card flex flex-col gap-2 items-center text-center">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40">
            Which letter is this?
          </p>
          <div className="w-16 h-16 rounded-moo flex items-center justify-center text-3xl font-bold bg-moo-accent text-white">
            {state.spell_letter}
          </div>
          <p className="text-sm text-moo-ink/70">
            A letter from <strong>{state.spelling_word}</strong> is raised. Your
            child feels it and says the letter out loud.
          </p>
        </div>
      )}

      {/* Riddle, then build the letter */}
      {state.game_mode === "build" && (
        <div className="moo-card flex flex-col gap-3 items-center text-center">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40">
            Riddle and make a letter
          </p>
          {state.spell_letter ? (
            <>
              <p className="text-sm text-moo-ink/80">
                Make the letter <strong>{state.spell_letter}</strong> from{" "}
                <strong>{state.spelling_word}</strong>: press dots to raise
                them, press again to lower.
              </p>
              <div className="grid grid-rows-3 grid-flow-col gap-x-6 gap-y-2">
                {DOT_GRID.flatMap((row) =>
                  row.map((dot) => {
                    const up = (state.build_raised ?? []).includes(dot);
                    const wanted = (state.build_target ?? []).includes(dot);
                    return (
                      <div
                        key={dot}
                        className={`w-11 h-11 rounded-full border-2 flex items-center justify-center text-sm font-semibold ${
                          up && wanted
                            ? "bg-emerald-500 text-white border-emerald-500"
                            : up
                              ? "bg-amber-400 text-white border-amber-400"
                              : wanted
                                ? "bg-white border-moo-accent border-dashed"
                                : "bg-white border-moo-ink/20"
                        }`}
                      >
                        {dot + 1}
                      </div>
                    );
                  })
                )}
              </div>
              <p className="text-xs text-moo-ink/50">
                Dashed: dots the letter needs. Green: raised and right. Yellow:
                raised but not part of the letter.
              </p>
            </>
          ) : (
            <p className="text-sm text-moo-ink/70">Moo is telling a riddle — listen!</p>
          )}
        </div>
      )}

      {/* Interactive spelling */}
      {phase === "spelling" && state.game_mode !== "name" && state.game_mode !== "build" && state.spelling_word && (
        <div className="moo-card flex flex-col gap-3 items-center text-center">
          <p className="text-xs uppercase tracking-wide text-moo-ink/40">
            Spelling
          </p>
          <div className="flex gap-1.5 flex-wrap justify-center">
            {state.spelling_word.toUpperCase().split("").map((ch, i) => {
              const done = state.spell_index !== null && i < state.spell_index;
              const current = i === state.spell_index;
              return (
                <span
                  key={i}
                  className={`w-10 h-10 rounded-moo flex items-center justify-center text-lg font-bold border ${
                    current
                      ? "bg-moo-accent text-white border-moo-accent"
                      : done
                        ? "bg-emerald-100 text-emerald-700 border-emerald-200"
                        : "bg-white text-moo-ink/40 border-moo-ink/15"
                  }`}
                >
                  {ch}
                </span>
              );
            })}
          </div>
          {state.spell_awaiting ? (
            <p className="text-sm text-moo-ink/80">
              Feel the letter <strong>{state.spell_letter}</strong>, then press
              a button for <strong>each raised dot</strong> to lower it.
              {state.spell_dots_left != null && state.spell_dots_left > 0 && (
                <> {state.spell_dots_left} still up.</>
              )}
            </p>
          ) : (
            <p className="text-sm text-moo-ink/60">Raising letter…</p>
          )}
        </div>
      )}

    </>
  );
}
