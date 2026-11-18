import asyncio,json,time
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
async def main():
 async with Client(streamable_http_client("http://127.0.0.1:8700/mcp")) as c:
  async def call(n,a):
   r=await asyncio.wait_for(c.call_tool(n,a),35)
   return json.loads(r.content[0].text)
  print(json.dumps({"say":await call("say",{"text":"Проверяю потоковый голос на единственном активном слоте."})}),flush=True)
  await asyncio.sleep(20)
  before=await call("observe",{})
  held=sum(i["count"] for i in before["inventory"] if i["name"].endswith(" dirt"))
  print(json.dumps({"pickup_request":await call("act",{"intent":"get","args":{"item":"dirt","count":held+1}}),"before":before}),flush=True)
  await asyncio.sleep(12)
  print(json.dumps({"pickup_after":await call("observe",{}),"stop":await call("stop",{})}),flush=True)
asyncio.run(main())
