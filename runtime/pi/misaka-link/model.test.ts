import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import misakaLink from "./misaka-link.ts";

test("a resumed session selects the operator-rendered gateway model", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pi-model-"));
  const old = process.env.PI_CODING_AGENT_DIR;
  process.env.PI_CODING_AGENT_DIR = dir;
  try {
    fs.writeFileSync(path.join(dir, "settings.json"), JSON.stringify({defaultProvider:"zai", defaultModel:"z-ai/glm-5.3-flash"}));
    const handlers = new Map<string, Function>();
    const chosen = {id:"z-ai/glm-5.3-flash"};
    let selected: unknown;
    misakaLink({on:(event:string, handler:Function) => handlers.set(event,handler), setModel:async(model:unknown) => {selected=model;return true;}});
    await handlers.get("session_start")!({}, {cwd:dir,modelRegistry:{find:(provider:string,id:string)=> {
      assert.equal(provider,"zai"); assert.equal(id,chosen.id); return chosen;
    }}});
    assert.equal(selected,chosen);
    selected = undefined;
    await handlers.get("session_start")!({}, {cwd:dir, model:{provider:"opencode-go-gateway", id:"opencode-go/kimi-k3"}, modelRegistry:{find:()=>{throw new Error("must preserve selected Go model");}}});
    assert.equal(selected,undefined);
  } finally {
    if (old === undefined) delete process.env.PI_CODING_AGENT_DIR; else process.env.PI_CODING_AGENT_DIR=old;
    fs.unlinkSync(path.join(dir,"settings.json"));
    fs.rmdirSync(dir);
  }
});
