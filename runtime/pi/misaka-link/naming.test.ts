import test from "node:test";
import assert from "node:assert/strict";
import { makeNameOf, parseNaming } from "./naming.ts";

// The template's anchor line, as the runner renders it into PERSONA.md
// (agent/last_order/prompt_template.md): `- Mind name: Last Order. Your name:
// {node_name}. A nickname is not given at birth: …`
const ANCHOR = (name: string) =>
  `- Mind name: Last Order. Your name: ${name}. A nickname is not given at birth: it may appear during your life.`;

test("both shipped presets are read from the PERSONA.md anchor", () => {
  assert.deepEqual(parseNaming(ANCHOR("Мисака 00007")), { prefix: "Мисака", serial: 7 });
  assert.deepEqual(parseNaming(ANCHOR("узел 00003")), { prefix: "узел", serial: 3 });
});

test("a third, made-up preset works the same — no preset list anywhere", () => {
  assert.deepEqual(parseNaming(ANCHOR("сестра 00042")), { prefix: "сестра", serial: 42 });
  const nameOf = makeNameOf(parseNaming(ANCHOR("сестра 00042")));
  assert.equal(nameOf(7), "сестра 00007");
  assert.equal(nameOf(123456), "сестра 123456"); // 6 digits: no truncation
});

test("a persona without the anchor falls back to bare serials", () => {
  assert.deepEqual(parseNaming("no name at all"), { prefix: null, serial: null });
  assert.deepEqual(parseNaming("My name is Мисака 00007, said someone in chat"), {
    prefix: null,
    serial: null,
  });
});

test("nameOf renders actors other than the node itself with the same prefix", () => {
  const nameOf = makeNameOf(parseNaming(ANCHOR("Мисака 00007")));
  assert.equal(nameOf(12), "Мисака 00012");
  assert.equal(nameOf("7"), "Мисака 00007");
  assert.equal(nameOf("operator"), "operator");
  assert.equal(nameOf(null), "?");
});

test("nameOf without a preset passes the raw actor through", () => {
  const nameOf = makeNameOf({ prefix: null, serial: null });
  assert.equal(nameOf(12), "12");
  assert.equal(nameOf("net"), "net");
});
