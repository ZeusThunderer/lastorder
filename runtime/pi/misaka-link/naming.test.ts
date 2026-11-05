import test from "node:test";
import assert from "node:assert/strict";
import { makeNameOf, parseNaming } from "./naming.ts";

test("both presets are read out of the workplace's own PERSONA.md", () => {
  assert.equal(parseNaming("Имя: Мисака 00007\nРечь: …").prefix, "Мисака");
  assert.equal(parseNaming("Имя: узел 00003").prefix, "узел");
  assert.equal(parseNaming("Имя: Мисака 00007").prefix, "Мисака");
});

test("a persona without a preset name falls back to bare serials", () => {
  assert.equal(parseNaming("no name at all").prefix, null);
});

test("nameOf formats any serial in the preset's format", () => {
  const nameOf = makeNameOf({ prefix: "Мисака" });
  assert.equal(nameOf(12), "Мисака 00012");
  assert.equal(nameOf("7"), "Мисака 00007");
  assert.equal(nameOf("123456"), "Мисака 123456"); // 6 digits: no truncation
  assert.equal(nameOf("operator"), "operator");
  assert.equal(nameOf(null), "?");
  assert.equal(nameOf(""), "?");
});

test("nameOf without a preset passes the raw actor through", () => {
  const nameOf = makeNameOf({ prefix: null });
  assert.equal(nameOf(12), "12");
  assert.equal(nameOf("net"), "net");
});
