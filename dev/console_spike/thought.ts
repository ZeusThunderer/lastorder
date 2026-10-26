// T-041 R1-1: two ways to inject an operator "thought" into a Pi session.
// Install into the agent dir of the spike container:
//   docker exec -i node1-pi bash -c 'cat > /home/sister/.pi/agent/extensions/thought.ts && chown 1001:1001 /home/sister/.pi/agent/extensions/thought.ts' < dev/console_spike/thought.ts
//
// /thought <text>  — pi.sendMessage({...role:"assistant"...}); VERIFIED round 1:
//   the entry lands in the session with role "custom" (not "assistant"), and the
//   model reads it as an outside line. Kept as the measured baseline.
// /thought2 <text> — the R1-1 retry: a pending flag consumed by a before_agent_start
//   handler that RETURNS the message, which is added to the next run. The spike
//   log then shows whether the request to the model carries role "assistant".
import fs from "fs";

const FLAG = "/tmp/pi-spike/thought-pending.json";

export default function (pi: any) {
  pi.registerCommand("thought", {
    description: "insert a thought via pi.sendMessage (round-1 baseline)",
    handler: async (args: string, ctx: any) => {
      const text = String(args ?? "").trim();
      if (!text) {
        ctx.ui.notify("usage: /thought <text>", "error");
        return;
      }
      pi.sendMessage({
        role: "assistant",
        content: [{ type: "text", text }],
      });
      ctx.ui.notify(`thought (sendMessage) inserted: ${text.length} chars`, "info");
    },
  });

  pi.registerCommand("thought2", {
    description: "queue a thought for before_agent_start injection as the agent's own line",
    handler: async (args: string, ctx: any) => {
      const text = String(args ?? "").trim();
      if (!text) {
        ctx.ui.notify("usage: /thought2 <text>", "error");
        return;
      }
      fs.writeFileSync(FLAG, JSON.stringify({ text, ts: Date.now() }));
      ctx.ui.notify(`thought (before_agent_start) queued: ${text.length} chars`, "info");
    },
  });

  pi.on("before_agent_start", async (_event: any, _ctx: any) => {
    let pending: { text: string } | null = null;
    try {
      pending = JSON.parse(fs.readFileSync(FLAG, "utf8"));
      fs.unlinkSync(FLAG);
    } catch {
      return;
    }
    return {
      message: {
        role: "assistant",
        content: [{ type: "text", text: pending.text }],
      },
    };
  });
}
