import asyncio,json,time
from pathlib import Path
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
PHRASES = ['Привет, я проверяю голос.', 'Я готова начать строительство.', 'Нужно собрать дерево и камень.', 'Покажи ближайшую берёзу.', 'Верстак уже готов.', 'Я нашла подходящее место для дома.', 'Сначала построим фундамент.', 'Мне нужны ещё четыре доски.', 'Печь работает, скоро будет стекло.', 'Проверяю инвентарь перед дорогой.', 'Остановись и подожди меня.', 'Расскажи, что ты сейчас видишь.', 'Материалов пока недостаточно.', 'Я отправила сестре новую схему.', 'Проверим стены и крышу.', 'Сегодня хорошая погода.', 'Я слышу тебя во время разговора.', 'Дом почти готов, осталось окно.', 'Сохраняю результаты проверки.', 'Спасибо, проверка завершена.']
async def main():
 async with Client(streamable_http_client("http://127.0.0.1:8700/mcp")) as c:
  for text in PHRASES:
   t=time.time()
   await asyncio.wait_for(c.call_tool("say",{"text":text}),30)
   end=time.monotonic()+120
   while time.monotonic()<end:
    await asyncio.sleep(.2)
    rows=[json.loads(x) for x in Path("/mind-run/log.jsonl").read_text().splitlines() if x.startswith("{")]
    found=[r for r in rows if r.get("kind")=="voice" and r.get("text")==text and r.get("ts",0)>=t]
    if found:
     print(json.dumps(found[-1],ensure_ascii=False),flush=True);break
   else:print(json.dumps({"text":text,"error":"120s timeout"}),flush=True)
asyncio.run(main())
