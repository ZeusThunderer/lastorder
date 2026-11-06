// One life's delivery loop: hive feed + Misaka-net inbox → one Pi message → ack,
// with the section-11 at-least-once rules (the runner bridge's contract,
// reused): the ack cursor moves only after Pi took the batch, the last 200
// `#id:seq` pairs absorb the hive's repeats, and a down hive backs off to 60 s
// and raises delivery_failing once it answers again. No board semantics, no
// behaviour — the rules live in the sister's instructions (section 13, ADR-032).

import { envelopeLines, netEnvelope, wakeEnvelope, type NameOf, type BoardEvent } from "./envelope.ts";
import {
  HiveRejected,
  HiveUnavailable,
  type FeedResponse,
  type InboxMessage,
  type WakeEvent,
} from "./hive.ts";

export const POLL_S = 10.0; // the healthy feed pace; the section-11 ceiling is 15 s
export const RETRY_PAUSE_S = 2.0; // the hive is down: 2, 4, 8 ... capped
export const RETRY_MAX_S = 60.0;
export const ALARM_AFTER = 5; // consecutive failures before delivery_failing
export const SEEN_MAX = 200; // the #id:seq pairs one life remembers

/** The hive surface the loop needs (HiveClient implements it; tests fake it). */
export interface HiveLink {
  feed(cursor: number | null): Promise<FeedResponse>;
  ack(seq: number): Promise<void>;
  alarmDeliveryFailing(text: string, slot: string, serial: number | null): Promise<void>;
  inbox(): Promise<InboxMessage[]>;
  wakeFeed(cursor: number | null): Promise<{ events: WakeEvent[]; cursor: number; top: number }>;
  wakeAck(seq: number): Promise<void>;
}

export interface Sender {
  /** pi.sendUserMessage — a throw means Pi refused; the cursor must not move. */
  send(text: string, deliverAs: "steer" | "followUp"): void;
  /** ctx.isIdle — followUp when free, steer while the agent works. */
  isIdle(): boolean;
}

export type Journal = (type: string, payload?: Record<string, unknown>) => void;

export class LinkCore {
  private readonly hive: HiveLink;
  private readonly sender: Sender;
  private readonly nameOf: NameOf;
  private readonly log: Journal;
  readonly pollS: number;
  private cursor: number | null = null; // null: a new life, the hive snapshots once
  private readonly seen = new Map<string, null>();
  private failures = 0;
  private alarmPending = 0;
  private slot: string;
  private serial: number | null;
  /** The desk's wake feed rides the SAME loop, desk sessions only (T-043). */
  private readonly desk: boolean;
  private wakeCursor: number | null = null;
  private readonly wakeSeen = new Set<number>();

  constructor(opts: {
    hive: HiveLink;
    sender: Sender;
    nameOf: NameOf;
    journal?: Journal;
    pollS?: number;
    slot?: string;
    serial?: number | null;
    desk?: boolean;
  }) {
    this.hive = opts.hive;
    this.sender = opts.sender;
    this.nameOf = opts.nameOf;
    this.log = opts.journal ?? (() => {});
    this.pollS = opts.pollS ?? POLL_S;
    this.slot = opts.slot ?? "";
    this.serial = opts.serial ?? null;
    this.desk = opts.desk ?? false;
  }

  private deliver(text: string): void {
    this.sender.send(text, this.sender.isIdle() ? "followUp" : "steer");
  }

  private fail(): number {
    this.failures += 1;
    // one alarm per outage: arm only when the counter crosses the threshold
    if (this.failures === ALARM_AFTER) this.alarmPending = this.failures;
    return Math.min(RETRY_PAUSE_S * 2 ** (this.failures - 1), RETRY_MAX_S);
  }

  private async alarmIfPending(): Promise<void> {
    if (!this.alarmPending) return;
    // delivery_failing waits for the hive to answer (section 11): send it now
    try {
      await this.hive.alarmDeliveryFailing(
        `board delivery to slot ${this.slot || "?"} failed ${this.alarmPending} times in a row`,
        this.slot,
        this.serial
      );
      this.log("misakalink.alarm", { failures: this.alarmPending });
      this.alarmPending = 0;
    } catch (err) {
      // the alarm rides with the next successful answer; a rejection means the
      // key cannot raise it at all — stop retrying every cycle
      if (err instanceof HiveRejected) this.alarmPending = 0;
      this.log("misakalink.alarm_failed", { error: String(err) });
    }
  }

  private async feedAnswered(): Promise<void> {
    // the failure counter belongs to the feed (R1-3): a working inbox must not
    // reset it while the feed itself is down, or the alarm never fires
    await this.alarmIfPending();
    if (this.failures) this.log("misakalink.recovered", { after: this.failures });
    this.failures = 0;
  }

