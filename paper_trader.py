"""
DUSK paper trader — forward paper-trades the ignition strategy on REAL Binance data (no real orders).
Runs every 4h (same cadence as the scanner). Keeps a virtual balance and TWO bots that share the same
entries/filters but differ on EXITS, so you can compare them live:
    Bot A "mech"  : take-profit +20% / stop -15% / 21-day time-cap   (matches the backtest)
    Bot B "scale" : stop -15% · bank HALF at +18% · trail the rest (20% off the peak) toward +27%
Entry = ignition signal + filter #1 (skip funding >= +0.0001/8h) + filter #4 (trade only when ETH/BTC
< 20d MA). 1% risk per trade, max 5 concurrent per bot. State persists in paper_state.json; a summary
is sent to Telegram each run (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID). NOT real trading, NOT financial advice.

Run locally:  python3 paper_trader.py        (one 4h step)
"""
import os, json, datetime, ccxt, numpy as np, pandas as pd, requests

START_BAL = 25000.0
RISK, STOP, MAXPOS, H = 0.01, 0.15, 5, 126
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

def fetch(ex, sym, limit=60):
    try:
        o = ex.fetch_ohlcv(f"{sym}/USDT", '4h', limit=limit)
        if len(o) < 55: return None
        return pd.DataFrame(o, columns=['ts','o','h','l','c','v'])
    except Exception:
        return None

def signal_on_closed(df):
    """Ignition check on the last CLOSED 4h candle (index -2). Returns (fired, close, ts, volx)."""
    i = len(df) - 2
    va = df['v'].iloc[i-LOOK:i].mean()
    volx = df['v'].iloc[i] / va if va else 0
    sma = df['c'].iloc[i-49:i+1].mean()
    green = df['c'].iloc[i] > df['o'].iloc[i] and df['c'].iloc[i] > df['c'].iloc[i-1]
    off = df['c'].iloc[i] <= sma * (1 + BASEMUL)
    fired = volx >= VOLX and green and off
    return fired, float(df['c'].iloc[i]), int(df['ts'].iloc[i]), float(volx)

def ethbtc_regime(ex):
    try:
        b = ex.fetch_ohlcv("BTC/USDT", '1d', limit=60); e = ex.fetch_ohlcv("ETH/USDT", '1d', limit=60)
        if len(b) < 25 or len(e) < 25: return None
        bc = pd.Series([x[4] for x in b]); ec = pd.Series([x[4] for x in e]); ratio = ec/bc
        return bool(ratio.iloc[-1] < ratio.rolling(ETHBTC_MA).mean().iloc[-1])
    except Exception: return None

def current_funding(sym):
    for cand in (f"{sym}USDT", f"1000{sym}USDT"):
        try:
            r = requests.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": cand}, timeout=10)
            if r.status_code != 200: continue
            fr = r.json().get("lastFundingRate")
            if fr is not None: return float(fr)
        except Exception: continue
    return None

def send(text):
    # dedicated PAPER bot (keeps paper noise off the live alert bot); falls back to the scanner bot if unset
    tok = os.environ.get("PAPER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("PAPER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; message below)\n" + text); return
    try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e: print(f"telegram failed: {e}\n{text}")

def blank_bot(): return {"bal": START_BAL, "pos": {}, "closed": [], "last_entry": {}}
def load_state():
    try: return json.load(open(STATE_FILE))
    except Exception: return {"A": blank_bot(), "B": blank_bot(), "candle": 0}
def save_state(s): json.dump(s, open(STATE_FILE, 'w'), indent=1)

def _net(g): return (1 + g) * COST - 1            # round-trip fee/slip on a gross fractional move

def open_pos(bot, sym, price, ts, tier):
    notional = (RISK * bot["bal"]) / STOP          # 1% risk at a 15% stop
    bot["pos"][sym] = {"entry": price, "notional": notional, "ts": ts, "age": 0,
                       "peak": price, "half": False, "tier": tier}
    bot["last_entry"][sym] = ts

def close_pos(bot, sym, gross, why, xts, frac=1.0):
    """Realize `frac` of the position at gross return `gross`. Returns (pnl, closed_fully)."""
    p = bot["pos"][sym]; pnl = p["notional"] * frac * _net(gross)
    bot["bal"] += pnl
    bot["closed"].append({"sym": sym, "ret": round(100*_net(gross), 2), "pnl": round(pnl, 2),
                          "why": why, "xts": int(xts), "entry_ts": p["ts"]})
    if frac >= 1.0:
        del bot["pos"][sym]; return pnl, True
    p["notional"] *= (1 - frac); p["half"] = True; return pnl, False

