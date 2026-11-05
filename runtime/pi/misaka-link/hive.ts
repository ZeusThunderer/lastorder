// The extension's hive client: the board HTTP API by node key (section 9) plus
// Misaka-net through the sister's own MCP mount (/node/mcp, X-Node-Key — the same
// identity the HTTP inbox would refuse, it speaks only the runner key). Plain
// fetch, no dependencies: the sandbox installs nothing.

export type FetchFn = (url: string, init: RequestInit) => Promise<Response>;

export class HiveUnavailable extends Error {}
export class HiveRejected extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export interface FeedResponse {
  events: BoardEventLike[];
  cursor: number;
  snapshot: boolean;
}

export interface BoardEventLike {
  seq: number | string;
  entry_id: number | string;
  [key: string]: unknown;
}

export interface InboxMessage {
  from_serial: number | string;
  text: unknown;
}

const TIMEOUT_MS = 10_000;

export class HiveClient {
  private mcpReady = false;
  private mcpSessionId: string | null = null;
  private mcpId = 0;

  readonly opts: {
    base: string;
    mcpUrl: string;
    key: string;
    fetchFn?: FetchFn;
    timeoutMs?: number;
  };

  constructor(opts: {
    /** The hive HTTP base, e.g. http://hive:8000 . */
    base: string;
    /** The sister's MCP url, e.g. http://hive:8000/node/mcp . */
    mcpUrl: string;
    /** The node key (X-Node-Key). */
    key: string;
    fetchFn?: FetchFn;
    timeoutMs?: number;
  }) {
    this.opts = opts;
  }

  private get fetch(): FetchFn {
    return this.opts.fetchFn ?? (url => fetch(url));
  }

  private async request(
    method: string,
    path: string,
    body?: unknown,
    headers: Record<string, string> = {}
  ): Promise<Response> {
    const init: RequestInit = {
      method,
      headers: {
        "X-Node-Key": this.opts.key,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...headers,
      },
      signal: AbortSignal.timeout(this.opts.timeoutMs ?? TIMEOUT_MS),
    };
    if (body !== undefined) init.body = JSON.stringify(body);
    let response: Response;
    try {
      response = await this.fetch(this.opts.base.replace(/\/+$/, "") + path, init);
    } catch (err) {
      if (err instanceof HiveRejected || err instanceof HiveUnavailable) throw err;
      throw new HiveUnavailable(`hive ${path}: ${String((err as Error)?.message ?? err)}`);
    }
    if (response.status >= 500) {
      throw new HiveUnavailable(`hive ${path}: HTTP ${response.status}`);
    }
    if (!response.ok) {
      throw new HiveRejected(`hive ${path}: HTTP ${response.status} ${await safeText(response)}`, response.status);
    }
    return response;
  }

  /** GET /board/feed?since= — the board events after the cursor. */
  async feed(cursor: number | null): Promise<FeedResponse> {
    const path = cursor === null ? "/board/feed" : `/board/feed?since=${cursor}`;
    const response = await this.request("GET", path);
    const data = (await response.json()) as Record<string, unknown>;
    return {
      events: Array.isArray(data.events) ? (data.events as BoardEventLike[]) : [],
      cursor: Number(data.cursor ?? 0) || 0,
      snapshot: data.snapshot === true,
    };
  }

  /** POST /board/ack — move the feed cursor; only after Pi took the batch. */
  async ack(seq: number): Promise<void> {
    await this.request("POST", "/board/ack", { seq });
  }

  /** POST /board/alarms — delivery_failing; a node key may raise this one type. */
  async alarmDeliveryFailing(text: string, slot: string, serial: number | null): Promise<void> {
    const body: Record<string, unknown> = { type: "delivery_failing", text, slot };
    if (serial !== null) body.serial = serial;
    await this.request("POST", "/board/alarms", body);
  }

  // --- Misaka-net over the sister's stateless MCP mount --------------------------------

  private async mcpRpc(method: string, params?: unknown, notify = false): Promise<unknown | null> {
    const id = notify ? undefined : ++this.mcpId;
    const body: Record<string, unknown> = { jsonrpc: "2.0", method };
    if (params !== undefined) body.params = params;
    if (id !== undefined) body.id = id;
    const headers: Record<string, string> = { Accept: "application/json, text/event-stream" };
    if (this.mcpSessionId !== null) headers["mcp-session-id"] = this.mcpSessionId;
    let response: Response;
    try {
      response = await this.fetch(this.opts.mcpUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Node-Key": this.opts.key,
          ...headers,
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(this.opts.timeoutMs ?? TIMEOUT_MS),
      });
    } catch (err) {
      throw new HiveUnavailable(`hive mcp ${method}: ${String((err as Error)?.message ?? err)}`);
    }
    const sid = response.headers.get("mcp-session-id");
    if (sid) this.mcpSessionId = sid;
    if (response.status >= 500) {
      throw new HiveUnavailable(`hive mcp ${method}: HTTP ${response.status}`);
    }
    if (!response.ok) {
      throw new HiveRejected(
        `hive mcp ${method}: HTTP ${response.status} ${await safeText(response)}`,
        response.status
      );
    }
    if (notify || id === undefined) return null;
    const payload = parseSseData(await response.text()).find(
      doc => doc && typeof doc === "object" && "result" in (doc as object)
    );
    const doc = payload as { result?: unknown; error?: { message?: unknown } } | undefined;
    if (!doc) throw new HiveUnavailable(`hive mcp ${method}: no result in the response`);
    if (doc.error) throw new HiveRejected(`hive mcp ${method}: ${String(doc.error.message ?? "error")}`, 0);
    return doc.result;
  }

  /** tools/call inbox over /node/mcp — the one-shot Misaka-net drain. */
  async inbox(): Promise<InboxMessage[]> {
    if (!this.mcpReady) {
      await this.mcpRpc("initialize", {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "misaka-link", version: "1.0.0" },
      });
      await this.mcpRpc("notifications/initialized", undefined, true);
      this.mcpReady = true;
    }
    const result = (await this.mcpRpc("tools/call", {
      name: "inbox",
      arguments: {},
    })) as { content?: { text?: unknown }[]; isError?: boolean } | null;
    const text = result?.content?.[0]?.text;
    if (result?.isError) throw new HiveRejected(String(text ?? "inbox failed"), 0);
    const data = JSON.parse(String(text ?? "{}")) as { messages?: unknown };
    return Array.isArray(data.messages)
      ? (data.messages as InboxMessage[]).filter(m => m && typeof m === "object")
      : [];
  }
}

async function safeText(response: Response): Promise<string> {
  try {
    return (await response.text()).slice(0, 200);
  } catch {
    return "";
  }
}

/** Pull every `data:` JSON payload out of an SSE body (the MCP POST answers SSE). */
export function parseSseData(text: string): unknown[] {
  const out: unknown[] = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("data:")) continue;
    const payload = line.slice(5).trim();
    if (!payload) continue;
    try {
      out.push(JSON.parse(payload));
    } catch {
      // a keep-alive or a partial frame — skip it
    }
  }
  return out;
}
