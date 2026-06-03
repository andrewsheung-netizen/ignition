"""
Catalyst-news screen for a single ticker — used by scan_notify.py when a coin IGNITES.
Pulls recent headlines from up to THREE free sources, merges/dedupes, tags catalysts:
  1. NewsData.io   (free key, env NEWSDATA_KEY) — keyword search by the coin's FULL NAME + crypto ctx
  2. GNews         (free key, env GNEWS_KEY)     — same name+ctx search; good small-cap coverage
  3. CryptoCompare (no key)                       — general crypto feed, filtered to the ticker
(CryptoPanic was removed — its API is no longer free.)

Querying the coin's full name (e.g. "Dusk Network") with a crypto-context filter catches far more
small-cap catalysts than the bare ticker. Results are post-filtered for relevance (name or ticker
must appear in the headline/description). Then catalyst keywords are tagged (🔥 bullish / ⚠️ risk),
and a Telegram message is built: tagged items first, else the latest 3 headlines as a fallback.

Only dep is `requests`. Safe to import with no keys (those sources just skip). Not financial advice.
"""
import os, re, time, requests

DAYS = 14
TIMEOUT = 15
UA = {'User-Agent': 'ignition-catalyst-screen'}
CTX = "(crypto OR cryptocurrency OR token OR blockchain OR coin OR web3 OR DeFi)"

# ticker -> full project name, so news search hits articles that don't use the ticker symbol.
# Only confident names are listed; unknown tickers fall back to a ticker-only search.
NAMES = {
    "ZEC":"Zcash","BONK":"Bonk","WIF":"dogwifhat","FET":"Fetch.ai","PENDLE":"Pendle","AAVE":"Aave",
    "JTO":"Jito","JUP":"Jupiter","INJ":"Injective","RAY":"Raydium","WLD":"Worldcoin",
    "PYTH":"Pyth Network","AVAX":"Avalanche","JASMY":"Jasmy","ALGO":"Algorand","DUSK":"Dusk Network",
    "HUMA":"Huma Finance","AGLD":"Adventure Gold","EDU":"Open Campus","BNX":"BinaryX",
    "HOLO":"Holoworld","ZEN":"Horizen","BOME":"Book of Meme","USUAL":"Usual","TURBO":"Turbo",
    "GALA":"Gala","XAI":"Xai","NIL":"Nillion","POLYX":"Polymesh","XVG":"Verge","PORTAL":"Portal",
    "SNT":"Status","ROSE":"Oasis Network","RED":"RedStone","API3":"API3","STX":"Stacks",
    "KAITO":"Kaito","REZ":"Renzo","SLP":"Smooth Love Potion","SEI":"Sei","PROS":"Prosper",
    "ZRO":"LayerZero","PEOPLE":"ConstitutionDAO","LPT":"Livepeer","SCRT":"Secret Network","AXL":"Axelar",
}

BULL_KW = ["listing","list on","lists ","listed","binance","coinbase","kraken","okx","upbit","bybit",
           "kucoin","bitget","partner","integrat","launch","mainnet","testnet","go live","golive",
           "upgrade","collaborat","acquire","acquisition","custody","etf","funding","raises","raised",
           "investment","staking","airdrop","buyback","burn","tokeniz","rwa","treasury","grant",
           "whitelist","tge"]
RISK_KW = ["unlock","hack","exploit","lawsuit","delist","sec charges","sec sues","rug","drain","outage","halt"]

def _ago(ts):
    d = int(time.time()) - int(ts)
    if d < 3600:  return f"{max(d//60,0)}m"
    if d < 86400: return f"{d//3600}h"
    return f"{d//86400}d"

def _iso_ts(s):
    if not s: return int(time.time())
    s = str(s).strip().replace('Z', '+00:00').replace(' ', 'T', 1)
    try:
        import datetime
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except Exception:
        return int(time.time())

def _query(sym):
    name = NAMES.get(sym.upper())
    return (f'"{name}" AND {CTX}' if name else f'{sym} AND {CTX}'), name

