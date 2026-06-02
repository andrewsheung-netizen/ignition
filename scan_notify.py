"""
Always-on ignition scanner — runs in the cloud (GitHub Actions), checks the watchlist on the
latest CLOSED 4h candle, and sends a Telegram message when any coin fires. No API keys needed
for price data (public Binance). Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID as env/secrets.
"""
import os, datetime, ccxt, numpy as np, pandas as pd, requests
VOLX, LOOK, BASEMUL, WARMX = 3.0, 6, 0.05, 2.0
HEARTBEAT_HOUR = 8   # UTC hour for the once-a-day "all quiet" confirmation (matches the 08:10 run)
DEFAULT = ["ZEC","BONK","FET","AAVE","PENDLE","WIF","RENDER","INJ","JTO","JUP",
           "RAY","PYTH","SEI","STX","DUSK"]

def get_exchange():
    # Binance returns HTTP 451 to US cloud IPs (GitHub runners). Try US-cloud-friendly
    # exchanges that list the same coins; use the first that loads.
    for name in ['kucoin','okx','gateio','mexc','binance']:
        try:
            ex=getattr(ccxt,name)({'enableRateLimit':True}); ex.load_markets()
            print(f"using exchange: {name}"); return ex
        except Exception as e:
            print(f"{name} unavailable: {str(e)[:70]}")
    raise SystemExit("no exchange reachable")

BADGE={'CORE':'⭐CORE','VERIFY':'◎VERIFY','WATCH':'·watch'}
def load_watch():
    # Prefer the tiered CSV (sym,tier,...); fall back to plain txt, then DEFAULT.
    if os.path.exists("ignition_watchlist.csv"):
        import csv
        syms=[]; tiers={}
        for r in csv.DictReader(open("ignition_watchlist.csv")):
            s=(r.get('sym') or '').strip().upper()
            if s: syms.append(s); tiers[s]=(r.get('tier') or '').strip().upper()
        if syms: return syms, tiers
    if os.path.exists("ignition_watchlist.txt"):
        wl=[l.strip().upper() for l in open("ignition_watchlist.txt") if l.strip()]
        if wl: return wl, {}
    return DEFAULT, {}

def check(ex, sym):
    o=ex.fetch_ohlcv(f"{sym}/USDT",'4h',limit=60)
    if len(o)<55: return None
    df=pd.DataFrame(o,columns=['ts','o','h','l','c','v'])
    i=len(df)-2                                   # last CLOSED candle (-1 is still forming)
    va=df['v'].iloc[i-LOOK:i].mean()
    volx=df['v'].iloc[i]/va if va else 0
    sma=df['c'].iloc[i-49:i+1].mean()
    green=df['c'].iloc[i]>df['o'].iloc[i] and df['c'].iloc[i]>df['c'].iloc[i-1]
    off=df['c'].iloc[i]<=sma*(1+BASEMUL)
    return dict(sym=sym,volx=volx,green=green,fired=volx>=VOLX and green and off,
                warm=volx>=WARMX and green and not(volx>=VOLX and green and off),
                close=df['c'].iloc[i])

def send(text):
    tok,chat=os.environ.get("TELEGRAM_TOKEN"),os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; message below)\n"+text); return
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  data={"chat_id":chat,"text":text},timeout=20)

if __name__=='__main__':
    ex=get_exchange()
    syms,tiers=load_watch(); fired,warm=[],[]; scanned=0
    for s in syms:
        try:
            r=check(ex,s)
            if not r: continue
            scanned+=1; r['tier']=tiers.get(s,'')
            if r['fired']: fired.append(r)
            elif r['warm']: warm.append(r)
        except Exception: pass
    lbl=lambda r:(f"{r['sym']} {BADGE.get(r['tier'],'')}").strip()
    if fired:
        lines=[f"⚡ IGNITION — {lbl(r)} (vol {r['volx']:.1f}x, ${r['close']:.6g})" for r in sorted(fired,key=lambda x:-x['volx'])]
        if warm: lines.append("warming: "+", ".join(f"{lbl(r)} ({r['volx']:.1f}x)" for r in warm))
        send("\n".join(lines)+"\n\n⭐CORE = strong · ◎VERIFY = check liquidity · ·watch = low-conviction. "
             "Exit discretionary. Not financial advice.")
    elif warm:
        send("Warming (watch): "+", ".join(f"{lbl(r)} ({r['volx']:.1f}x)" for r in warm)+"\nNot financial advice.")
    elif datetime.datetime.now(datetime.timezone.utc).hour == HEARTBEAT_HOUR:
        send(f"✅ Daily check — scanned {scanned} coins on {ex.id}, nothing igniting today.")  # once-a-day heartbeat
    else:
        print(f"quiet — scanned {scanned}, nothing igniting (silent this run)")
