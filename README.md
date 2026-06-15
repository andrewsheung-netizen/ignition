# DUSK Ignition — cloud scanner

Always-on 4h ignition scanner. Checks the responder watchlist on each closed 4h candle and pings
Telegram when a coin fires. Runs on GitHub Actions. Not financial advice.

## Entry signal
4h close, ALL true: volume ≥ 3× avg(prior 6) · green breakout (close>open and close>prev close) ·
off-base (close ≤ SMA50×1.05) · 18-bar (3-day) cooldown.

## Entry filters (validated 2026-06, full-history backtest; they STACK)
Shown on every IGNITION alert; the bot still alerts on every fire and marks **⛔ SKIP/FADE** vs
**▶ TRADE ✅** so the call stays discretionary.

- **#1 Funding** — skip when current 8h funding ≥ ~`0.0001` (≈ +11% APR, Binance baseline). Ignitions
  fade into crowded-long funding. Sharpe 1.53→1.87 alone.
- **#4 ETH/BTC regime** — trade only when ETH/BTC is **below** its 20-day MA (capital rotating past ETH
  into small high-beta alts); skip when above (ETH-led tape). Sharpe 1.58→1.86 alone.
- **Combined (require both):** Sharpe ~2.16, MAR ~15, recent-fold F3 +27% — best configuration found.
  (In-sample/bull-inflated; expect forward compression. The relative lift is the signal.)
- Tested and dropped: volume buy-fraction gate (#2) and cross-sectional selection (#3) — no portfolio edge.

Implemented in `action.py` (verdict/message) + `scan_notify.py` (`ethbtc_regime`, `current_funding`).

## Key files
`scan_notify.py` scanner+alerts · `action.py` play+filters · `regime.py` CORE-basket RISK-ON/OFF ·
`catalyst_news.py` / `daily_brief.py` / `coinmarketcal.py` news · `ignition_watchlist.txt` SYM,TIER.
Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` (+ optional `BRIEF_BOT_TOKEN`/`BRIEF_CHAT_ID`,
`CRYPTOPANIC_TOKEN`, `COINMARKETCAL_KEY`).
