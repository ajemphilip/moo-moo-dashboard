// The Moo's job line. The app lends the phone to the Moo exactly as a browser
// does on the AI engine screen: it says hello, asks for the next job, does it,
// and posts the result. The Moo never has to reach the phone.
//
//   POST /engine/hello   {name, device}            -> {engine_id}
//   GET  /engine/next    ?engine_id=…  waits up to 25 s  -> {job | null}
//   POST /engine/result  {engine_id, job_id, ok, result | error}
//   POST /engine/report  {device, stage}   a note in the Moo's log

export type JobKind = "chat" | "moderate" | "transcribe" | "speak";

export interface Job {
  id: string;
  kind: JobKind;
  payload: Record<string, any>;
}

export type JobResult = Record<string, unknown>;

const HEADERS = { "Content-Type": "application/json", "X-Moo-Setup": "1" };

export interface LoopEvents {
  onState: (text: string) => void;
  onJob: (job: Job | null, seconds?: number) => void;
}

export function report(base: string, stage: string) {
  fetch(`${base}/engine/report`, {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify({ device: "Moo app on iPhone", stage }),
  }).catch(() => {});
}

/** Works for the Moo at `base` until `stop()` says true. */
export async function lend(
  base: string,
  name: string,
  run: (job: Job) => Promise<JobResult>,
  stop: () => boolean,
  ev: LoopEvents
) {
  let engineId = "";
  const hello = async () => {
    const r = await fetch(`${base}/engine/hello`, {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify({ name, device: "Moo app (React Native)" }),
    });
    engineId = (await r.json()).engine_id;
  };
  while (!stop()) {
    let job: Job | null = null;
    try {
      if (!engineId) await hello();
      const r = await fetch(`${base}/engine/next?engine_id=${engineId}`);
      if (r.status === 404) {
        engineId = "";
        continue;
      }
      job = (await r.json()).job;
      ev.onState("Connected to the Moo");
    } catch {
      ev.onState("Cannot reach the Moo. Trying again…");
      engineId = "";
      await new Promise((res) => setTimeout(res, 3000));
      continue;
    }
    if (!job) continue;
    ev.onJob(job);
    const started = Date.now();
    let body: Record<string, unknown>;
    try {
      body = { engine_id: engineId, job_id: job.id, ok: true, result: await run(job) };
    } catch (e: any) {
      body = { engine_id: engineId, job_id: job.id, ok: false, error: String(e?.message ?? e) };
      report(base, `error in ${job.kind}: ${String(e?.message ?? e).slice(0, 100)}`);
    }
    try {
      await fetch(`${base}/engine/result`, { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
    } catch {
      /* the Moo will time the job out */
    }
    ev.onJob(null, (Date.now() - started) / 1000);
  }
}
