"""
Regime detector for the ignition strategy — ALT-MARKET TREND (equal-weight CORE alt index).

Validated (DUSK_regime_validate_v2.py): gating ignitions on "the alt complex is trending up" — the
equal-weight CORE alt index's 50-day MA SLOPING UP — beat every other filter and was the only one
positive in all three time folds INCLUDING the recent one (ON geo +2.12 vs OFF +0.64; recent-third
+4.12 vs ungated -1.69). It's a momentum gate on the alt market: RISK-ON when alts trend up, RISK-OFF
when they roll over. Damage-reducer, not a guarantee; whipsaws are possible at turns.

Used by scan_notify.py to alert ONLY when the regime flips. No keys needed. Not financial advice.
"""
import pandas as pd

SMA_D = 50
SLOPE_N = 20      # the 50d MA is "rising" if it's higher than SLOPE_N days ago

def _core_syms():
    syms = []
    try:
        for line in open('ignition_watchlist.txt'):
            p = [x.strip().upper() for x in line.split(',')]
            if len(p) > 1 and p[1] == 'CORE': syms.append(p[0])
    except Exception:
        pass
    if 'DUSK' not in syms: syms.append('DUSK')
    return syms

def get_regime(ex):
    """Return dict(state 'ON'/'OFF', slope %, n coins) from the CORE alt-basket 50d MA slope, or None."""
    series = []
    for s in _core_syms():
        try:
            o = ex.fetch_ohlcv(f"{s}/USDT", '1d', limit=SMA_D + SLOPE_N + 15)
            if len(o) < SMA_D + 5: continue
            ser = pd.Series([b[4] for b in o],
                            index=pd.to_datetime([b[0] for b in o], unit='ms').normalize())
            series.append(ser)
        except Exception:
            continue
    if len(series) < 3: return None
    px = pd.concat(series, axis=1).sort_index()
    basket = (1 + px.pct_change().mean(axis=1)).cumprod()      # equal-weight alt index
    ma = basket.rolling(SMA_D).mean().dropna()
    if len(ma) < SLOPE_N + 1: return None
    cur, past = ma.iloc[-1], ma.iloc[-1 - SLOPE_N]
    slope = (cur/past - 1) * 100
    return dict(state=('ON' if cur > past else 'OFF'), slope=slope, n=len(series))

def _label(state): return "RISK-ON 🟢" if state == 'ON' else "RISK-OFF 🔴"

def flip_msg(reg, prev):
    guide = ("alt market 50d trend turned UP — ignition edge historically positive (incl. recently). "
             "Engage selectively."
             if reg['state'] == 'ON' else
             "alt market 50d trend turned DOWN — strategy underperforms here; size down / stand aside "
             "until it flips back.")
    return (f"🔄 REGIME FLIP: {_label(prev)} → {_label(reg['state'])}\n"
            f"CORE alt-basket {SMA_D}d MA is {reg['slope']:+.1f}% over {SLOPE_N}d ({reg['n']} coins).\n"
            f"{guide}\nNot financial advice.")

def line(reg):
    return "" if not reg else (f"\n\n🧭 Regime: {_label(reg['state'])} "
                               f"(alt-basket {SMA_D}d MA {reg['slope']:+.1f}% / {SLOPE_N}d)")
