// One section-11 envelope line per event, on the exact format the temporary
// runner bridge already delivers (runtime/node/boardlink.py — the spec's single
// envelope format, reused per the supervision note). The prefix is built from the
// structured fields alone: the environment stamps the source, and the author's
// text is whitespace-collapsed onto the same line so it can never break into a
// second line and pose as another envelope.

export type NameOf = (actor: unknown) => string;

export interface BoardEvent {
  seq: number | string;
  entry_id: number | string;
  op: string;
  actor: unknown;
  before?: unknown;
  after?: unknown;
  entry?: {
    kind?: unknown;
    from?: unknown;
    to?: unknown;
    status?: unknown;
    claimed_by?: unknown;
    text?: unknown;
  };
}

export interface NetMessage {
  from_serial: number | string;
  text: unknown;
}

/** The author's text on one line: every whitespace run collapses. */
export function collapse(value: unknown): string {
  return String(value ?? "").split(/\s+/).filter(Boolean).join(" ");
}

function obj(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** One section-11 line for one feed event, or null for an op we do not speak. */
export function boardEnvelope(event: BoardEvent, nameOf: NameOf): string | null {
  const op = String(event.op ?? "");
  const entry = obj(event.entry);
  const entryId = event.entry_id;
  const kind = String(entry.kind ?? "entry");
  const after = obj(event.after);
  const actor = nameOf(event.actor);
  if (op === "post" || op === "snapshot") {
    const head =
      `[board #${entryId} ${kind} · from ${nameOf(entry.from)}` +
      ` · to ${nameOf(entry.to)} · ${entry.status ?? "?"}]`;
    const body = collapse(entry.text);
    return body ? `${head} ${body}` : head;
  }
  if (op === "claim") {
    const who = nameOf(after.claimed_by) !== "?" ? nameOf(after.claimed_by) : actor;
    return `[board #${entryId} · taken by ${who}]`;
  }
  if (op === "reply") {
    const head = `[board #${entryId} ${kind} · reply by ${actor}]`;
    const body = collapse(after.text);
    return body ? `${head} ${body}` : head;
  }
  if (op === "transition") {
    const head = `[board #${entryId} ${kind} · ${after.status ?? entry.status ?? "?"} by ${actor}]`;
    const body = collapse(after.text);
    return body ? `${head} ${body}` : head;
  }
  if (op === "readdress") {
    return `[board #${entryId} · to ${nameOf(after.to ?? entry.to)}]`;
  }
  return null;
}

/** One Misaka-net message as a single envelope line. */
export function netEnvelope(message: NetMessage, nameOf: NameOf): string {
  const head = `[net from ${nameOf(message.from_serial)}]`;
  const body = collapse(message.text);
  return body ? `${head} ${body}` : head;
}

export const LINES_MAX = 10;

/** At most `limit` envelope lines; the rest becomes the marker line (section 11). */
export function capLines(lines: string[], limit: number = LINES_MAX): string[] {
  if (lines.length <= limit) return [...lines];
  return [...lines.slice(0, limit), `… ${lines.length - limit} more, \`board\``];
}

export function envelopeLines(
  events: BoardEvent[],
  nameOf: NameOf,
  limit: number = LINES_MAX
): string[] {
  const lines: string[] = [];
  for (const event of events) {
    const line = boardEnvelope(event, nameOf);
    if (line !== null) lines.push(line);
  }
  return capLines(lines, limit);
}
