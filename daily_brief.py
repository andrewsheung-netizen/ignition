"""
DAILY CATALYST BRIEF — deterministic, token-free (no LLM). Runs on a daily cron like the scanner.

For every watchlist coin it pulls recent catalyst news (via catalyst_news.py: NewsData + GNews +
CryptoCompare), tags 🔥 bullish / ⚠️ risk, flags likely-UPCOMING items from forward-tense headlines,
LOGS catalysts for ALL coins (running history, deduped), and builds a Telegram brief:
  * DUSK always on top (its daily watch — reported even if quiet; logged to DUSK_catalyst_log.md)
  * UPCOMING CATALYSTS (watchlist) — coins with forward-looking catalysts (position-ahead names)
  * recent catalysts — coins with material recent news
  * coins with nothing are omitted
Logs: DUSK -> DUSK_catalyst_log.md ; others -> catalyst_log_watchlist.md (dedup by coin+headline).

Forward events are best-effort from headline language; for a true dated calendar, add CoinMarketCal
(set COINMARKETCAL_KEY) — left as a hook. Not financial advice.
Run:  python3 daily_brief.py   (env: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, optional NEWSDATA_KEY/GNEWS_KEY)
"""
import os, re, datetime, requests
import catalyst_news as cn

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHFILE = os.path.join(HERE, 'ignition_watchlist.txt')
DUSK_LOG = os.path.join(HERE, 'DUSK_catalyst_log.md')
WL_LOG = os.path.join(HERE, 'catalyst_log_watchlist.md')
DIGEST = os.path.join(HERE, 'DUSK_catalyst_digest.md')
TODAY = datetime.date.today().isoformat()
FUTURE = re.compile(r"\b(will|to|upcoming|scheduled|set to|soon|launch(es|ing)?|listing|to list|"
                    r"goes? live|mainnet|testnet|airdrop|unlock|tge|debuts?|to add|next week|this week)\b", re.I)

def load_watch():
    out = []
    for l in open(WATCHFILE):
        if not l.strip(): continue
        p = [x.strip().upper() for x in l.split(',')]
        out.append((p[0], p[1] if len(p) > 1 else ''))
    return out

def send(text):
    # Brief uses its OWN bot, separate from the live scanner. Falls back to the scanner's creds if
    # the brief-specific ones aren't set. (chat_id for a private chat = your user id, the same across
    # bots — but you must /start the new bot once so it's allowed to message you.)
    tok = os.environ.get("BRIEF_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("BRIEF_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: print("(no Telegram creds; brief below)\n" + text); return
    for i in range(0, len(text), 3800):                       # Telegram 4096-char cap
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text[i:i+3800]}, timeout=20)

def is_upcoming(title): return bool(FUTURE.search(title or ""))
def _key(sym, title): return f"{sym}|{re.sub(r'[^a-z0-9]', '', title.lower())[:50]}"

def main():
    watch = load_watch()
    dusk_tier = dict(watch).get('DUSK', 'CORE')
    # existing watchlist-log keys for dedup
    seen = set()
    if os.path.exists(WL_LOG):
        for line in open(WL_LOG):
            parts = [x.strip() for x in line.split('|')]   # date|SYM|emoji|UPCOMING/RECENT|title|url
            if len(parts) >= 5 and re.match(r'\d{4}-\d{2}-\d{2}$', parts[0]):
                seen.add(_key(parts[1], parts[4]))

    dusk = {'posts': [], 'cats': []}
    upcoming, recent = [], []          # (sym,tier,emoji,kw,title,url)
    wl_log_new = []
    for sym, tier in watch:
        try: posts = cn.gather(sym)
        except Exception as e: print(f"{sym}: {str(e)[:50]}"); posts = []
        cats = [(p, cn._tag(p['title'])) for p in posts]
        cats = [(p, tg) for p, tg in cats if tg]               # only catalyst-tagged
        if sym == 'DUSK':
            dusk = {'posts': posts, 'cats': cats}; continue
        if not cats: continue
        for p, (emoji, kw) in cats:
            up = is_upcoming(p['title'])
            rec = (sym, tier, emoji, kw, p['title'], p.get('url', ''))
            (upcoming if up else recent).append(rec)
            k = _key(sym, p['title'])
            if k not in seen:
                seen.add(k)
                wl_log_new.append(f"{TODAY} | {sym} | {emoji} | {'UPCOMING' if up else 'RECENT'} | "
                                  f"{p['title'][:90]} | {p.get('url','')}")

    # ---- logs ----
    if wl_log_new:
        hdr = "" if os.path.exists(WL_LOG) else "# Watchlist catalyst log (auto, daily_brief.py)\n\n"
        with open(WL_LOG, 'a') as f:
            if hdr: f.write(hdr)
            f.write("\n".join(wl_log_new) + "\n")
    # DUSK log: append a dated line every run (even if quiet)
    dline = (f"{TODAY} | DUSK | " +
             ("; ".join(f"{cn._tag(p['title'])[0]} {p['title'][:70]}" for p, _ in dusk['cats'][:3])
              if dusk['cats'] else "no new catalysts"))
    with open(DUSK_LOG, 'a') as f: f.write("\n" + dline)

    # ---- brief ----
    L = [f"📅 DUSK watchlist — catalyst brief {TODAY}", ""]
    # DUSK always top
    L.append(f"⭐ DUSK ({dusk_tier})")
    if dusk['cats']:
        for p, (emoji, kw) in dusk['cats'][:4]:
            L.append(f"   {emoji} {p['title'][:90]}{(' '+p['url']) if p.get('url') else ''}")
    else:
        L.append("   · no new catalysts (quiet) — watch for a 4h ignition")
    L.append("")
    # Upcoming across watchlist
    L.append("🔭 UPCOMING CATALYSTS (watchlist):")
    if upcoming:
        for sym, tier, emoji, kw, title, url in upcoming[:20]:
            L.append(f"   {emoji} {sym} [{tier}] — {title[:80]}{(' '+url) if url else ''}")
    else:
        L.append("   (none detected today)")
    # Recent
    if recent:
        L.append("")
        L.append("📰 recent catalysts:")
        for sym, tier, emoji, kw, title, url in recent[:15]:
            L.append(f"   {emoji} {sym} [{tier}] — {title[:80]}")
    L.append("")
    L.append("Catalyst = context/conviction; entry is still the 4h ignition. Not financial advice.")
    brief = "\n".join(L)

    open(DIGEST, 'w').write(brief + "\n")
    send(brief)
    print(f"brief sent | upcoming {len(upcoming)} recent {len(recent)} | logged {len(wl_log_new)} new -> {WL_LOG}")

if __name__ == '__main__':
    main()