  /** One inbox + feed → session → ack cycle; returns the seconds to the next one. */
  async pollOnce(): Promise<number> {
    // Misaka-net first: the inbox is drained one-shot, whatever we take must go
    // out in this same cycle or it is lost.
    try {
      const messages = await this.hive.inbox();
      await this.alarmIfPending(); // the hive answered — a pending alarm goes out
      if (messages.length) {
        const lines = messages.map(m => netEnvelope(m, this.nameOf));
        try {
          this.deliver(lines.join("\n"));
          this.log("misakalink.net_delivered", { lines: lines.length });
        } catch (err) {
          // taken from the inbox and never delivered — lost for good (section 11)
          this.log("misakalink.net_lost", { lines: lines.length, error: String(err) });
        }
      }
    } catch (err) {
      if (err instanceof HiveUnavailable) {
        this.log("misakalink.net_failed", { attempt: this.failures + 1, error: String(err) });
        return this.fail();
      }
      // a permanent refusal (an unknown key, no live node) is a configuration
      // matter, not a delivery outage — the life goes on at the healthy pace
      if (err instanceof HiveRejected) {
        this.log("misakalink.net_refused", { error: String(err) });
      } else {
        throw err;
      }
    }

    // the desk's wake feed (T-043): the environment's own wake reasons — the same
    // at-least-once contract, its own cursor, its own envelope kind
    if (this.desk) {
      let wake;
      try {
        wake = await this.hive.wakeFeed(this.wakeCursor);
      } catch (err) {
        if (err instanceof HiveUnavailable) {
          this.log("misakalink.wake_failed", { attempt: this.failures + 1, error: String(err) });
          return this.fail();
        }
        if (err instanceof HiveRejected) {
          this.log("misakalink.wake_refused", { error: String(err) });
          return this.pollS;
        }
        throw err;
      }
      await this.alarmIfPending();
      if (wake.top > 0 && this.wakeCursor !== null && wake.top < this.wakeCursor) {
        // the feed was rebuilt behind our cursor: start it over
        this.wakeCursor = null;
        this.wakeSeen.clear();
        this.log("misakalink.wake_reset", { cursor: wake.top });
        wake = await this.hive.wakeFeed(null);
      }
      const fresh = wake.events.filter(e => !this.wakeSeen.has(e.seq));
      if (fresh.length) {
        const lines = fresh.map(e => wakeEnvelope(e));
        try {
          this.deliver(lines.join("\n"));
          for (const event of fresh) this.rememberWake(event.seq);
          this.log("misakalink.wake_delivered", { lines: lines.length });
        } catch (err) {
          this.log("misakalink.wake_send_failed", { lines: lines.length, error: String(err) });
          return this.pollS;
        }
      }
      const top = Math.max(...wake.events.map(e => Number(e.seq) || 0), 0);
      if (top) {
        try {
          await this.hive.wakeAck(top); // only after Pi took the batch
          this.wakeCursor = top;
        } catch (err) {
          if (!(err instanceof HiveRejected)) this.log("misakalink.wake_ack_failed", { error: String(err) });
        }
      }
    }

    let response;
    try {
      response = await this.hive.feed(this.cursor);
    } catch (err) {
      if (err instanceof HiveUnavailable) {
        this.log("misakalink.feed_failed", { attempt: this.failures + 1, error: String(err) });
        return this.fail();
      }
      if (err instanceof HiveRejected) {
        this.log("misakalink.feed_refused", { error: String(err) });
        return this.pollS;
      }
      throw err;
    }
    await this.feedAnswered();

    if (!response.events.length) {
      // An empty snapshot still needs its ack: the cursor row is created by the
      // ack alone, and without it the hive answers every poll with the snapshot
      // — open tasks only, no messages or replies, ever.
      if (response.snapshot) {
        try {
          await this.hive.ack(response.cursor);
          this.cursor = response.cursor;
        } catch (err) {
          if (!(err instanceof HiveRejected)) this.log("misakalink.ack_failed", { error: String(err) });
        }
      }
      return this.pollS;
    }

    const fresh = response.events.filter(e => !this.seen.has(key(e)));
    const top = Math.max(...response.events.map(e => Number(e.seq) || 0));
    if (fresh.length) {
      const lines = envelopeLines(fresh as BoardEvent[], this.nameOf);
      try {
        this.deliver(lines.join("\n"));
      } catch (err) {
        // no ack, no seen: the next cycle delivers the same batch again
        this.log("misakalink.send_failed", { lines: lines.length, error: String(err) });
        return this.pollS;
      }
      for (const event of fresh) this.remember(key(event));
      this.log("misakalink.delivered", { lines: lines.length, seq: top });
    }
    try {
      await this.hive.ack(top); // only after Pi took the batch
    } catch (err) {
      if (!(err instanceof HiveRejected)) this.log("misakalink.ack_failed", { error: String(err) });
      return this.pollS;
    }
    this.cursor = top;
    return this.pollS;
  }

  private remember(key: string): void {
    this.seen.delete(key);
    this.seen.set(key, null);
    while (this.seen.size > SEEN_MAX) {
      const oldest = this.seen.keys().next();
      if (oldest.done) break;
      this.seen.delete(oldest.value);
    }
  }

  private rememberWake(seq: number): void {
    this.wakeSeen.add(seq);
    while (this.wakeSeen.size > SEEN_MAX) {
      const oldest = this.wakeSeen.values().next();
      if (oldest.done) break;
      this.wakeSeen.delete(oldest.value);
    }
  }
}

function key(event: { entry_id: unknown; seq: unknown }): string {
  return `${event.entry_id}:${event.seq}`;
}

export { key as eventKey };
