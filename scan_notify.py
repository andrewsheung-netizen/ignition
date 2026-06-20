"""
Always-on ignition scanner — runs in the cloud (GitHub Actions), checks the watchlist on the
latest CLOSED 4h candle, and sends a Telegram message when any coin fires. No API keys needed
for price data (public Binance). Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID as env/secrets.
"""
import os, time, json, datetime, ccxt, numpy as np, pandas as pd, requests
try:
    import catalyst_news as cat        # auto news screen on a fire (optional; safe if missing)
except Exception as e:
    cat = None; print(f"(catalyst_news unavailable: {str(e)[:60]})")
try:
    import regime                       # BTC-trend regime flip alert (optional; safe if missing)
except Exception as e:
    regime = None; print(f"(regime unavailable: {str(e)[:60]})")
try:
    import action                       # tier+regime -> recommended play line (optional; safe if missing)
except Exception as e:
    action = None; print(f"(action unavailable: {str(e)[:60]})")
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
    fwd=[]; dd=[]; nxt=[]; last=-10**9
    for i in range(50,len(c)-1):
        if i-last<CD: continue
        if v[i]>=VOLX*va[i] and c[i]>o2[i] and c[i]>c[i-1] and c[i]<=sma[i]*(1+BASEMUL):
            w=c[i+1:i+1+H]
            if len(w):
                fwd.append(w.max()/c[i]-1)        # best level reached (upside ceiling)
                dd.append(w.min()/c[i]-1)          # worst level reached (drawdown from entry)
                nxt.append(c[i+1]/c[i]-1); last=i
    if len(fwd)<5: return None
    f=np.array(fwd); nb=np.array(nxt); d=np.array(dd)
    return dict(n=len(f),hit=round(100*np.mean(f>=0.20)),med=round(100*np.median(f)),
                dd=round(100*np.median(d)),neg=round(100*np.mean(f<0)),
                nb=round(100*nb.mean(),1),nbpos=round(100*np.mean(nb>0)))

def ethbtc_regime(ex):
    """Entry filter #4: True if ETH/BTC is BELOW its 20d MA (favourable alt-rotation tape), else False.
    Computed once per scan from daily closes. None on failure (treated as neutral downstream)."""
    try:
        b = ex.fetch_ohlcv("BTC/USDT", '1d', limit=60); e = ex.fetch_ohlcv("ETH/USDT", '1d', limit=60)
        if len(b) < 25 or len(e) < 25: return None
        bc = pd.Series([x[4] for x in b]); ec = pd.Series([x[4] for x in e])
        ratio = ec / bc; ma = ratio.rolling(20).mean()
        return bool(ratio.iloc[-1] < ma.iloc[-1])
    except Exception as ex2:
        print(f"ethbtc_regime failed: {str(ex2)[:50]}"); return None

def current_funding(sym):
    """Entry filter #1: current 8h funding for {sym}. Binance fapi is geo-blocked from US (GitHub runners)
    AND HK -> use US-reachable venues OKX then Gate. Tries bare + 1000-scaled ticker. Returns float or None."""
    s = sym.upper()
    for base in (s, "1000" + s):
        # OKX public funding-rate
        try:
            r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
                             params={"instId": f"{base}-USDT-SWAP"}, timeout=10)
            if r.status_code == 200:
                d = r.json().get("data") or []
                if d and d[0].get("fundingRate") not in (None, ""): return float(d[0]["fundingRate"])
        except Exception:
            pass
        # Gate.io futures contract (funding_rate field)
        try:
            r = requests.get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{base}_USDT", timeout=10)
            if r.status_code == 200:
                fr = r.json().get("funding_rate")
                if fr not in (None, ""): return float(fr)
        except Exception:
            pass
    return None

def send(text):
    tok,chat=os.environ.get("TELEGRAM_TOKEN"),os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; message below)\n"+text); return
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  data={"chat_id":chat,"text":text},timeout=20)

LEGEND=("\n\n⭐CORE strong · ◎VERIFY check liquidity · ·watch low-conviction. "
        "history = past 21-day move after this coin's signals (upside excursion). "
        "Exit discretionary. Not financial advice.")

