"""
DUSK paper trader — weekly P&L scorecard. Reads paper_state.json (written by paper_trader.py) and sends a
once-a-week Telegram summary per bot: balance, total return, win rate, avg win/loss, profit factor, max
drawdown, best/worst trade, and exit-reason mix. Read-only (no state changes). Not financial advice.

Run:  python3 paper_report.py        (scheduled weekly via paper-report.yml)
"""
import os, json, datetime, requests

START_BAL = 25000.0
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'paper_state.json')

def send(text):
    tok = os.environ.get("PAPER_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("PAPER_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; message below)\n" + text); return
    try: requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e: print(f"telegram failed: {e}\n{text}")

def maxdd(closed):
    """Max drawdown of the realized-equity curve (START + cumulative closed P&L, in exit order)."""
    eq = START_BAL; peak = START_BAL; mdd = 0.0
    for c in sorted(closed, key=lambda x: x.get("xts", 0)):
        eq += c["pnl"]; peak = max(peak, eq); mdd = max(mdd, (peak - eq)/peak if peak > 0 else 0)
    return 100*mdd

def scorecard(bot, label):
    closed = bot.get("closed", []); bal = bot.get("bal", START_BAL)
    ret = 100*(bal/START_BAL - 1); nopen = len(bot.get("pos", {}))
    if not closed:
        return f"{label}: ${bal:,.0f} ({ret:+.1f}%) · {nopen} open · no closed trades yet"
    wins = [c for c in closed if c["pnl"] > 0]; losses = [c for c in closed if c["pnl"] <= 0]
    n = len(closed); wr = 100*len(wins)/n
    aw = sum(c["ret"] for c in wins)/len(wins) if wins else 0
    al = sum(c["ret"] for c in losses)/len(losses) if losses else 0
    gw = sum(c["pnl"] for c in wins); gl = sum(c["pnl"] for c in losses)
    pf = (gw/abs(gl)) if gl < 0 else float('inf')
    best = max(closed, key=lambda c: c["pnl"]); worst = min(closed, key=lambda c: c["pnl"])
    why = {}
    for c in closed: why[c["why"]] = why.get(c["why"], 0) + 1
    whystr = ", ".join(f"{k} {v}" for k, v in sorted(why.items(), key=lambda x: -x[1]))
    return ("\n".join([
        f"{label}: ${bal:,.0f} ({ret:+.1f}%) · {nopen} open",
        f"   trades {n} · win {wr:.0f}% · avg win {aw:+.1f}% / avg loss {al:+.1f}% · PF {pf:.2f}",
        f"   max drawdown {maxdd(closed):.0f}% · best {best['sym']} {best['ret']:+.0f}% / worst {worst['sym']} {worst['ret']:+.0f}%",
        f"   exits: {whystr}",
    ]))

def main():
    try: s = json.load(open(STATE_FILE))
    except Exception:
        send("📊 DUSK PAPER weekly — no paper_state.json yet (paper trader hasn't run). Not financial advice."); return
    now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
    msg = ["📊 DUSK PAPER — weekly scorecard (" + now + ")",
           scorecard(s.get("A", {}), "Bot A  mech  +20/−15"),
           scorecard(s.get("B", {}), "Bot B  scale ½@18/trail"),
           "(paper — virtual money, real prices. Bot A = backtest match; B = scale-out. Not financial advice.)"]
    send("\n".join(msg))

if __name__ == '__main__':
    main()
