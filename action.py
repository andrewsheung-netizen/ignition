"""
Action helper for the ignition scanner — turns a fired coin's price into the concrete PLAY, per the
validated strategy in DUSK_TRADING_RULES.md.

BASE: trade the whole responder list, risk 1% of capital, -15% stop, take-profit +18-22% (scale 1/2 /
ride 1/2 or trail), ~5 concurrent. Tier + regime shown as a CONVICTION note.

ENTRY FILTERS (validated 2026-06, full-history backtest, and they STACK -> Sharpe ~1.58 -> 2.16):
  #1 FUNDING: ignitions fade when funding is positive (crowded longs). Skip when trailing/current 8h
     funding >= FUNDING_POS_8H (~+11% APR, the Binance baseline).
  #4 ETH/BTC REGIME: ignitions work when ETH/BTC is BELOW its ~20d MA (capital rotating past ETH into
     small high-beta alts). Skip when ETH/BTC is above its MA (ETH-led tape).
We ALERT either way but mark SKIP/fade when a filter is unfavorable (discretion retained). funding and
ethbtc_off are passed in by scan_notify; if unavailable they're treated as neutral (no skip). Not advice.
"""
STOP, RISK = 0.15, 0.01
SIZE_PCT = RISK / STOP * 100        # 1% risk / 15% stop = ~6.7% of capital per position
FUNDING_POS_8H = 0.0001             # #1: skip when 8h funding >= this (~+11% APR / Binance baseline)

def _is_core(tier): return (tier or "").strip().upper() == "CORE"
def _regime_state(reg):
    if not reg: return None
    st = reg.get("state"); return st if st in ("ON", "OFF") else None
def _p(x): return f"{x:.6g}"

def _conviction(core, state, tier):
    if core and state == "ON":          return "⭐CORE + RISK-ON 🟢 — highest conviction (the A-book)"
    if core and state == "OFF":         return "⭐CORE + RISK-OFF 🔴 — solid coin, weak tape → trim size"
    if (not core) and state == "ON":    return f"{tier} + RISK-ON 🟢 — lower conviction (small caps lag CORE here)"
    if (not core) and state == "OFF":   return f"{tier} + RISK-OFF 🔴 — rotation favourable; thin liquidity → keep small"
    return f"{tier} · regime n/a"

def action_line(r, reg, funding=None, ethbtc_off=None):
    """Concrete play for a FIRED coin + the #1/#4 entry-filter verdict.
    funding   = current 8h funding rate (float) or None.
    ethbtc_off= True if ETH/BTC is BELOW its MA (favourable), False if above (unfavourable), None if n/a."""
    close = r.get("close") or 0.0
    stop_px = close * (1 - STOP); tp1 = close * 1.18; tp2 = close * 1.22; tp_run = close * 1.27
    core = _is_core(r.get("tier")); state = _regime_state(reg); tier = (r.get("tier") or "BROAD").upper()
    conv = _conviction(core, state, tier)

    # filter context lines
    if funding is None:
        fline = "funding: n/a"
    else:
        fline = f"funding {funding*100:+.3f}%/8h (~{funding*3*365*100:+.0f}%/yr)"
    eline = ("ETH/BTC below MA — alt-rotation ✓" if ethbtc_off is True else
             "ETH/BTC above MA — ETH-led tape ✗" if ethbtc_off is False else "ETH/BTC: n/a")

    reasons = []
    if funding is not None and funding >= FUNDING_POS_8H: reasons.append("funding positive/crowded (#1)")
    if ethbtc_off is False:                               reasons.append("ETH/BTC above MA (#4)")

    if reasons:   # one or both filters unfavourable -> still alert, but mark SKIP/fade
        return "\n   ".join([
            f"⛔ SKIP / FADE per entry filters: {', '.join(reasons)}",
            f"{fline}  ·  {eline}",
            f"(backtest: these underperform; if you override, trade small. stop −15% = ${_p(stop_px)})",
            f"conviction: {conv}",
        ])
    return "\n   ".join([     # both filters favourable (or n/a) -> the A-setup
        f"▶ PLAY: TRADE ✅ entry filters OK ({fline} · {eline})",
        f"risk 1% of capital → position ~{SIZE_PCT:.1f}%  ·  stop −15% = ${_p(stop_px)}",
        f"take-profit: ½ at +18% (${_p(tp1)}), ride ½ → +22% (${_p(tp2)}) or trail toward +27% (${_p(tp_run)})",
        f"conviction: {conv}",
    ])

def discipline_footer():
    return ("⚠ Whole list · risk 1% of capital (~6.7% position) · −15% stop · TP +18–22% · ~5 max concurrent · "
            "ENTRY FILTERS (validated, they stack): trade only when funding ≤ ~neutral AND ETH/BTC below its "
            "MA; SKIP/fade otherwise. NEVER hold, exit into the swing. Not financial advice.")
