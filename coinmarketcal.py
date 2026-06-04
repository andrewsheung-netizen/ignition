"""
CoinMarketCal — TRUE dated catalyst calendar (free API key). Deterministic, token-free.

Unlike catalyst_news.py (recent headlines, best-effort "upcoming" from forward-tense language),
this pulls events with HARD DATES: listings, mainnets, unlocks, upgrades, conferences, etc.

Flow: resolve watchlist tickers -> CoinMarketCal coin IDs (cached to disk so we rarely hit /coins),
then GET /events filtered to those coins over the next N days. Safe no-op if COINMARKETCAL_KEY is
unset (the brief just falls back to headline-based upcoming). Not financial advice.

Get a free key at https://coinmarketcal.com/en/developer  ->  set env COINMARKETCAL_KEY.
"""
import os, json, datetime, requests

BASE = "https://developers.coinmarketcal.com/v1"
HERE = os.path.dirname(os.path.abspath(__file__))
IDMAP = os.path.join(HERE, "cmc_cal_coinmap.json")     # cached symbol -> coin id


def _headers():
    return {"x-api-key": os.environ.get("COINMARKETCAL_KEY", ""),
            "Accept": "application/json",
            "Accept-Encoding": "deflate, gzip"}        # CMC-Cal requires Accept-Encoding


def _coin_id_map(force=False):
    """symbol(UPPER) -> CoinMarketCal coin id. Cached to disk; only refetched if missing/forced."""
    if not force and os.path.exists(IDMAP):
        try:
            m = json.load(open(IDMAP))
            if m: return m
        except Exception:
            pass
    out = {}
    try:
        for page in range(1, 60):                       # ~100/page; stop on short/empty page
            r = requests.get(f"{BASE}/coins", headers=_headers(),
                             params={"page": page, "max": 100}, timeout=20)
            if r.status_code != 200:
                if page == 1: print(f"cmc-cal /coins {r.status_code}")
                break
            data = r.json()
            rows = data.get("body", data) if isinstance(data, dict) else data
            if not rows: break
            for c in rows:
                sym = (c.get("symbol") or "").upper()
                if sym and sym not in out:              # first (highest-rank) wins on ticker clashes
                    out[sym] = c.get("id")
            if len(rows) < 100: break
    except Exception as e:
        print(f"cmc-cal coins map failed: {str(e)[:60]}")
    if out:
        json.dump(out, open(IDMAP, "w"))
    return out


def upcoming(symbols, days=45, max_events=150):
    """Return hard-dated upcoming events for the given tickers, soonest first.
    [] if no key, no id matches, or the API is unreachable."""
    if not os.environ.get("COINMARKETCAL_KEY"):
        return []
    idmap = _coin_id_map()
    want = {s.upper() for s in symbols}
    ids = [idmap[s] for s in want if s in idmap]
    if not ids:
        return []
    today = datetime.date.today()
    params = {"max": max_events, "page": 1,
              "dateRangeStart": today.isoformat(),
              "dateRangeEnd": (today + datetime.timedelta(days=days)).isoformat(),
              "coins": ",".join(ids), "sortBy": "created_desc"}
    try:
        r = requests.get(f"{BASE}/events", headers=_headers(), params=params, timeout=25)
        if r.status_code != 200:
            print(f"cmc-cal /events {r.status_code}: {r.text[:80]}"); return []
        body = r.json().get("body", [])
    except Exception as e:
        print(f"cmc-cal events failed: {str(e)[:60]}"); return []
    out = []
    for e in body:
        title = ((e.get("title") or {}).get("en") or "").strip()
        date = (e.get("date_event") or "")[:10]
        url = e.get("source") or e.get("proof") or ""
        conf = e.get("percentage")                      # crowd confidence 0-100
        cats = ", ".join(c.get("name", "") for c in e.get("categories", []) if c.get("name"))
        for c in e.get("coins", []):
            s = (c.get("symbol") or "").upper()
            if s in want:
                out.append({"sym": s, "date": date, "title": title,
                            "url": url, "conf": conf, "cat": cats})
    out.sort(key=lambda x: (x["date"] or "9999-99-99"))
    return out


if __name__ == "__main__":                              # quick manual check
    import sys
    syms = sys.argv[1:] or ["DUSK", "JUP", "AAVE", "INJ"]
    evs = upcoming(syms)
    if not evs:
        print("(no events — check COINMARKETCAL_KEY is set and reachable)")
    for e in evs:
        print(f"{e['date']}  {e['sym']:6}  {e['title'][:70]}  ({e['cat']}, {e['conf']}%)")
