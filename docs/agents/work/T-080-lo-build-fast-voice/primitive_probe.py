import asyncio,json,time,os
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from py4j.java_gateway import JavaGateway,GatewayParameters
from runtime.game.py4j_body import bridge_addresses
host,port,*_=bridge_addresses()
g=JavaGateway(gateway_parameters=GatewayParameters(address=host,port=port,auto_convert=True))
mc=g.entry_point
names=sorted(set(str(m.getName()) for m in mc.getClass().getMethods()))
print(json.dumps({"loaded_methods":names}),flush=True)
for radius in [4,5,50]:
 blocks=[dict(b) for b in mc.getBlocksAround(radius)]
 print(json.dumps({"radius":radius,"count":len(blocks),"sample":blocks[:4]}),flush=True)
g.close()
async def main():
 async with Client(streamable_http_client("http://127.0.0.1:8700/mcp")) as c:
  async def call(n,a):
   r=await asyncio.wait_for(c.call_tool(n,a),30)
   return json.loads(r.content[0].text)
  before=await call("observe",{})
  print(json.dumps({"observe":before}),flush=True)
  pos=before["self"]["pos"]
  async def probe(intent,args):
   t=time.monotonic()
   try:
    req=await call("act",{"intent":intent,"args":args})
    await asyncio.sleep(6)
    after=await call("observe",{})
    stop=await call("stop",{})
    print(json.dumps({"probe":intent,"args":args,"request":req,"before":before,"after":after,"stop":stop,"seconds":time.monotonic()-t}),flush=True)
   except Exception as e:print(json.dumps({"probe":intent,"error":str(e)}),flush=True)
  await probe("goto",{"x":int(pos[0])+2,"y":int(pos[1]),"z":int(pos[2])})
  before=await call("observe",{})
  dirt=next((b for b in before["blocks"] if b["name"].endswith(" dirt")),None)
  if dirt:await probe("mine",{"pos":dirt["pos"][0]})
asyncio.run(main())
