"""Bounded resource probe, run inside mind container without a sister."""
import asyncio,json,time
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

def decode(r):
    text=r.content[0].text if r.content else "{}"
    try:return json.loads(text)
    except ValueError:return {"text":text}

async def main():
    async with Client(streamable_http_client("http://127.0.0.1:8700/mcp")) as c:
        async def call(name,args):
            return decode(await c.call_tool(name,args))
        initial=await call("observe",{})
        print(json.dumps({"initial":initial},ensure_ascii=False),flush=True)
        if not initial.get("self",{}).get("in_game"):
            print("BLOCKED: body not in game",flush=True);return
        for item in ["oak log","oak planks","cobblestone","crafting table","wooden pickaxe","glass","definitely nonexistent item"]:
            before=await call("observe",{})
            held=sum(i["count"] for i in before.get("inventory",[]) if i["name"].split(" ",1)[-1]==item)
            response=await call("act",{"intent":"get","args":{"item":item,"count":held+1}})
            deadline=time.monotonic()+(300 if item=="glass" else 120)
            events=[]
            while time.monotonic()<deadline and "error" not in response:
                e=await call("wait_events",{"timeout_s":5});events.append(e)
                if e.get("intent_id") == response.get("intent_id") and e.get("type") in ("intent.done", "intent.failed"):break
            await call("stop",{})
            after=await call("observe",{})
            print(json.dumps({"item":item,"request":response,"before":before.get("inventory"),"after":after.get("inventory"),"events":events},ensure_ascii=False),flush=True)

asyncio.run(main())
