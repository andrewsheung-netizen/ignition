"""
Catalyst-news screen for a single ticker — used by scan_notify.py when a coin IGNITES.
Pulls recent headlines from TWO sources and merges/dedupes them:
  1. CryptoPanic   (per-ticker, best) — needs a free token in env CRYPTOPANIC_TOKEN
  2. CryptoCompare (no key) — general crypto feed, filtered to the ticker by symbol match

Then tags headlines that look like catalysts (listing, partnership, launch, mainnet, ...) and
builds a Telegram message: catalyst-tagged items first; if none match in the window, the latest
3 headlines as a fallback so you always get a read. Bearish items (unlock, hack...) flagged too.

No heavy deps — just `requests`. Safe to import even with no token (CryptoPanic just skipped).
Not financial advice.
"""
import os, re, time, requests

DAYS = 14
TIMEOUT = 15
UA = {'User-Agent': 'ignition-catalyst-screen'}

# bullish catalysts -> 🔥 ; risks -> ⚠️ (checked first so warnings surface)
BULL_KW = ["listing", "list on", "lists ", "listed", "binance", "coinbase", "kraken", "okx",
           "upbit", "bybit", "kucoin", "bitget", "partner", "integrat", "launch", "mainnet",
           "testnet", "go live", "golive", "upgrade", "collaborat", "acquire", "acquisition",
           "custody", "etf", "funding", "raises", "raised", "investment", "staking", "airdrop",
           "buyback", "burn", "tokeniz", "rwa", "treasury", "grant", "whitelist", "tge", "listing on"]
RISK_KW = ["unlock", "hack", "exploit", "lawsuit", "delist", "sec charges", "sec sues",
           "rug", "drain", "outage", "halt"]

def _ago(ts):
    d = int(time.time()) - int(ts)
    if d < 3600:  return f"{max(d//60,0)}m"
    if d < 86400: return f"{d//3600}h"
    return f"{d//86400}d"

def _cryptopanic(sym, token):
    """Per-currency feed. Tries the classic v1 then the developer v2 endpoint."""
    urls = [f"https://cryptopanic.com/api/v1/posts/?auth_token={token}&currencies={sym}&public=true",
            f"https://cryptopanic.com/api/developer/v2/posts/?auth_token={token}&currencies={sym}"]
    for u in urls:
        try:
            r = requests.get(u, headers=UA, timeout=TIMEOUT)
            if r.status_code != 200:
                print(f"  cryptopanic {sym}: HTTP {r.status_code}"); continue
            js = r.json(); res = js.get('results') or js.get('data') or []
            out = []
            for p in res:
                title = p.get('title') or (p.get('instruments') and '') or ''
                if not title: continue
                pub = p.get('published_at') or p.get('published') or ''
                ts = _iso_ts(pub)
                src = (p.get('source') or {}).get('domain') or (p.get('source') or {}).get('title') or 'cryptopanic'
                out.append(dict(title=title.strip(), url=p.get('url') or '', ts=ts, source=src, hay=title.upper()))
            if out: return out
        except Exception as e:
            print(f"  cryptopanic {sym} err: {str(e)[:50]}")
    return []

def _iso_ts(s):
    if not s: return int(time.time())
    try:
        import datetime
        s = s.replace('Z', '+00:00')
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except Exception:
        return int(time.time())

_CC_CACHE = {'ts': 0, 'data': None}
def _cryptocompare_all():
    """One shared fetch of the latest ~100 crypto headlines (no key). Cached 5 min per process."""
    if _CC_CACHE['data'] is not None and time.time() - _CC_CACHE['ts'] < 300:
        return _CC_CACHE['data']
    try:
        u = "https://data-api.cryptocompare.com/news/v1/article/list?lang=EN&limit=100"
        r = requests.get(u, headers=UA, timeout=TIMEOUT); r.raise_for_status()
        data = []
        for a in r.json().get('Data', []):
            title = (a.get('TITLE') or '').strip()
            if not title: continue
            cats = " ".join(c.get('CATEGORY', '') for c in a.get('CATEGORY_DATA', []))
            hay = f"{title} {a.get('KEYWORDS','')} {cats}".upper()
            data.append(dict(title=title, url=a.get('URL') or '',
                             ts=int(a.get('PUBLISHED_ON') or time.time()),
                             source=(a.get('SOURCE_DATA') or {}).get('NAME') or 'cryptocompare', hay=hay))
        _CC_CACHE.update(ts=time.time(), data=data); return data
    except Exception as e:
        print(f"  cryptocompare err: {str(e)[:50]}"); return []

def _norm(t): return re.sub(r'[^a-z0-9]', '', t.lower())[:60]

def gather(sym, days=DAYS):
    posts, token = [], os.environ.get('CRYPTOPANIC_TOKEN')
    if token: posts += _cryptopanic(sym, token)
    # CryptoCompare: keep only headlines that mention the ticker as a standalone token
    pat = re.compile(rf'\b{re.escape(sym.upper())}\b')
    posts += [p for p in _cryptocompare_all() if pat.search(p['hay'])]
    cutoff = int(time.time()) - days*86400
    posts = [p for p in posts if p['ts'] >= cutoff]
    seen, uniq = set(), []
    for p in sorted(posts, key=lambda x: -x['ts']):
        k = _norm(p['title'])
        if k in seen: continue
        seen.add(k); uniq.append(p)
    return uniq

def _tag(title):
    t = title.lower()
    for kw in RISK_KW:
        if kw in t: return ('⚠️', kw)
    for kw in BULL_KW:
        if kw in t: return ('🔥', kw)
    return None

def catalyst_message(sym, days=DAYS):
    posts = gather(sym, days)
    if not posts:
        return f"📰 {sym} — catalyst check: no ticker news in last {days}d (CryptoPanic+CryptoCompare)."
    tagged = [(p, _tag(p['title'])) for p in posts]
    cats = [(p, tg) for p, tg in tagged if tg]
    lines = [f"📰 {sym} — catalyst check (last {days}d)"]
    if cats:
        for p, (emoji, kw) in cats[:5]:
            ttl = p['title'][:90] + ('…' if len(p['title']) > 90 else '')
            link = f" {p['url']}" if p['url'] else ""
            lines.append(f"{emoji} [{_ago(p['ts'])}] {ttl} — {kw} ({p['source']}){link}")
    else:
        lines.append(f"· no catalyst keywords matched — latest {min(3,len(posts))} headlines:")
        for p in posts[:3]:
            ttl = p['title'][:90] + ('…' if len(p['title']) > 90 else '')
            link = f" {p['url']}" if p['url'] else ""
            lines.append(f"· [{_ago(p['ts'])}] {ttl} ({p['source']}){link}")
    lines.append("auto-screen · verify before acting · not financial advice")
    return "\n".join(lines)

if __name__ == '__main__':
    import sys
    print(catalyst_message(sys.argv[1].upper() if len(sys.argv) > 1 else 'DUSK'))