def manage_exits(bot, which, sym, hi, lo, cl, xts):
    """Apply the bot's exit rules to one position given the latest closed candle. Returns detailed event strs:
    🔴/🟢/🟡/⏳ SYM TIER — Bot X reason  $entry→$exit  (ret% , $pnl)."""
    p = bot["pos"][sym]; e = p["entry"]; tier = p.get("tier", "")
    p["age"] += 1; p["peak"] = max(p["peak"], hi); ev = []
    def do(gross, why, frac=1.0, tag='🔴', extra=''):
        pnl, _ = close_pos(bot, sym, gross, why, xts, frac=frac)
        xpx = e * (1 + gross); basis = 'on ½' if frac < 1 else 'net'
        ev.append(f"{tag} {sym} {tier} — Bot {which} {why}  ${e:.6g}→${xpx:.6g}  "
                  f"({100*_net(gross):+.1f}% {basis}, ${pnl:+,.0f}){extra}")
    if which == 'A':      # mechanical +20% / -15% / 21d
        if   lo <= e*(1-STOP): do(-STOP, "stop −15%")
        elif hi >= e*1.20:     do(0.20, "TP +20%", tag='🟢')
        elif p["age"] >= H:    do(cl/e-1, "21d time-exit", tag='⏳')
    else:                 # scale + trail
        if not p["half"]:
            if   lo <= e*(1-STOP): do(-STOP, "stop −15%")
            elif hi >= e*1.18:     do(0.18, "booked ½ @ +18%", frac=0.5, tag='🟡', extra=' · riding rest → +27%')
            elif p["age"] >= H:    do(cl/e-1, "21d time-exit", tag='⏳')
        else:             # remaining half: target +27% / 20% trail / catastrophe / time
            if   hi >= e*1.27:         do(0.27, "runner +27%", tag='🟢')
            elif cl <= p["peak"]*0.80: do(cl/e-1, "trail stop", tag='🔴')
            elif lo <= e*(1-STOP):     do(-STOP, "stop −15%")
            elif p["age"] >= H:        do(cl/e-1, "21d time-exit", tag='⏳')
    return ev

def main():
    ex = get_exchange(); s = load_state(); watch = load_watch()
    ethbtc_off = ethbtc_regime(ex)
    dfcache = {}
    def getdf(sym):
        if sym not in dfcache: dfcache[sym] = fetch(ex, sym)
        return dfcache[sym]

    exits = {"A": [], "B": []}
    # 1) manage open positions against the latest CLOSED candle
    for bk in ("A", "B"):
        for sym in list(s[bk]["pos"]):
            df = getdf(sym)
            if df is None: continue
            last = df.iloc[-2]
            exits[bk] += manage_exits(s[bk], bk, sym, float(last['h']), float(last['l']), float(last['c']), int(last['ts']))

    # 2) entries — same signal + filters for both bots
    entries, skips = [], []
    for sym, tier in watch:
        df = getdf(sym)
        if df is None: continue
        fired, close, ts, volx = signal_on_closed(df)
        if not fired: continue
        # cooldown vs either bot's last entry for this coin (3 days)
        le = max(s["A"]["last_entry"].get(sym, 0), s["B"]["last_entry"].get(sym, 0))
        if le and (ts - le) < COOLDOWN * 4*3600*1000: continue
        fv = current_funding(sym)
        fund_ok = (fv is None) or (fv < FUNDING_POS)
        reg_ok = (ethbtc_off is None) or ethbtc_off
        if not (fund_ok and reg_ok):
            why = []
            if not fund_ok: why.append(f"funding {fv*100:+.3f}%")
            if not reg_ok:  why.append("ETH/BTC>MA")
            skips.append(f"{sym} ({', '.join(why)})"); continue
        opened = []
        for bk in ("A", "B"):
            if len(s[bk]["pos"]) < MAXPOS: open_pos(s[bk], sym, close, ts, tier); opened.append(bk)
        if opened:
            stop_px = close * (1 - STOP)
            size_str = " · ".join(f"{bk} ${s[bk]['pos'][sym]['notional']:,.0f}" for bk in opened)
            fstr = 'n/a' if fv is None else f"{fv*100:+.3f}%/8h"
            entries.append(
                f"🟢 {sym} {tier} @ ${close:.6g}  (vol {volx:.1f}x · funding {fstr})\n"
                f"     position ~{RISK/STOP*100:.1f}% of capital  ·  stop −15% = ${stop_px:.6g}\n"
                f"     Bot A: take-profit +20% = ${close*1.20:.6g}\n"
                f"     Bot B: ½ at +18% = ${close*1.18:.6g}, trail rest → +27% = ${close*1.27:.6g}\n"
                f"     size: {size_str}")

    s["candle"] = (int(datetime.datetime.now(datetime.timezone.utc).timestamp())//14400)*14400
    save_state(s)

    # 3) report — equity = realized balance + mark-to-market of open positions (latest close)
    def equity(bk):
        b = s[bk]; unreal = 0.0
        for sym, p in b["pos"].items():
            df = getdf(sym)
            if df is None: continue
            cl = float(df.iloc[-2]['c'])
            unreal += p["notional"] * _net(cl/p["entry"] - 1)
        return b["bal"] + unreal, unreal
    def line(bk, label):
        b = s[bk]; eq, unreal = equity(bk); ret = 100*(eq/START_BAL - 1)
        return (f"{label}: equity ${eq:,.0f} ({ret:+.1f}%) = realized ${b['bal']:,.0f} + open ${unreal:+,.0f}"
                f" · {len(b['pos'])} open · {len(b['closed'])} closed")
    reg = "ETH/BTC<MA ✓ (trade)" if ethbtc_off else "ETH/BTC>MA ✗ (skip)" if ethbtc_off is False else "ETH/BTC n/a"
    msg = ["📝 DUSK PAPER (4h step) — " + reg,
           ("📈 NEW TRADES:\n" + "\n".join(entries)) if entries else "📈 NEW TRADES: none",
           "⛔ SKIP (filter): " + (", ".join(skips) if skips else "none"),
           ("🔻 EXITS:\n" + "\n".join(exits["A"] + exits["B"])) if (exits["A"] or exits["B"]) else "🔻 EXITS: none",
           line("A", "Bot A mech  +20/−15"),
           line("B", "Bot B scale ½@18/trail"),
           "(paper — virtual money, real prices. Not financial advice.)"]
    send("\n".join(msg))

if __name__ == '__main__':
    main()
