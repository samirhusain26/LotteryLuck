"""Scrape scratch-off game data from lottery.net and its scratchcards.net detail pages.

Usable as a module (``scrape_lottery_net_df``) or as a CLI:

    python scrape.py --state NJ --csv scratchoffs_NJ.csv
"""
import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

import pandas as pd
import requests
from bs4 import BeautifulSoup

STATE_SLUGS = {
    "NJ": "new-jersey",
    "NY": "new-york",
    "PA": "pennsylvania",
    "TX": "texas",
    "CA": "california",
    "FL": "florida",
}

STATE_NAMES = {
    "NJ": "New Jersey",
    "NY": "New York",
    "PA": "Pennsylvania",
    "TX": "Texas",
    "CA": "California",
    "FL": "Florida",
}

BASE_URL = "https://www.lottery.net"
LIST_PATHS = {"CA": "scratchers"}

HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Referer": "https://www.lottery.net/",
}

DETAIL_DELAY_SECONDS = 1.0
MAX_CONSECUTIVE_FAILURES = 3

# ---------- Parsing helpers ----------
MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KMB])?\b", re.I)
PLAIN_NUMBER_RE = re.compile(r"([\d,]+(?:\.\d+)?)")
ODDS_RE = re.compile(r"1\s*in\s*([\d,]*\.?\d+)", re.I)
INT_RE = re.compile(r"\d[\d,]*")
# Prizes paid over time ("$1K/WK/LIFE", "$500 A Week For Life") have no single cash value.
ANNUITY_RE = re.compile(r"/|\blife\b|\bweek\b|\bwk\b|\bmonth\b|\bmo\b|\byear\b|\byr\b", re.I)
SUFFIX_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def is_ticket_prize(s: Optional[str]) -> bool:
    s = (s or "").lower()
    return "ticket" in s or "free" in s


def is_annuity_prize(s: Optional[str]) -> bool:
    return bool(s) and bool(ANNUITY_RE.search(s))


def to_float_money(s: Optional[str]) -> Optional[float]:
    """Parse a single cash amount. Returns None for free tickets, annuities, or text like 'BIG SPIN EVENT'."""
    if not s or is_ticket_prize(s) or is_annuity_prize(s):
        return None
    m = MONEY_RE.search(s)
    if m:
        value = float(m.group(1).replace(",", ""))
        suffix = (m.group(2) or "").upper()
        return value * SUFFIX_MULTIPLIERS.get(suffix, 1)
    m = PLAIN_NUMBER_RE.fullmatch(s.strip())
    return float(m.group(1).replace(",", "")) if m else None


