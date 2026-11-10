// workshop (T-047, ADR-041): the environment's loader for the agent's own Pi
// extensions. Pi refuses to start when any extension fails to load, so Pi only
// loads the ACTIVE dir (settings.json), and an extension gets there only after
// it loaded cleanly in a throwaway `pi -ne -e <entry>`. The runner/desk does the
// same check before every session (runtime/node/workshop.py); this half lets the
// agent do it mid-session: the `workshop_reload` tool checks, rebuilds the active
// dir and queues `/workshop-reload`, which reloads the runtime (tools cannot call
// ctx.reload(); the queued command is Pi's own pattern, examples/extensions/
// reload-runtime.ts). Mechanics only: what to build is the agent's (ADR-032).

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFile } from "node:child_process";

const ENTRIES = ["index.ts", "index.js"];
const CHECK_TIMEOUT_MS = 30_000;

interface Source { dir: string; prefix: string }
interface Config { active: string; sources: Source[] }
interface Row { tool: string; from: string; ok: boolean; error?: string }

function log(type: string, payload: Record<string, unknown> = {}): void {
  console.error(JSON.stringify({ ts: new Date().toISOString(), type, ...payload }));
}

function agentDir(): string {
  return process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");
}

function readConfig(): Config | null {
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(agentDir(), "workshop.json"), "utf8"));
    if (typeof raw?.active !== "string" || !Array.isArray(raw?.sources)) return null;
    return raw as Config;
  } catch {
    return null;
  }
}

function entryOf(dir: string): string | null {
  for (const name of ENTRIES) {
    const p = path.join(dir, name);
    try {
      const st = fs.lstatSync(p);
      if (st.isFile() && st.size > 0) return p;
    } catch { /* absent */ }
  }
  return null;
}

export function check(entry: string): Promise<string | null> {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "pi-check-"));
  return new Promise((resolve) => {
    const child = execFile(
      "pi",
      ["--mode", "rpc", "--offline", "--no-session", "-ne", "-e", entry],
      {
        timeout: CHECK_TIMEOUT_MS,
        cwd: home,
        env: { PATH: process.env.PATH ?? "", HOME: home, PI_CODING_AGENT_DIR: home },
      },
      (err: any, stdout: string, stderr: string) => {
        fs.rmSync(home, { recursive: true, force: true });
        const out = `${stderr ?? ""}${stdout ?? ""}`;
        const line = out
          .split("\n")
          .find((l) => l.includes("Failed to load extension") || l.startsWith("Error:"));
        if (line) return resolve(line.trim().slice(0, 500));
        if (err?.killed) return resolve(`the extension did not finish loading in ${CHECK_TIMEOUT_MS / 1000} s`);
        if (err && typeof err.code === "number") return resolve(`pi exited ${err.code}: ${out.trim().slice(-300)}`);
        resolve(null);
      },
    );
    child.stdin?.end();
  });
}

function copyTool(src: string, dst: string): void {
  fs.cpSync(src, dst, {
    recursive: true,
    dereference: false,
    filter: (p: string) => !path.basename(p).startsWith(".") && path.basename(p) !== "node_modules",
  });
}

export async function rebuild(
  config: Config,
  cwd: string,
  checker: (entry: string) => Promise<string | null> = check,
): Promise<Row[]> {
  const rows: Row[] = [];
  const staging = `${config.active}.next`;
  fs.rmSync(staging, { recursive: true, force: true });
  fs.mkdirSync(staging, { recursive: true });
  const own = new Set<string>();
  for (const source of config.sources) {
    const dir = path.isAbsolute(source.dir) ? source.dir : path.join(cwd, source.dir);
    const from = source.prefix.replace(/-$/, "");
    let names: string[] = [];
    try { names = fs.readdirSync(dir).sort(); } catch { continue; }
    for (const name of names) {
      const toolDir = path.join(dir, name);
      let st: fs.Stats;
      try { st = fs.lstatSync(toolDir); } catch { continue; }
      if (!st.isDirectory() || name.startsWith(".")) continue;
      if (from === "net" && own.has(name)) {
        rows.push({ tool: name, from, ok: false, error: "shadowed by your own workshop tool of the same name" });
        continue;
      }
      const entry = entryOf(toolDir);
      if (!entry) {
        rows.push({ tool: name, from, ok: false, error: `no entry point ${ENTRIES.join(" or ")}` });
        continue;
      }
      const error = await checker(entry);
      rows.push(error ? { tool: name, from, ok: false, error } : { tool: name, from, ok: true });
      if (!error) {
        copyTool(toolDir, path.join(staging, `${source.prefix}${name}`));
        if (from === "own") own.add(name);
      }
    }
  }
  fs.rmSync(config.active, { recursive: true, force: true });
  fs.renameSync(staging, config.active);
  return rows;
}

export function summary(rows: Row[]): string {
  if (!rows.length) return "workshop: no extensions found (one directory per tool, index.ts entry)";
  return rows
    .map((r) => (r.ok ? `ok   ${r.from}/${r.tool}` : `FAIL ${r.from}/${r.tool}: ${r.error}`))
    .join("\n");
}

function pendingFile(): string {
  return path.join(agentDir(), "workshop-active.pending");
}

export default function workshop(pi: any): void {
  pi.registerCommand("workshop-reload", {
    description: "Reload the session runtime with the checked workshop extensions",
    handler: async (_args: string, ctx: any) => {
      await ctx.reload();
    },
  });

  pi.on("session_start", (event: any) => {
    if (event?.reason !== "reload") return;
    let note = "";
    try {
      note = fs.readFileSync(pendingFile(), "utf8");
      fs.rmSync(pendingFile(), { force: true });
    } catch {
      return; // a reload the operator typed: nothing to report
    }
    const names = (pi.getAllTools?.() ?? [])
      .filter((t: any) => JSON.stringify(t?.sourceInfo ?? t?.source ?? "").includes("workshop-active"))
      .map((t: any) => t.name);
    log("workshop.reloaded", { tools: names });
    pi.sendMessage(
      {
        customType: "workshop",
        content: `[workshop] runtime reloaded.\n${note}\nworkshop tools now loaded: ${names.join(", ") || "(none registered)"}`,
        display: true,
      },
      { triggerTurn: true },
    );
  });

  pi.registerTool({
    name: "workshop_reload",
    label: "Workshop reload",
    description:
      "Check every Pi extension in your workshop/ (one directory per tool, index.ts entry) and on the " +
      "network's shelf, then reload your session with the ones that load. A broken extension is left " +
      "out and its error is returned here; your session keeps running.",
    parameters: { type: "object", properties: {}, additionalProperties: false },
    async execute(_id: string, _params: unknown, _signal: unknown, _onUpdate: unknown, ctx: any) {
      const config = readConfig();
      if (!config) {
        return { content: [{ type: "text", text: "workshop: no workshop.json in the agent dir" }], isError: true };
      }
      const rows = await rebuild(config, ctx?.cwd ?? process.cwd());
      const text = summary(rows);
      fs.writeFileSync(pendingFile(), text);
      log("workshop.check", { rows });
      pi.sendUserMessage("/workshop-reload", { deliverAs: "followUp", expandPromptTemplates: true });
      return { content: [{ type: "text", text: `${text}\n\nreload queued: it runs when this turn ends.` }] };
    },
  });
}
