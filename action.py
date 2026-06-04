"""
Action helper for the ignition scanner — turns a fired coin's price into the concrete PLAY, per the
validated best strategy in DUSK_TRADING_RULES.md.

BASE STRATEGY (validated by the portfolio + true-OOS tests): trade the WHOLE responder list UNGATED,
uniform sizing — risk 1% of capital, −15% stop, take-profit +18–22% (scale ½ / ride ½ or trail),
~5 concurrent. We do NOT skip or hard-resize by regime; tier + regime are shown only as a CONVICTION
note (CORE+RISK-ON = the A-book; thin coin in a weak regime = lower conviction → trim at discretion).

Risk & size are expressed as % of CAPITAL (capital-agnostic — no $ to set or maths to do). Stop & take-
profit are shown as actual price levels off the fired close. Not financial advice.
"""
STOP, RISK = 0.15, 0.01
SIZE_PCT = RISK / STOP * 100        # 1% risk / 15% stop = ~6.7% of capital per position

def _is_core(tier): return (tier or "").strip().upper() == "CORE"
def _regime_state(reg):
    if not reg: return None
    st = reg.get("state"); return st if st in ("ON", "OFF") else None
def _p(x): return f"{x:.6g}"

def action_line(r, reg):
    """Concrete play for a FIRED coin (uniform ungated strategy) + a conviction note from tier+regime.
    Risk/size as % of capital; stop/TP as price levels. `r` = check() row; `reg` = get_regime() or None."""
    close = r.get("close") or 0.0
    stop_px = close * (1 - STOP); tp1 = close * 1.18; tp2 = close * 1.22; tp_run = close * 1.27
    core = _is_core(r.get("tier")); state = _regime_state(reg); tier = (r.get("tier") or "BROAD").upper()
    if core and state == "ON":          conv = "⭐CORE + RISK-ON 🟢 — highest conviction (the A-book)"
    elif core and state == "OFF":       conv = "⭐CORE + RISK-OFF 🔴 — solid coin, weak tape → trim size"
    elif (not core) and state == "ON":  conv = f"{tier} + RISK-ON 🟢 — lower conviction (small caps lag CORE here)"
    elif (not core) and state == "OFF": conv = f"{tier} + RISK-OFF 🔴 — rotation favourable; thin liquidity → keep small"
    else:                               conv = f"{tier} · regime n/a"
    return "\n   ".join([
        f"▶ PLAY: TRADE — risk 1% of capital → position ~{SIZE_PCT:.1f}%  ·  stop −15% = ${_p(stop_px)}",
        f"take-profit: ½ at +18% (${_p(tp1)}), ride ½ → +22% (${_p(tp2)}) or trail toward +27% (${_p(tp_run)})",
        f"conviction: {conv}",
    ])

def discipline_footer():
    return ("⚠ Whole list, UNGATED · risk 1% of capital (~6.7% position) · −15% stop (a floor — gaps slip) "
            "· TP +18–22% · ~5 max concurrent (~5% total open risk) · NEVER hold, exit into the swing. "
            "Regime/tier = conviction only. Not financial advice.")
