import test from "node:test";
import assert from "node:assert/strict";
import { LinkCore, RETRY_MAX_S, type HiveLink, type Sender } from "./link.ts";
import { HiveRejected, HiveUnavailable, type FeedResponse, type InboxMessage } from "./hive.ts";
import { makeNameOf, parseNaming } from "./naming.ts";

const nameOf = makeNameOf(parseNaming("Мисака 00007"));

interface Sent {
  text: string;
  deliverAs: "steer" | "followUp";
}

class FakeSender implements Sender {
  idle = true;
  sent: Sent[] = [];
  refuse = 0; // throw the next N sends (Pi refused the message)

  send(text: string, deliverAs: "steer" | "followUp"): void {
    if (this.refuse > 0) {
      this.refuse -= 1;
      throw new Error("pi refused");
    }
    this.sent.push({ text, deliverAs });
  }

  isIdle(): boolean {
    return this.idle;
  }
}

function boardEvent(seq: number, entryId: number, text = "hello"): Record<string, unknown> {
  return {
    seq,
    entry_id: entryId,
    op: "post",
    actor: "operator",
    entry: { kind: "task", from: "operator", to: "net", status: "open", text },
  };
}

class FakeHive implements HiveLink {
  feedQueue: (FeedResponse | Error)[] = [];
  inboxQueue: (InboxMessage[] | Error)[] = [];
  acks: number[] = [];
  alarms: { text: string; slot: string; serial: number | null }[] = [];
  feedCalls: number[] = [];

  async feed(cursor: number | null): Promise<FeedResponse> {
    this.feedCalls.push(cursor ?? -1);
    const next = this.feedQueue.shift();
    if (next instanceof Error) throw next;
    return next ?? { events: [], cursor: 0, snapshot: false };
  }

  async ack(seq: number): Promise<void> {
    this.acks.push(seq);
  }

  async alarmDeliveryFailing(text: string, slot: string, serial: number | null): Promise<void> {
    this.alarms.push({ text, slot, serial });
  }

  async inbox(): Promise<InboxMessage[]> {
    const next = this.inboxQueue.shift();
    if (next instanceof Error) throw next;
    return next ?? [];
  }
}

function link(hive: FakeHive, sender: FakeSender) {
  return new LinkCore({ hive, sender, nameOf, pollS: 10, slot: "LastOrder01", serial: 7 });
}

test("idle session delivers as followUp, a working one as steer", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  hive.feedQueue.push({ events: [boardEvent(1, 1)], cursor: 1, snapshot: false });
  sender.idle = true;
  await link(hive, sender).pollOnce();
  assert.equal(sender.sent[0]!.deliverAs, "followUp");

  const hive2 = new FakeHive();
  const sender2 = new FakeSender();
  hive2.feedQueue.push({ events: [boardEvent(1, 1)], cursor: 1, snapshot: false });
  sender2.idle = false;
  await link(hive2, sender2).pollOnce();
  assert.equal(sender2.sent[0]!.deliverAs, "steer");
});

test("the ack cursor moves only after Pi took the batch", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  const batch = { events: [boardEvent(1, 1)], cursor: 1, snapshot: false };
  hive.feedQueue.push(batch, batch); // the hive repeats what was not acked
  sender.refuse = 1; // Pi refuses this one
  const core = link(hive, sender);
  await core.pollOnce();
  assert.deepEqual(hive.acks, []); // no ack, no cursor move
  await core.pollOnce(); // hive repeats (at-least-once), Pi takes it now
  assert.equal(hive.feedCalls[1], -1); // the second attempt asked the same place
  assert.equal(sender.sent.length, 1); // delivered exactly once per refusal-free attempt
  assert.deepEqual(hive.acks, [1]);
});

