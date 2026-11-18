import httpx,time,json,threading,concurrent.futures
c=httpx.Client(base_url="http://127.0.0.1:8790",timeout=120)
pcm=c.post("/speak",json={"text":"Я слышу тебя во время разговора."}).content
started=threading.Event()
def speak():
 t=time.monotonic();n=0
 with c.stream("POST","/speak/stream",json={"text":"Проверяю одновременную работу голоса и распознавания речи."}) as r:
  r.raise_for_status()
  for chunk in r.iter_bytes():
   if not n:started.set()
   n+=len(chunk)
 return {"tts_total_s":time.monotonic()-t,"bytes":n}
with concurrent.futures.ThreadPoolExecutor() as pool:
 f=pool.submit(speak)
 assert started.wait(60)
 t=time.monotonic();r=c.post("/transcribe",content=pcm);r.raise_for_status()
 print(json.dumps({"stt_wait_s":time.monotonic()-t,"transcript":r.json(),"tts":f.result()}),flush=True)