def block(ex, r, kind, reg=None, ethbtc_off=None):
    s=forward_stats(ex, r['sym'])
    hist=(f"\n   ↳ next 4h bar avg {s['nb']:+.1f}% (green {s['nbpos']}%)"
          f"\n   ↳ 21d: up {s['med']:+d}% median · dip {s['dd']:+d}% typical · hit {s['hit']}% (n={s['n']})") if s else ""
    head="⚡ IGNITION" if kind=='fire' else "🟡 WARMING"
    badge=BADGE.get(r['tier'],'')
    # On a FIRE, append the recommended play WITH the validated entry filters (#1 funding, #4 ETH/BTC).
    if kind=='fire' and action:
        fv=current_funding(r['sym'])                       # current 8h funding (filter #1)
        act=f"\n   {action.action_line(r, reg, funding=fv, ethbtc_off=ethbtc_off)}"
    else:
        act=""
    return f"{head} — {r['sym']}{(' '+badge) if badge else ''} (vol {r['volx']:.1f}x, ${r['close']:.6g}){hist}{act}"

def send_catalysts(coins):
    """After an alert, send a SEPARATE catalyst-news message per coin (fired AND warming)."""
    if not cat or not coins: return
    for r in sorted(coins, key=lambda x: -x['volx']):
        try:
            msg = cat.catalyst_message(r['sym'])
            if msg: send(msg)
        except Exception as e:
            print(f"catalyst {r['sym']} failed: {str(e)[:60]}")

def regime_flip_check(ex):
    """Read BTC-trend regime; Telegram-alert ONLY when it flips state. Returns the regime dict."""
    if not regime: return None
    try:
        reg=regime.get_regime(ex)
    except Exception as e:
        print(f"regime check failed: {str(e)[:50]}"); return None
    if not reg: return None
    try: st=json.load(open('scan_state.json'))
    except Exception: st={}
    prev=st.get('regime','')
    if prev and prev!=reg['state']:
        send(regime.flip_msg(reg, prev))                    # the FLIP alert
    if st.get('regime')!=reg['state']:
        st['regime']=reg['state']; json.dump(st,open('scan_state.json','w'))
    return reg

def scan_once(force=False):
    """force=True (manual /scan): always report current status, ignore dedup.
       force=False (scheduled): alert once per 4h candle, heartbeat once/day."""
    ex=get_exchange()
    reg=regime_flip_check(ex)                                # regime flip alert (once per state change)
    ethbtc_off=ethbtc_regime(ex)                             # entry filter #4 (once per scan)
    syms,tiers=load_watch(); fired,warm=[],[]; scanned=0
    for s in syms:
        try:
            r=check(ex,s)
            if not r: continue
            scanned+=1; r['tier']=tiers.get(s,'')
            if r['fired']: fired.append(r)
            elif r['warm']: warm.append(r)
        except Exception: pass
    have=fired or warm
    def build():
        body="\n".join([block(ex,r,'fire',reg,ethbtc_off) for r in sorted(fired,key=lambda x:-x['volx'])]
                      +[block(ex,r,'warm',reg,ethbtc_off) for r in sorted(warm,key=lambda x:-x['volx'])])
        rl=regime.line(reg) if (regime and reg) else ""
        # discipline footer once per alert, only when something actually fired (rules §5/§6)
        disc=(f"\n\n{action.discipline_footer()}") if (fired and action) else ""
        return f"{body}\n\n(scanned {scanned}: {len(fired)} firing, {len(warm)} warming){rl}{disc}{LEGEND}"
    rline=regime.line(reg) if (regime and reg) else ""
    if force:
        send((build() if have else f"✅ /scan — checked {scanned} coins on {ex.id}, nothing igniting right now.") + (rline if not have else ""))
        send_catalysts(fired + warm)                       # news screen on each fired AND warming ticker
        return
    candle_id=(int(time.time())//14400)*14400          # current 4h window (dedup key)
    try: state=json.load(open('scan_state.json'))
    except Exception: state={'alert_id':0,'hb_date':''}
    save=lambda: json.dump(state,open('scan_state.json','w'))
    now=datetime.datetime.now(datetime.timezone.utc)
    if have and candle_id!=state.get('alert_id'):
        send(build()); send_catalysts(fired + warm); state['alert_id']=candle_id; save()
    elif have:
        print("already alerted this candle (retry run) — staying silent")
    else:
        today=now.strftime('%Y-%m-%d')
        if now.hour==HEARTBEAT_HOUR and state.get('hb_date')!=today:
            send(f"✅ Daily check — scanned {scanned} coins on {ex.id}, nothing igniting today." + rline)
            state['hb_date']=today; save()
        else:
            print(f"quiet — scanned {scanned}, nothing igniting (silent)")

if __name__=='__main__':
    import sys
    scan_once(force=('force' in sys.argv or '--scan' in sys.argv))   # `python scan_notify.py force` = manual send
