"""
DUSK paper trader — forward paper-trades the ignition strategy on REAL Binance data (no real orders).
Runs every ~30 min. Sends a LIVE Telegram message on each ENTRY and each EXIT (no 4h batching), plus a
once-daily equity heartbeat. Two bots share entries/filters, differ on EXITS:
    Bot A "mech"  : take-profit +20% / stop -15% / 21-day time-exit   (matches the backtest)
    Bot B "scale" : stop -15% · bank HALF at +18% · trail the rest (20% off the peak) toward +27%
Entry = ignition signal (on a closed 4h candle) + filter #1 (skip funding >= +0.0001/8h) + filter #4
(trade only when ETH/BTC < 20d MA). 1% risk/trade, max 5 concurrent per bot.

Exits are checked on 15-MINUTE candles, processing EVERY closed candle since the last run (so a flaky
schedule never skips an exit), using each candle's HIGH/LOW (intra-candle TP/stop touches are caught even
if the candle closes elsewhere). State persists in paper_state.json (committed back by CI). Dedicated bot:
PAPER_BOT_TOKEN / PAPER_CHAT_ID. NOT real trading, NOT financial advice.    Run:  python3 paper_trader.py
"""
import os, json, time, datetime, ccxt, numpy as np, pandas as pd, requests

START_BAL = 25000.0
RISK, STOP, MAXPOS = 0.01, 0.15, 5
TIME_CAP_MS = 21*24*3600*1000               # 21-day time-exit
FINE_TF, FINE_LIMIT = '15m', 200            # exit-check timeframe
VOLX, LOOK, BASEMUL, COOLDOWN = 3.0, 6, 0.05, 18
FUNDING_POS, ETHBTC_MA = 0.0001, 20
FEE, SLIP = 0.0006, 0.0010; COST = 1 - FEE - SLIP
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_state.json')
WATCHFILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ignition_watchlist.txt')

def get_exchange():
    try:
        ex = ccxt.binance({'enableRateLimit': True, 'options': {'fetchMarkets': ['spot']}})
        ex.urls['api']['public'] = 'https://data-api.binance.vision/api/v3'
        ex.load_markets(); return ex
    except Exception:
        ex = ccxt.binance({'enableRateLimit': True}); ex.load_markets(); return ex

def load_watch():
    out = []
    for l in open(WATCHFILE):
        if not l.strip(): continue
        p = [x.strip().upper() for x in l.split(',')]
        out.append((p[0], p[1] if len(p) > 1 else 'NA'))
    if not any(s == 'DUSK' for s, _ in out): out.append(('DUSK', 'CORE'))
    return out

def fetch(ex, sym, tf='4h', limit=60):
    try:
        o = ex.fetch_ohlcv(f"{sym}/USDT", tf, limit=limit)
        if len(o) < 3: return None
        return pd.DataFrame(o, columns=['ts','o','h','l','c','v'])
    except Exception:
        return None

def signal_on_closed(df):
    """Ignition on the last CLOSED 4h candle (index -2). Returns (fired, close, ts, volx)."""
    i = len(df) - 2
    va = df['v'].iloc[i-LOOK:i].mean(); volx = df['v'].iloc[i]/va if va else 0
    sma = df['c'].iloc[i-49:i+1].mean()
    green = df['c'].iloc[i] > df['o'].iloc[i] and df['c'].iloc[i] > df['c'].iloc[i-1]
    off = df['c'].iloc[i] <= sma*(1+BASEMUL)
    return (volx >= VOLX and green and off), float(df['c'].iloc[i]), int(df['ts'].iloc[i]), float(volx)

def ethbtc_regime(ex):
    try:
        b = ex.fetch_ohlcv("BTC/USDT", '1d', limit=60); e = ex.fetch_ohlcv("ETH/USDT", '1d', limit=60)
        if len(b) < 25 or len(e) < 25: return None
        bc = pd.Series([x[4] for x in b]); ec = pd.Series([x[4] for x in e]); ratio = ec/bc
        return bool(ratio.iloc[-1] < ratio.rolling(ETHBTC_MA).mean().iloc[-1])
    except Exception: return None

def current_funding(sym):
    s = sym.upper()
    for base in (s, "1000"+s):
        try:
            r = requests.get("https://www.okx.com/api/v5/public/funding-rate",
                             params={"instId": f"{base}-USDT-SWAP"}, timeout=10)
            if r.status_code == 200:
                d = r.json().get("data") or []
                if d and d[0].get("fundingRate") not in (None, ""): return float(d[0]["fundingRate"])
        except Exception: pass
        try:
            r = requests.get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{base}_USDT", timeout=10)
            if r.status_code == 200:
                fr = r.json().get("funding_rate")
                if fr not in (None, ""): return float(fr)
        except Exception: pass
    return None

