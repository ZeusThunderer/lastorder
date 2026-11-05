// misaka-link (T-048, ADR-042): the environment's own delivery channel into the
// live `hapi pi` session — stdin belongs to HAPI, so the board feed, Misaka-net
// and the network's envelopes reach the agent only through this extension
// (TASKBOARD.md section 11). It delivers; it adds no agent tools and no
// behaviour rules (ADR-032, ADR-041). The identity is the one mcp.json already
// carries (the node key); the naming preset comes from the workplace's own
// PERSONA.md, never hardcoded.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { makeNameOf, parseNaming } from "./naming.ts";
import { HiveClient } from "./hive.ts";
import { LinkCore } from "./link.ts";

const POLL_S = 10_000; // ms; the section-11 ceiling is 15 s

function log(type: string, payload: Record<string, unknown> = {}): void {
  // the extension's journal: one JSON line on Pi's stderr (docker logs)
  console.error(JSON.stringify({ ts: new Date().toISOString(), type, ...payload }));
}

interface LinkConfig {
  hive: HiveClient;
  nameOf: ReturnType<typeof makeNameOf>;
  serial: number | null;
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
  const base = url.replace(/\/node\/mcp\/?$/, "");

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
  };
}

export default function misakaLink(pi: unknown) {
  const api = pi as {
    on(event: string, handler: (event: unknown, ctx: unknown) => unknown): void;
    sendUserMessage(content: string, options: { deliverAs: "steer" | "followUp" }): void;
  };
  let stopped = false;
  let running: Promise<void> | null = null;

  api.on("session_start", async (_event: unknown, ctxRaw: unknown) => {
    const ctx = ctxRaw as {
      cwd: string;
      isIdle(): boolean;
    };
    const agentDir = process.env.PI_CODING_AGENT_DIR || path.join(os.homedir(), ".pi", "agent");
    const config = readLinkConfig(agentDir, ctx.cwd);
    if (config === null) return;
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
    });
    log("misakalink.start", { agentDir, serial: config.serial });
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
        await new Promise(resolve => setTimeout(resolve, Math.max(0, pause)));
      }
    })();
  });

  api.on("session_shutdown", () => {
    stopped = true; // the loop finishes its sleep and exits; no in-flight re-schedule
  });
}
