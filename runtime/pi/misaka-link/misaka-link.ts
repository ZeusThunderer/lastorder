// misaka-link (T-048, ADR-042): the environment's own delivery channel into the
// live `hapi pi` session — stdin belongs to HAPI, so the board feed, Misaka-net
// and the network's envelopes reach the agent only through this extension
// (TASKBOARD.md section 11). It delivers; it adds no agent tools and no
// behaviour rules (ADR-032, ADR-041) — except two guards: no long `sleep` that
// would hold the delivered messages, and no call repeated in a loop. The identity is the one mcp.json already
// carries (the node key); the naming preset comes from the workplace's own
// PERSONA.md, never hardcoded.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { makeNameOf, parseNaming } from "./naming.ts";
import { HiveClient } from "./hive.ts";
import { LinkCore } from "./link.ts";

const POLL_S = 10_000; // ms; the section-11 ceiling is 15 s
export const SLEEP_MAX_S = 30;
const IDLE_POLL_MS = 120_000; // the desk only: see the loop

/** Strip reasoning_content from assistant messages before the last user message
 * (the current turn keeps its own) and let Z.ai clear it too. Mutates the payload. */
export function dropOldReasoning(event: unknown): unknown {
  const payload = (event as { payload?: unknown })?.payload as
    | { messages?: Array<Record<string, unknown>>; thinking?: Record<string, unknown> }
    | undefined;
  if (!payload || !Array.isArray(payload.messages)) return undefined;
  let lastUser = -1;
  payload.messages.forEach((m, i) => {
    if (m?.role === "user") lastUser = i;
  });
  for (let i = 0; i < lastUser; i++) {
    const m = payload.messages[i]!;
    if (m?.role === "assistant") delete m.reasoning_content;
  }
  if (payload.thinking && typeof payload.thinking === "object") payload.thinking.clear_thinking = true;
  return payload;
}

/** The same call (name + input) REPEAT_MAX times in a row within one run is a loop,
 * not work — Gemini CLI (5 in a row), OpenHands and Goose stop it the same way. The
 * body's wait_events is the one call made to repeat. */
export const REPEAT_MAX = 5;
export class RepeatGuard {
  private last = "";
  private count = 0;
  reset(): void {
    this.last = "";
    this.count = 0;
  }
  /** True when this call should be refused. */
  check(toolName: string, input: unknown): boolean {
    if (/wait_events$/.test(toolName)) return false;
    const key = toolName + JSON.stringify(input ?? null);
    this.count = key === this.last ? this.count + 1 : 1;
    this.last = key;
    return this.count >= REPEAT_MAX;
  }
}

/** The longest `sleep N` in a shell command, in seconds (N, Ns, Nm, Nh). */
export function longestSleep(command: string): number {
  const unit: Record<string, number> = { "": 1, s: 1, m: 60, h: 3600 };
  let longest = 0;
  for (const m of command.matchAll(/\bsleep\s+(\d+(?:\.\d+)?)([smh]?)\b/g)) {
    longest = Math.max(longest, Number(m[1]) * unit[m[2]!]!);
  }
  return longest;
}

function log(type: string, payload: Record<string, unknown> = {}): void {
  // the extension's journal: one JSON line on Pi's stderr (docker logs)
  console.error(JSON.stringify({ ts: new Date().toISOString(), type, ...payload }));
}

interface LinkConfig {
  hive: HiveClient;
  nameOf: ReturnType<typeof makeNameOf>;
  serial: number | null;
  desk: boolean;
}

/** The hive MCP url + node key from the agent dir's mcp.json (rendered by the
 * runner, root-owned 0444 — the same identity the session's own hive MCP uses). */
function readLinkConfig(agentDir: string, cwd: string): LinkConfig | null {
  let server: { url?: unknown; headers?: Record<string, unknown> } = {};
  try {
    const mcp = JSON.parse(fs.readFileSync(path.join(agentDir, "mcp.json"), "utf8"));
    server = mcp?.mcpServers?.hive ?? {};
  } catch (err) {
    log("misakalink.no_mcp_json", { agentDir, error: String(err) });
    return null;
  }
  const url = typeof server.url === "string" ? server.url : "";
  const key = String(server.headers?.["X-Node-Key"] ?? "");
  if (!url || !key) {
    log("misakalink.no_hive_server", { agentDir });
    return null;
  }
  // a sister's mount ends /node/mcp, the desk's /last-order/mcp (T-043): the HTTP
  // base is the url minus whichever MCP mount it rides
  const base = url.replace(/\/(node|last-order)\/mcp\/?$/, "");

  let naming = { prefix: null as string | null, serial: null as number | null };
  try {
    naming = parseNaming(fs.readFileSync(path.join(cwd, "PERSONA.md"), "utf8"));
  } catch (err) {
    log("misakalink.no_persona", { cwd, error: String(err) }); // bare serials in envelopes
  }
  const serial = naming.serial;
  return {
    hive: new HiveClient({ base, mcpUrl: url, key }),
    nameOf: makeNameOf(naming),
    serial,
    // the desk's own MCP mount rides the same agent-dir shape (T-043): its
    // session also reads the wake feed; a sister's does not
    desk: /\/last-order\/mcp\/?$/.test(url),
  };
}

