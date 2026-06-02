"""
Telegram command listener — lets you message the bot "/scan" to run the scanner on demand.
Polls getUpdates, and if YOU (your chat id) sent /scan since last check, runs scan_once(force=True).
Tracks the Telegram update offset in command_state.json so old commands aren't re-run.
Scheduled (e.g. every 10 min) by command-listener.yml, so latency is up to the poll interval.
"""
import os, json, requests, scan_notify
TOKEN=os.environ["TELEGRAM_TOKEN"]; CHAT=str(os.environ["TELEGRAM_CHAT_ID"])
ST="command_state.json"
try: st=json.load(open(ST))
except Exception: st={"offset":0}

r=requests.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates",
               params={"offset":st["offset"],"timeout":0},timeout=30).json()
run=False
for u in r.get("result",[]):
    st["offset"]=u["update_id"]+1
    m=u.get("message") or {}
    txt=(m.get("text") or "").strip().lower()
    frm=str((m.get("chat") or {}).get("id"))
    if frm!=CHAT: continue                       # ignore anyone who isn't you
    if txt in ("/scan","scan","/run","run"): run=True
json.dump(st,open(ST,"w"))

if run:
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  data={"chat_id":CHAT,"text":"🔄 Running scan on demand…"},timeout=20)
    scan_notify.scan_once(force=True)
else:
    print("no /scan command this poll")