def send(text):
    tok = os.environ.get("PAPER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("PAPER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; message below)\n" + text + "\n"); return
    try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e: print(f"telegram failed: {e}\n{text}")

def blank_bot(): return {"bal": START_BAL, "pos": {}, "closed": [], "last_entry": {}}
def load_state():
    try: return json.load(open(STATE_FILE))
    except Exception: return {"A": blank_bot(), "B": blank_bot(), "last_4h": 0, "hb_date": ""}
def save_state(s): json.dump(s, open(STATE_FILE, 'w'), indent=1)

def _net(g): return (1 + g) * COST - 1

def open_pos(bot, sym, price, ts, tier):
    bot["pos"][sym] = {"entry": price, "notional": (RISK*bot["bal"])/STOP, "ts": ts,
                       "last_check": ts, "peak": price, "half": False, "tier": tier}
    bot["last_entry"][sym] = ts

def close_pos(bot, sym, gross, why, xts, frac=1.0):
    p = bot["pos"][sym]; pnl = p["notional"] * frac * _net(gross); bot["bal"] += pnl
    bot["closed"].append({"sym": sym, "ret": round(100*_net(gross), 2), "pnl": round(pnl, 2),
                          "why": why, "xts": int(xts), "entry_ts": p["ts"]})
    if frac >= 1.0: del bot["pos"][sym]; return pnl, True
    p["notional"] *= (1 - frac); p["half"] = True; return pnl, False

def manage_exits(bot, which, sym, hi, lo, cl, xts):
    """One 15m candle -> apply exit rules, return detailed event strings (caller sends them live)."""
    p = bot["pos"][sym]; e = p["entry"]; tier = p.get("tier", ""); p["peak"] = max(p["peak"], hi); ev = []
    def do(gross, why, frac=1.0, tag='🔴', extra=''):
        pnl, _ = close_pos(bot, sym, gross, why, xts, frac=frac); xpx = e*(1+gross)
        basis = 'on ½' if frac < 1 else 'net'
        ev.append(f"{tag} EXIT {sym} {tier} — Bot {which} {why}  ${e:.6g}→${xpx:.6g}  "
                  f"({100*_net(gross):+.1f}% {basis}, ${pnl:+,.0f}){extra}")
    timecap = (xts - p["ts"]) >= TIME_CAP_MS
    if which == 'A':
        if   lo <= e*(1-STOP): do(-STOP, "stop −15%")
        elif hi >= e*1.20:     do(0.20, "TP +20%", tag='🟢')
        elif timecap:          do(cl/e-1, "21d time-exit", tag='⏳')
    else:
        if not p["half"]:
            if   lo <= e*(1-STOP): do(-STOP, "stop −15%")
            elif hi >= e*1.18:     do(0.18, "booked ½ @ +18%", frac=0.5, tag='🟡', extra=' · riding rest → +27%')
            elif timecap:          do(cl/e-1, "21d time-exit", tag='⏳')
        else:
            if   hi >= e*1.27:         do(0.27, "runner +27%", tag='🟢')
            elif cl <= p["peak"]*0.80: do(cl/e-1, "trail stop", tag='🔴')
            elif lo <= e*(1-STOP):     do(-STOP, "stop −15%")
            elif timecap:              do(cl/e-1, "21d time-exit", tag='⏳')
    return ev


def exit_pass(ex, s):
    """Every run: process every CLOSED 15m candle since each position's last check; send exits live."""
    syms = sorted({sym for bk in ('A', 'B') for sym in s[bk]['pos']})
    fine = {}
    for sym in syms:
        df = fetch(ex, sym, FINE_TF, FINE_LIMIT)
        if df is not None and len(df) > 2: fine[sym] = df.iloc[:-1]   # drop the still-forming candle
    for bk in ('A', 'B'):
        for sym in list(s[bk]['pos']):
            df = fine.get(sym)
            if df is None: continue
            p0 = s[bk]['pos'][sym]; lastc = p0.get('last_check', p0.get('ts', 0)); closed = False
            for _, row in df.iterrows():
                cts = int(row['ts'])
                if cts <= lastc: continue
                evs = manage_exits(s[bk], bk, sym, float(row['h']), float(row['l']), float(row['c']), cts)
                lastc = cts
                for e in evs: send(e)
                if sym not in s[bk]['pos']: closed = True; break
            if not closed and sym in s[bk]['pos']: s[bk]['pos'][sym]['last_check'] = lastc