def _newsdata(sym):
    key = os.environ.get('NEWSDATA_KEY')
    if not key: return []
    q, name = _query(sym)
    try:
        r = requests.get("https://newsdata.io/api/1/latest", headers=UA, timeout=TIMEOUT,
                         params={'apikey': key, 'q': q, 'language': 'en'})
        if r.status_code != 200:
            print(f"  newsdata {sym}: HTTP {r.status_code}"); return []
        out = []
        for a in r.json().get('results', []) or []:
            title = (a.get('title') or '').strip()
            if not title: continue
            out.append(dict(title=title, url=a.get('link') or '', ts=_iso_ts(a.get('pubDate')),
                            source=a.get('source_id') or 'newsdata', desc=a.get('description') or ''))
        return out
    except Exception as e:
        print(f"  newsdata {sym} err: {str(e)[:50]}"); return []

def _gnews(sym):
    key = os.environ.get('GNEWS_KEY')
    if not key: return []
    q, name = _query(sym)
    try:
        r = requests.get("https://gnews.io/api/v4/search", headers=UA, timeout=TIMEOUT,
                         params={'q': q, 'lang': 'en', 'max': 10, 'apikey': key, 'sortby': 'publishedAt'})
        if r.status_code != 200:
            print(f"  gnews {sym}: HTTP {r.status_code}"); return []
        out = []
        for a in r.json().get('articles', []) or []:
            title = (a.get('title') or '').strip()
            if not title: continue
            out.append(dict(title=title, url=a.get('url') or '', ts=_iso_ts(a.get('publishedAt')),
                            source=(a.get('source') or {}).get('name') or 'gnews', desc=a.get('description') or ''))
        return out
    except Exception as e:
        print(f"  gnews {sym} err: {str(e)[:50]}"); return []

_CC_CACHE = {'ts': 0, 'data': None}
def _cryptocompare_all():
    if _CC_CACHE['data'] is not None and time.time() - _CC_CACHE['ts'] < 300:
        return _CC_CACHE['data']
    try:
        r = requests.get("https://data-api.cryptocompare.com/news/v1/article/list",
                         headers=UA, timeout=TIMEOUT, params={'lang': 'EN', 'limit': 100})
        r.raise_for_status()
        data = []
        for a in r.json().get('Data', []):
            title = (a.get('TITLE') or '').strip()
            if not title: continue
            cats = " ".join(c.get('CATEGORY', '') for c in a.get('CATEGORY_DATA', []))
            data.append(dict(title=title, url=a.get('URL') or '',
                             ts=int(a.get('PUBLISHED_ON') or time.time()),
                             source=(a.get('SOURCE_DATA') or {}).get('NAME') or 'cryptocompare',
                             desc='', hay=f"{title} {a.get('KEYWORDS','')} {cats}".upper()))
        _CC_CACHE.update(ts=time.time(), data=data); return data
    except Exception as e:
        print(f"  cryptocompare err: {str(e)[:50]}"); return []

def _relevant(sym, name, title, desc):
    h = f"{title} {desc or ''}".lower()
    if name and name.lower() in h: return True
    return re.search(rf'\b{re.escape(sym.lower())}\b', h) is not None

def _norm(t): return re.sub(r'[^a-z0-9]', '', t.lower())[:60]

def gather(sym, days=DAYS):
    sym = sym.upper(); name = NAMES.get(sym)
    posts = _newsdata(sym) + _gnews(sym)
    posts = [p for p in posts if _relevant(sym, name, p['title'], p.get('desc'))]
    pat = re.compile(rf'\b{re.escape(sym)}\b')
    posts += [p for p in _cryptocompare_all() if pat.search(p['hay']) or (name and name.upper() in p['hay'])]
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
    sym = sym.upper(); posts = gather(sym, days)
    if not posts:
        return f"📰 {sym} — catalyst check: no ticker news in last {days}d (NewsData+GNews+CryptoCompare)."
    cats = [(p, tg) for p in posts if (tg := _tag(p['title']))]
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
