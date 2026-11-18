import httpx,time,json,statistics,concurrent.futures
phrases=["Привет, я проверяю голос.","Я готова начать строительство.","Нужно собрать дерево и камень.","Покажи ближайшую берёзу.","Верстак уже готов.","Я нашла подходящее место для дома.","Сначала построим фундамент.","Мне нужны ещё четыре доски.","Печь работает, скоро будет стекло.","Проверяю инвентарь перед дорогой.","Остановись и подожди меня.","Расскажи, что ты сейчас видишь.","Материалов пока недостаточно.","Я отправила сестре новую схему.","Проверим стены и крышу.","Сегодня хорошая погода.","Я слышу тебя во время разговора.","Дом почти готов, осталось окно.","Сохраняю результаты проверки.","Спасибо, проверка завершена."]
c=httpx.Client(base_url="http://127.0.0.1:8790",timeout=180)
print(json.dumps({"health":c.get("/health").json()}),flush=True)
for i,text in enumerate(phrases):
 t=time.monotonic();first=None;pcm=bytearray();gaps=[];prev=None
 try:
  with c.stream("POST","/speak/stream",json={"text":text}) as r:
   r.raise_for_status()
   for chunk in r.iter_bytes():
    now=time.monotonic()
    if first is None:first=now-t
    if prev is not None:gaps.append(now-prev)
    prev=now;pcm.extend(chunk)
  row={"i":i,"text":text,"first_s":first,"total_s":time.monotonic()-t,"audio_s":len(pcm)/96000,"even_pcm":len(pcm)%2==0,"max_gap_s":max(gaps,default=0)}
  if i<10:
   st=time.monotonic();row["transcript"]=c.post("/transcribe",content=bytes(pcm)).json();row["stt_s"]=time.monotonic()-st
  print(json.dumps(row,ensure_ascii=False),flush=True)
 except Exception as e: print(json.dumps({"i":i,"error":str(e)}),flush=True)