test("a hive repeat of the same event lands in the session once", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  const repeat = { events: [boardEvent(5, 17)], cursor: 5, snapshot: false };
  hive.feedQueue.push(repeat, repeat); // ack failed between the two polls
  const core = link(hive, sender);
  await core.pollOnce();
  assert.deepEqual(hive.acks, [5]);
  await core.pollOnce(); // the same #17:5 again — already seen
  assert.equal(sender.sent.length, 1);
  assert.equal(sender.sent[0]!.text, "[board #17 task · from operator · to net · open] hello");
});

test("25 events fold into one message of 10 lines plus the marker", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  const events = Array.from({ length: 25 }, (_, i) => boardEvent(100 + i, i + 1, `t${i}`));
  hive.feedQueue.push({ events, cursor: 124, snapshot: false });
  await link(hive, sender).pollOnce();
  assert.equal(sender.sent.length, 1);
  const lines = sender.sent[0]!.text.split("\n");
  assert.equal(lines.length, 11);
  assert.match(lines[10]!, /15 more, `board`/);
  assert.deepEqual(hive.acks, [124]);
});

test("an empty snapshot still gets its ack", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  hive.feedQueue.push({ events: [], cursor: 33, snapshot: true });
  const core = link(hive, sender);
  await core.pollOnce();
  assert.deepEqual(hive.acks, [33]);
  assert.equal(sender.sent.length, 0);
});

test("a down hive backs off to 60 s, then raises delivery_failing once it answers", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  const core = link(hive, sender);
  for (let i = 0; i < 6; i++) hive.inboxQueue.push(new HiveUnavailable("down"));
  for (const expected of [2, 4, 8, 16, 32, RETRY_MAX_S]) {
    assert.equal(await core.pollOnce(), expected);
  }
  // the hive comes back: the pending alarm goes out before anything else
  hive.inboxQueue.push([{ from_serial: 12, text: "back" }]);
  const pause = await core.pollOnce();
  assert.equal(pause, 10);
  assert.equal(hive.alarms.length, 1);
  assert.match(hive.alarms[0]!.text, /times in a row/);
  assert.equal(hive.alarms[0]!.slot, "LastOrder01");
  assert.equal(hive.alarms[0]!.serial, 7);
  assert.equal(sender.sent.length, 1); // the drained message went out with it
  await core.pollOnce(); // healthy again: no second alarm
  assert.equal(hive.alarms.length, 1);
});

test("a refused feed (bad key, dead node) is configuration, not an outage", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  hive.inboxQueue.push(new HiveRejected("unknown node key", 401));
  hive.feedQueue.push(new HiveRejected("slot has no live node", 409));
  assert.equal(await link(hive, sender).pollOnce(), 10); // the healthy pace, no backoff
  assert.deepEqual(hive.alarms, []);
  assert.equal(sender.sent.length, 0);
});

test("Misaka-net arrives as one [net from …] message per drain", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  hive.inboxQueue.push([
    { from_serial: 12, text: "Смотри сюда" },
    { from_serial: 13, text: "и сюда" },
  ]);
  hive.feedQueue.push({ events: [], cursor: 0, snapshot: false });
  await link(hive, sender).pollOnce();
  assert.equal(sender.sent.length, 1);
  assert.deepEqual(sender.sent[0]!.text.split("\n"), [
    "[net from Мисака 00012] Смотри сюда",
    "[net from Мисака 00013] и сюда",
  ]);
});

test("a refused send loses the drained inbox batch — journalled, not silent", async () => {
  const hive = new FakeHive();
  const sender = new FakeSender();
  const journal: string[] = [];
  hive.inboxQueue.push([{ from_serial: 12, text: "gone" }]);
  sender.refuse = 1;
  const core = new LinkCore({
    hive,
    sender,
    nameOf,
    pollS: 10,
    journal: type => journal.push(type),
  });
  await core.pollOnce();
  assert.equal(sender.sent.length, 0);
  assert.ok(journal.includes("misakalink.net_lost"));
  // the inbox is one-shot: the batch is gone, the next drain finds it empty
  await core.pollOnce();
  assert.equal(sender.sent.length, 0);
});
