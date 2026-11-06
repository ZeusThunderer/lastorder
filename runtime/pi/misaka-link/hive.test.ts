import test from "node:test";
import assert from "node:assert/strict";
import { HiveClient, HiveRejected, HiveUnavailable, parseSseData } from "./hive.ts";
import type { FetchFn } from "./hive.ts";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function sseResponse(body: unknown, status = 200): Response {
  return new Response(`event: message\ndata: ${JSON.stringify(body)}\n\n`, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function client(fetchFn: FetchFn): HiveClient {
  return new HiveClient({
    base: "http://hive:8000",
    mcpUrl: "http://hive:8000/node/mcp",
    key: "sk-node-one",
    fetchFn,
  });
}

test("feed sends the node key and the cursor", async () => {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  const hive = client(async (url, init) => {
    calls.push({ url, headers: init.headers as Record<string, string> });
    return jsonResponse({ events: [{ seq: 3, entry_id: 1, op: "post" }], cursor: 3, snapshot: false });
  });
  const response = await hive.feed(3);
  assert.equal(response.events.length, 1);
  assert.equal(response.cursor, 3);
  assert.equal(calls[0]!.url, "http://hive:8000/board/feed?since=3");
  assert.equal(calls[0]!.headers["X-Node-Key"], "sk-node-one");
  // a new life asks with no cursor at all
  await hive.feed(null);
  assert.equal(calls[1]!.url, "http://hive:8000/board/feed");
});

test("server errors are an outage, client refusals are rejections", async () => {
  const down = client(async () => new Response("boom", { status: 503 }));
  await assert.rejects(down.feed(null), HiveUnavailable);
  const refuse = client(async () => jsonResponse({ error: { kind: "node_key" } }, 401));
  try {
    await refuse.feed(null);
    assert.fail("must reject");
  } catch (err) {
    assert.ok(err instanceof HiveRejected);
    assert.equal((err as HiveRejected).status, 401);
  }
  const unreachable = client(async (_url, _init) => {
    throw new TypeError("fetch failed");
  });
  await assert.rejects(unreachable.feed(null), HiveUnavailable);
});

test("ack posts the seq after the batch, alarms carry delivery_failing", async () => {
  const bodies: { path: string; body: unknown }[] = [];
  const hive = client(async (url, init) => {
    const path = url.replace("http://hive:8000", "");
    bodies.push({ path, body: init.body ? JSON.parse(String(init.body)) : null });
    return jsonResponse(null);
  });
  await hive.ack(41);
  await hive.alarmDeliveryFailing("failed 5 times in a row", "LastOrder01", 7);
  assert.deepEqual(bodies[0], { path: "/board/ack", body: { seq: 41 } });
  assert.deepEqual(bodies[1], {
    path: "/board/alarms",
    body: { type: "delivery_failing", text: "failed 5 times in a row", slot: "LastOrder01", serial: 7 },
  });
});

test("inbox: initialize, then the stateless tools/call over the MCP mount", async () => {
  const calls: { method: string; body: Record<string, unknown>; headers: Record<string, string> }[] = [];
  const hive = client(async (url, init) => {
    assert.equal(url, "http://hive:8000/node/mcp");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    calls.push({ method: String(body.method), body, headers: init.headers as Record<string, string> });
    if (body.method === "initialize") {
      return sseResponse({ jsonrpc: "2.0", id: body.id, result: { protocolVersion: "2025-03-26" } });
    }
    if (body.method === "tools/call") {
      return sseResponse({
        jsonrpc: "2.0",
        id: body.id,
        result: {
          content: [{ text: JSON.stringify({ messages: [{ from_serial: 12, to_serial: 7, text: "hi", ts: 1 }] }) }],
        },
      });
    }
    return new Response(null, { status: 202 });
  });
  const messages = await hive.inbox();
  assert.equal(messages.length, 1);
  assert.equal(messages[0]!.from_serial, 12);
  // the handshake happens once; the second drain is a single tools/call
  await hive.inbox();
  assert.deepEqual(
    calls.map(c => c.method),
    ["initialize", "notifications/initialized", "tools/call", "tools/call"]
  );
  assert.equal(calls[2]!.headers["X-Node-Key"], "sk-node-one");
  assert.deepEqual(calls[2]!.body.params, { name: "inbox", arguments: {} });
});

test("an MCP tool error surfaces as a rejection", async () => {
  const hive = client(async (_url, init) => {
    const request = JSON.parse(String(init.body)) as { id?: number; method: string };
    if (request.method === "notifications/initialized") return new Response(null, { status: 202 });
    return sseResponse({
      jsonrpc: "2.0",
      id: request.id,
      result:
        request.method === "initialize"
          ? { protocolVersion: "2025-03-26" }
          : { content: [{ text: "Error executing tool inbox: unknown node key" }], isError: true },
    });
  });
  await assert.rejects(hive.inbox(), /unknown node key/);
});

test("a streamable answer that never EOFs still resolves (the live hive holds the SSE open)", async () => {
  const encoder = new TextEncoder();
  const hive = client(async (_url, init) => {
    const request = JSON.parse(String(init.body)) as { id?: number; method: string };
    if (request.method === "notifications/initialized") return new Response(null, { status: 202 });
    const result =
      request.method === "initialize"
        ? { protocolVersion: "2025-03-26" }
        : { content: [{ text: JSON.stringify({ messages: [{ from_serial: 12, text: "hi" }] }) }] };
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(`event: message\ndata: ${JSON.stringify({
            jsonrpc: "2.0",
            id: request.id,
            result,
          })}\n\n`)
        );
        // and then the server just keeps the stream open, forever
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  });
  const messages = await hive.inbox(); // must return after the first frame
  assert.equal(messages.length, 1);
  assert.equal(messages[0]!.from_serial, 12);
});

test("a hive that accepts the connection and then breaks is an outage (R1-2)", async () => {
  let calls = 0;
  const silent = async () => {
    calls += 1;
    // headers arrive, the MCP body never does
    return new Response(new ReadableStream<Uint8Array>({ start() {} }), {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  };
  const hive = new HiveClient({
    base: "http://hive:8000",
    mcpUrl: "http://hive:8000/node/mcp",
    key: "sk-node-one",
    fetchFn: silent,
    timeoutMs: 40,
  });
  await assert.rejects(hive.inbox(), HiveUnavailable);
  assert.ok(calls >= 1);

  // and a feed body that dies mid-JSON is the same outage
  const encoder = new TextEncoder();
  const feedHive = new HiveClient({
    base: "http://hive:8000",
    mcpUrl: "http://hive:8000/node/mcp",
    key: "sk-node-one",
    fetchFn: async () =>
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(encoder.encode('{"events":[{"seq":1,"entry'));
            controller.close(); // the body ends before the JSON does
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } }
      ),
    timeoutMs: 40,
  });
  await assert.rejects(feedHive.feed(null), HiveUnavailable);
});

test("parseSseData picks the JSON payloads out of SSE frames", () => {
  const docs = parseSseData('event: message\ndata: {"a":1}\n\ndata: not-json\n\ndata: {"b":2}\n');
  assert.deepEqual(docs, [{ a: 1 }, { b: 2 }]);
});

test("the desk's mount (/last-order/mcp) yields the same HTTP base as a sister's", async () => {
  // T-043: the LO agent-dir mcp.json points at /last-order/mcp — the board HTTP
  // base (feed/ack/alarms) is still the hive root, not the MCP path
  const calls: string[] = [];
  const desk = new HiveClient({
    base: "http://hive:8000/last-order/mcp".replace(/\/(node|last-order)\/mcp\/?$/, ""),
    mcpUrl: "http://hive:8000/last-order/mcp",
    key: "sk-lo",
    fetchFn: async url => {
      calls.push(url);
      return jsonResponse({ events: [], cursor: 0, snapshot: false });
    },
  });
  await desk.feed(null);
  assert.equal(calls[0], "http://hive:8000/board/feed");
});
