"""
Regime detector for the ignition strategy — BTC daily trend vs its 50-day MA.

Validated (DUSK_regime_validate_v1.py): trading ignitions only when BTC > 50d MA roughly triples
per-trade geometric return (ON +1.9% vs OFF +0.69%) and halves the recent-period drawdown. It's a
damage-reducer, not a guarantee — RISK-ON = better odds, RISK-OFF = size down / stand aside.

Used by scan_notify.py to send a Telegram alert ONLY when the regime flips state. No keys needed.
Not financial advice.
"""
SMA_D = 50

def get_regime(ex):
    """Return dict(state 'ON'/'OFF', close, sma, pct) from BTC daily vs its 50d SMA, or None."""
    try:
        o = ex.fetch_ohlcv("BTC/USDT", '1d', limit=SMA_D + 10)
    except Exception as e:
        print(f"regime: BTC fetch failed {str(e)[:50]}"); return None
    closes = [b[4] for b in o]
    if len(closes) < SMA_D + 1: return None
    last = closes[-1]; sma = sum(closes[-SMA_D:]) / SMA_D
    return dict(state=('ON' if last > sma else 'OFF'), close=last, sma=sma, pct=last/sma - 1)

def _label(state): return "RISK-ON 🟢" if state == 'ON' else "RISK-OFF 🔴"

def flip_msg(reg, prev):
    guide = ("alts historically more favorable — ignition edge ~3x better when BTC is in uptrend. "
             "Still selective; recent regime only marginal."
             if reg['state'] == 'ON' else
             "ignition edge weak here — size down or stand aside until it flips back.")
    return (f"🔄 REGIME FLIP: {_label(prev)} → {_label(reg['state'])}\n"
            f"BTC ${reg['close']:,.0f} is {reg['pct']*100:+.1f}% vs its {SMA_D}-day MA.\n"
            f"{guide}\nNot financial advice.")

def line(reg):
    return "" if not reg else f"\n\n🧭 Regime: {_label(reg['state'])} (BTC {reg['pct']*100:+.1f}% vs {SMA_D}d MA)"