def to_float_odds(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = ODDS_RE.search(s)
    return float(m.group(1).replace(",", "")) if m else None


def to_int_any(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = INT_RE.search(s)
    return int(m.group().replace(",", "")) if m else None


# ---------- Data model ----------
@dataclass
class ScratchRow:
    state: str
    source: str
    scrape_ts: str
    game_number: Optional[int]
    game_name: Optional[str]
    price: Optional[float]
    overall_odds_1_in: Optional[float]
    top_prize_text: Optional[str]
    top_prize_amount: Optional[float]
    top_prizes_remaining: Optional[int]
    status: str = "active"
    detail_url: Optional[str] = None
    prize_data: List[Dict] = field(default_factory=list)
    # Calculated from the tracked prize tiers on the detail page (not every tier is listed upstream).
    tracked_prizes_remaining: Optional[int] = None
    tracked_prizes_total: Optional[int] = None
    prize_pool_remaining_pct: Optional[float] = None


# ---------- Fetching / table parsing ----------
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HDRS, timeout=30)
    r.raise_for_status()
    return r.text


def table_headers(table) -> List[str]:
    thead = table.find("thead")
    if thead:
        headers = [clean(th.get_text(" ")) for th in thead.find_all("th")]
        if headers:
            return headers
    first_tr = table.find("tr")
    if first_tr:
        return [clean(c.get_text(" ")) for c in first_tr.find_all(["th", "td"])]
    return []


def parse_table(table) -> List[Dict[str, str]]:
    """Return a list of {header: cell_text} dicts; a link in the first cell is stored under '_href'."""
    headers = table_headers(table)
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        row: Dict[str, str] = {}
        for i, td in enumerate(tds):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = clean(td.get_text(" "))
        link = tds[0].find("a", href=True)
        if link:
            row["_href"] = link["href"]
        rows.append(row)
    return rows


def normalize_row(row: Dict[str, str], state: str, scrape_ts: str) -> ScratchRow:
    """Map the varying lottery.net column headers onto normalized fields."""
    lower_map = {clean(k).lower(): v for k, v in row.items() if not k.startswith("_")}

    def pick(*candidates: str, exclude: tuple = ()) -> Optional[str]:
        for c in candidates:
            if lower_map.get(c):
                return lower_map[c]
        for c in candidates:
            for key, val in lower_map.items():
                if c in key and val and not any(x in key for x in exclude):
                    return val
        return None

    game_name = pick("game name", "game", "scratch-off", "name", "title", exclude=("no.", "number", "#"))
    top_prize_txt = pick("top prize", "largest prize", "jackpot", exclude=("remaining", "left"))

    return ScratchRow(
        state=state,
        source="lottery_net",
        scrape_ts=scrape_ts,
        game_number=to_int_any(pick("game no.", "game number", "game #", "number", "no.")),
        game_name=game_name,
        price=to_float_money(pick("ticket price", "price", "cost")),
        overall_odds_1_in=to_float_odds(pick("odds of winning", "overall odds", "odds")),
        top_prize_text=top_prize_txt,
        top_prize_amount=to_float_money(top_prize_txt),
        top_prizes_remaining=to_int_any(pick("top prizes remaining", "remaining top prizes", "top remaining")),
        detail_url=row.get("_href"),
    )


def scrape_game_details(url: str) -> List[Dict]:
    """Scrape the prize-tier table from a game detail page.

    Returns [{'tier', 'prize_text', 'prize', 'is_ticket', 'quantity', 'claimed', 'remaining'}].
    'prize' is None when the prize has no single cash value; 'quantity'/'claimed' are None when not published.
    """
    log(f"  Fetching details: {url}")
    try:
        soup = BeautifulSoup(fetch_html(url), "lxml")
    except Exception as e:
        log(f"  ERROR fetching {url}: {e}")
        return []

    target_table = None
    for tbl in soup.find_all("table"):
        if tbl.find("td", attrs={"data-title": "Prize"}) or tbl.find("th", string=re.compile("Prize", re.I)):
            target_table = tbl
            break
    if target_table is None:
        log(f"  WARNING: No prize table found for {url}")
        return []

    def cell(tr, title):
        td = tr.find("td", attrs={"data-title": title})
        return clean(td.get_text(" ")) if td else None

    tiers = []
    for tr in target_table.find_all("tr"):
        prize_text = cell(tr, "Prize")
        remaining = to_int_any(cell(tr, "Remaining"))
        if prize_text is None or remaining is None:
            continue
        tiers.append({
            "tier": cell(tr, "Tier"),
            "prize_text": prize_text,
            "prize": to_float_money(prize_text),
            "is_ticket": is_ticket_prize(prize_text),
            "quantity": to_int_any(cell(tr, "Quantity")),
            "claimed": to_int_any(cell(tr, "Claimed")),
            "remaining": remaining,
        })
    return tiers


def apply_prize_metrics(row: ScratchRow) -> None:
    """Fill tier-derived fields. Free-ticket prizes are valued at the ticket price."""
    tiers = row.prize_data
    if not tiers:
        return

    # lottery.net omits top-prize columns for some states; tier 1 of the detail page carries the same info.
    first = tiers[0]
    if row.top_prize_text is None:
        row.top_prize_text = first["prize_text"]
        row.top_prize_amount = first["prize"]
    if row.top_prizes_remaining is None:
        row.top_prizes_remaining = first["remaining"]

    row.tracked_prizes_remaining = sum(t["remaining"] for t in tiers)
    if all(t["quantity"] is not None for t in tiers):
        row.tracked_prizes_total = sum(t["quantity"] for t in tiers)

    value_remaining = value_total = 0.0
    for t in tiers:
        value = row.price if t["is_ticket"] else t["prize"]
        if value is None or t["quantity"] is None:
            continue
        value_remaining += value * t["remaining"]
        value_total += value * t["quantity"]
    if value_total > 0:
        row.prize_pool_remaining_pct = round(100 * value_remaining / value_total, 2)


def scrape_lottery_net(state_abbr: str = "NJ", fetch_details: bool = True) -> List[ScratchRow]:
    state_abbr = state_abbr.upper()
    slug = STATE_SLUGS.get(state_abbr)
    if not slug:
        raise ValueError(f"State {state_abbr} not supported. Available: {list(STATE_SLUGS)}")

    url = f"{BASE_URL}/{slug}/{LIST_PATHS.get(state_abbr, 'scratch-offs')}"
    log(f"Scraping main list: {url}")
    soup = BeautifulSoup(fetch_html(url), "lxml")
    scrape_ts = now_iso()

    rows: List[ScratchRow] = []
    for tbl in soup.find_all("table"):
        headers = [h.lower() for h in table_headers(tbl)]
        if not (any("odds" in h for h in headers) and any(k in h for h in headers for k in ("game", "prize", "price"))):
            continue
        for raw in parse_table(tbl):
            norm = normalize_row(raw, state_abbr, scrape_ts)
            if norm.game_name or norm.overall_odds_1_in or norm.price:
                rows.append(norm)

    seen = set()
    unique_rows: List[ScratchRow] = []
    for r in rows:
        key = r.game_number or (r.game_name or "", r.price or -1.0, r.top_prize_text or "")
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    log(f"Found {len(unique_rows)} games.")
    if not fetch_details:
        return unique_rows

    consecutive_failures = 0
    for r in unique_rows:
        if not r.detail_url:
            continue
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            log("  WARNING: Stopping detail scrape after repeated failures (likely blocked).")
            break
        r.prize_data = scrape_game_details(r.detail_url)
        if r.prize_data:
            consecutive_failures = 0
            apply_prize_metrics(r)
        else:
            consecutive_failures += 1
        time.sleep(DETAIL_DELAY_SECONDS)

    return unique_rows


# ---------- I/O ----------
def rows_to_json(rows: List[ScratchRow]) -> str:
    return json.dumps([asdict(r) for r in rows], indent=2)


def rows_to_dataframe(rows: List[ScratchRow]) -> pd.DataFrame:
    """Convert rows to a DataFrame; prize_data is serialized as a JSON string so it round-trips through CSV."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([asdict(r) for r in rows])
    df["prize_data"] = df["prize_data"].apply(json.dumps)
    numeric = [
        "game_number", "price", "overall_odds_1_in", "top_prize_amount", "top_prizes_remaining",
        "tracked_prizes_remaining", "tracked_prizes_total", "prize_pool_remaining_pct",
    ]
    for c in numeric:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def rows_to_csv(rows: List[ScratchRow], path: str) -> None:
    if rows:
        rows_to_dataframe(rows).to_csv(path, index=False)


def scrape_lottery_net_df(state_abbr: str = "NJ") -> pd.DataFrame:
    return rows_to_dataframe(scrape_lottery_net(state_abbr))


def main():
    ap = argparse.ArgumentParser(description="Scrape scratch-off data from lottery.net")
    ap.add_argument("--state", default="NJ", help=f"State abbreviation ({', '.join(STATE_SLUGS)})")
    ap.add_argument("--csv", default=None, help="CSV output path (default: scratchoffs_<STATE>.csv)")
    ap.add_argument("--json", action="store_true", help="also print the normalized rows as JSON to stdout")
    ap.add_argument("--no-details", action="store_true", help="skip per-game detail pages (faster, no tier data)")
    args = ap.parse_args()

    state = args.state.upper()
    rows = scrape_lottery_net(state, fetch_details=not args.no_details)
    if args.json:
        print(rows_to_json(rows))
    path = args.csv or f"scratchoffs_{state}.csv"
    rows_to_csv(rows, path)
    log(f"Wrote {len(rows)} rows to {path}")


if __name__ == "__main__":
    main()