def entry_pass(ex, s, watch):
    """Only when a new 4h candle has closed: scan for ignitions + filters, open, send entries live."""
    now_s = int(time.time()); cur4h = (now_s // 14400) * 14400
    if cur4h <= s.get('last_4h', 0): return
    ethbtc_off = ethbtc_regime(ex)
    for sym, tier in watch:
        df = fetch(ex, sym, '4h', 60)
        if df is None: continue
        fired, close, cts, volx = signal_on_closed(df)
        if not fired: continue
        le = max(s['A']['last_entry'].get(sym, 0), s['B']['last_entry'].get(sym, 0))
        if le and (cts - le) < COOLDOWN*4*3600*1000: continue
        fv = current_funding(sym)
        fund_ok = (fv is None) or (fv < FUNDING_POS); reg_ok = (ethbtc_off is None) or ethbtc_off
        if not (fund_ok and reg_ok): continue
        opened = [bk for bk in ('A', 'B') if len(s[bk]['pos']) < MAXPOS]
        for bk in opened: open_pos(s[bk], sym, close, cts, tier)
        if opened:
            stop_px = close*(1-STOP); fstr = 'n/a' if fv is None else f"{fv*100:+.3f}%/8h"
            size_str = " · ".join(f"{bk} ${s[bk]['pos'][sym]['notional']:,.0f}" for bk in opened)
            send(f"🟢 ENTRY {sym} {tier} @ ${close:.6g}  (vol {volx:.1f}x · funding {fstr})\n"
                 f"     position ~{RISK/STOP*100:.1f}% of capital  ·  stop −15% = ${stop_px:.6g}\n"
                 f"     Bot A: take-profit +20% = ${close*1.20:.6g}\n"
                 f"     Bot B: ½ at +18% = ${close*1.18:.6g}, trail rest → +27% = ${close*1.27:.6g}\n"
                 f"     size: {size_str}")
    s['last_4h'] = cur4h

def heartbeat(ex, s):
    """Once a day: equity (realized + open mark-to-market) for both bots."""
    today = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    if s.get('hb_date') == today: return
    px = {}
    for sym in {sym for bk in ('A', 'B') for sym in s[bk]['pos']}:
        df = fetch(ex, sym, FINE_TF, 3)
        if df is not None and len(df): px[sym] = float(df.iloc[-1]['c'])
    def line(bk, label):
        b = s[bk]; unreal = sum(p["notional"]*_net(px[sym]/p["entry"]-1) for sym, p in b["pos"].items() if sym in px)
        eq = b["bal"] + unreal
        return (f"{label}: equity ${eq:,.0f} ({100*(eq/START_BAL-1):+.1f}%) = realized ${b['bal']:,.0f} + "
                f"open ${unreal:+,.0f} · {len(b['pos'])} open · {len(b['closed'])} closed")
    reg = ethbtc_regime(ex); rs = "ETH/BTC<MA ✓" if reg else "ETH/BTC>MA ✗" if reg is False else "n/a"
    send("📝 DUSK PAPER — daily heartbeat (" + today + ", " + rs + ")\n"
         + line('A', "Bot A mech  +20/−15") + "\n" + line('B', "Bot B scale ½@18/trail")
         + "\n(paper — virtual money, real prices. Not financial advice.)")
    s['hb_date'] = today

def _migrate(s):
    """Backfill fields on positions created by older versions (e.g. missing 'last_check')."""
    for bk in ('A', 'B'):
        for p in s.get(bk, {}).get('pos', {}).values():
            p.setdefault('ts', 0); p.setdefault('entry', p.get('entry', 0.0))
            p.setdefault('last_check', p.get('ts', 0)); p.setdefault('peak', p.get('entry', 0.0))
            p.setdefault('half', False); p.setdefault('tier', '')
    s.setdefault('last_4h', 0); s.setdefault('hb_date', "")
    return s

def main():
    ex = get_exchange(); s = _migrate(load_state()); watch = load_watch()
    exit_pass(ex, s)          # live exits (every run, 15m, catch-up)
    entry_pass(ex, s, watch)  # live entries (only on a new closed 4h candle)
    heartbeat(ex, s)          # once-daily equity
    save_state(s)

if __name__ == '__main__':
    main()