export default function misakaLink(pi: unknown) {
  const api = pi as {
    setModel(model: unknown): Promise<boolean>;
    on(event: string, handler: (event: unknown, ctx: unknown) => unknown): void;
    sendUserMessage(content: string, options: { deliverAs: "steer" | "followUp" }): void;
  };
  let stopped = false;
  let running: Promise<void> | null = null;

  api.on("session_start", async (_event: unknown, ctxRaw: unknown) => {
    const ctx = ctxRaw as {
      cwd: string;
      modelRegistry: { find(provider: string, id: string): unknown };
      isIdle(): boolean;
    };
    const agentDir = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");
    // A resumed session can retain an obsolete provider/model. Honour the
    // operator-rendered defaults before delivering the first network event.
    const settingsPath = path.join(agentDir, "settings.json");
    if (fs.existsSync(settingsPath)) {
      const settings = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
      const selected = ctx.modelRegistry.find(settings.defaultProvider, settings.defaultModel);
      if (selected) await api.setModel(selected);
    }
    const config = readLinkConfig(agentDir, ctx.cwd);
    if (config === null) return;
    // the slot name for the delivery_failing alarm: root-rendered file beside the
    // configs (T-045, the T-048 R1-4 note); absent → the alarm stays slot-less
    let slot = "";
    try {
      slot = fs.readFileSync(path.join(agentDir, "slot"), "utf8").trim();
    } catch {
      // an older render: the serial still identifies the node
    }
    const sender = {
      send: (text: string, deliverAs: "steer" | "followUp") =>
        api.sendUserMessage(text, { deliverAs }),
      isIdle: () => ctx.isIdle(),
    };
    const link = new LinkCore({
      hive: config.hive,
      sender,
      nameOf: config.nameOf,
      journal: (type, payload) => log(type, payload),
      serial: config.serial,
      slot,
      desk: config.desk,
    });
    log("misakalink.start", { agentDir, serial: config.serial, slot: slot || null, desk: config.desk });
    stopped = false;
    running = (async () => {
      while (!stopped) {
        let pause = link.pollS * 1000;
        try {
          pause = (await link.pollOnce()) * 1000;
        } catch (err) {
          // a bug or a bad payload must not end delivery for the rest of the life
          log("misakalink.cycle_failed", { error: String(err) });
        }
        // an idle desk takes one batch per IDLE_POLL_MS: every delivery to an idle
        // session is a whole turn (2026-11-13: "Молчу." to each line, every 10 s);
        // the lines wait in the hive, a working session still gets them as steer
        if (config.desk && ctx.isIdle()) pause = Math.max(pause, IDLE_POLL_MS);
        await new Promise(resolve => setTimeout(resolve, Math.max(0, pause)));
      }
    })();
  });

  // A long `sleep` in bash holds every message — the operator's HAPI line waits for
  // the turn's end (2026-11-13: Last Order chained `sleep 540` for an hour). The one
  // guard of this extension: refuse it and end the turn, which IS the wait.
  // 2026-11-13 night: with sleep refused, Last Order "waited" by calling `date` every
  // 2-3 s inside one run — 1,200 calls an hour at ~140k context, a 5-hour quota gone.
  const repeats = new RepeatGuard();
  api.on("agent_start", () => repeats.reset());

  api.on("tool_call", (eventRaw: unknown) => {
    const event = eventRaw as { toolName?: string; input?: { command?: unknown } };
    if (repeats.check(String(event.toolName), event.input)) {
      log("misakalink.repeat_refused", { tool: event.toolName });
      return {
        block: true,
        terminate: true,
        reason:
          `the same call ${REPEAT_MAX} times in a row refused: a repeated call is a loop and ` +
          "every call resends your whole context. To wait, end your turn — the clock and " +
          "any new message wake you.",
      };
    }
    if (event.toolName !== "bash" || typeof event.input?.command !== "string") return;
    if (longestSleep(event.input.command) <= SLEEP_MAX_S) return;
    log("misakalink.sleep_refused", { command: event.input.command.slice(0, 80) });
    return {
      block: true,
      terminate: true,
      reason:
        `sleep over ${SLEEP_MAX_S}s refused: a running tool holds every message. ` +
        "To wait, end your turn — the clock and any new message wake you.",
    };
  });

  // Old reasoning stays out of the request (2026-11-13: thinking was ~48 % of a
  // sister's resent history). Pi asks Z.ai for clear_thinking:false and replays every
  // reasoning_content; Z.ai's own default drops reasoning of past turns — so do we.
  api.on("before_provider_request", (eventRaw: unknown) => dropOldReasoning(eventRaw));

  api.on("session_shutdown", () => {
    stopped = true; // the loop finishes its sleep and exits; no in-flight re-schedule
  });
}
