"""
Always-on ignition scanner — runs in the cloud (GitHub Actions), checks the watchlist on the
latest CLOSED 4h candle, and sends a Telegram message when any coin fires. No API keys needed
for price data (public Binance). Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID as env/secrets.
"""
import os, time, json, datetime, ccxt, numpy as np, pandas as pd, requests
VOLX, LOOK, BASEMUL, WARMX = 3.0, 6, 0.05, 2.0
HEARTBEAT_HOUR = 8   # UTC hour for the once-a-day "all quiet" confirmation (matches the 08:10 run)
DEFAULT = ["ZEC","BONK","FET","AAVE","PENDLE","WIF","RENDER","INJ","JTO","JUP",
           "RAY","PYTH","SEI","STX","DUSK"]

def get_exchange():
    # 1) Binance PUBLIC-DATA host (data-api.binance.vision) — not geo-blocked like api.binance.com,
    #    so the cloud scanner uses the SAME feed as your TradingView (Binance). Try this first.
    try:
        ex=ccxt.binance({'enableRateLimit':True,'options':{'fetchMarkets':['spot']}})  # spot only -> no fapi/dapi 451
        ex.urls['api']['public']='https://data-api.binance.vision/api/v3'
        ex.load_markets(); print("using exchange: binance (data-api.binance.vision)"); return ex
    except Exception as e:
        print(f"binance-vision unavailable: {str(e)[:70]}")
    # 2) Fallbacks if even the vision host is blocked (data differs slightly from Binance).
    for name in ['kucoin','okx','gateio','mexc']:
        try:
            ex=getattr(ccxt,name)({'enableRateLimit':True}); ex.load_markets()
            print(f"using exchange: {name}"); return ex
        except Exception as e:
            print(f"{name} unavailable: {str(e)[:60]}")
    raise SystemExit("no exchange reachable")

BADGE={'CORE':'⭐CORE','VERIFY':'◎VERIFY','WATCH':'·watch'}
def load_watch():
    # watchlist file holds "SYM,TIER" per line (TIER optional). One file carries everything.
    if os.path.exists("ignition_watchlist.txt"):
        syms=[]; tiers={}
        for line in open("ignition_watchlist.txt"):
            line=line.strip()
            if not line: continue
            parts=[p.strip().upper() for p in line.split(',')]
            syms.append(parts[0])
            if len(parts)>1 and parts[1]: tiers[parts[0]]=parts[1]
        if syms: return syms, tiers
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

def forward_stats(ex, sym, since_iso='2024-01-01T00:00:00Z', H=126, CD=18):
    """Backtest this coin's own ignition history: what happened in the H bars (21d) after each
    past signal. Returns typical 21-day move + hit/neg rates so the alert can show expectancy."""
    out=[]; since=ex.parse8601(since_iso)
    try:
        while True:
            o=ex.fetch_ohlcv(f"{sym}/USDT",'4h',since=since,limit=1000)
            if not o: break
            out+=o; since=o[-1][0]+1
            if len(o)<1000: break
    except Exception: return None
    if len(out)<200: return None
    df=pd.DataFrame(out,columns=['ts','o','h','l','c','v']).drop_duplicates('ts')
    c,o2,v=df['c'].values,df['o'].values,df['v'].values
    sma=pd.Series(c).rolling(50).mean().values; va=pd.Series(v).shift(1).rolling(LOOK).mean().values
    fwd=[]; nxt=[]; last=-10**9
    for i in range(50,len(c)-1):
        if i-last<CD: continue
        if v[i]>=VOLX*va[i] and c[i]>o2[i] and c[i]>c[i-1] and c[i]<=sma[i]*(1+BASEMUL):
            w=c[i+1:i+1+H]
            if len(w): fwd.append(w.max()/c[i]-1); nxt.append(c[i+1]/c[i]-1); last=i
    if len(fwd)<5: return None
    f=np.array(fwd); nb=np.array(nxt)
    return dict(n=len(f),hit=round(100*np.mean(f>=0.20)),med=round(100*np.median(f)),
                neg=round(100*np.mean(f<0)),nb=round(100*nb.mean(),1),nbpos=round(100*np.mean(nb>0)))

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
    # De-dup across the :05/:15/:25 retry runs: alert at most once per 4h candle, heartbeat once/day.
    candle_id=(int(time.time())//14400)*14400          # identifies the current 4h window
    try: state=json.load(open('scan_state.json'))
    except Exception: state={'alert_id':0,'hb_date':''}
    save=lambda: json.dump(state,open('scan_state.json','w'))
    now=datetime.datetime.now(datetime.timezone.utc)

    if (fired or warm) and candle_id!=state.get('alert_id'):
        if fired:
            lines=[]
            for r in sorted(fired,key=lambda x:-x['volx']):
                s=forward_stats(ex,r['sym'])
                hist=(f"\n   ↳ next 4h bar avg {s['nb']:+.1f}% (green {s['nbpos']}%)"
                      f"\n   ↳ 21d: {s['med']:+d}% median · hit {s['hit']}% · neg {s['neg']}% (n={s['n']})") if s else ""
                lines.append(f"⚡ IGNITION — {lbl(r)} (vol {r['volx']:.1f}x, ${r['close']:.6g}){hist}")
            if warm: lines.append("warming: "+", ".join(f"{lbl(r)} ({r['volx']:.1f}x)" for r in warm))
            send("\n".join(lines)+"\n\n⭐CORE strong · ◎VERIFY check liquidity · ·watch low-conviction. "
                 "history = past 21-day move after THIS coin's signals (upside excursion). Exit discretionary. Not financial advice.")
        else:
            send("Warming (watch): "+", ".join(f"{lbl(r)} ({r['volx']:.1f}x)" for r in warm)+"\nNot financial advice.")
        state['alert_id']=candle_id; save()
    elif fired or warm:
        print("already alerted for this candle (retry run) — staying silent")
    else:
        today=now.strftime('%Y-%m-%d')
        if now.hour==HEARTBEAT_HOUR and state.get('hb_date')!=today:
            send(f"✅ Daily check — scanned {scanned} coins on {ex.id}, nothing igniting today.")
            state['hb_date']=today; save()
        else:
            print(f"quiet — scanned {scanned}, nothing igniting (silent)")
