"""Scrape scratch-off games and prize tiers from scratchcards.net.

Usable as a module (``scrape_state_df``) or as a CLI:

    python scrape.py --state NJ            # writes data/scratchoffs_NJ.csv
    python scrape.py --all                 # every supported state
"""
import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://scratchcards.net"
DATA_DIR = Path(__file__).parent / "data"

# abbr: (scratchcards.net slug, display name, official lottery domain)
STATES = {
    "AZ": ("arizona", "Arizona", "arizonalottery.com"),
    "AR": ("arkansas", "Arkansas", "myarkansaslottery.com"),
    "CA": ("california", "California", "calottery.com"),
    "CO": ("colorado", "Colorado", "coloradolottery.com"),
    "CT": ("connecticut", "Connecticut", "ctlottery.org"),
    "DE": ("delaware", "Delaware", "delottery.com"),
    "DC": ("washington-dc", "Washington DC", "dclottery.com"),
    "FL": ("florida", "Florida", "floridalottery.com"),
    "GA": ("georgia", "Georgia", "galottery.com"),
    "ID": ("idaho", "Idaho", "idaholottery.com"),
    "IL": ("illinois", "Illinois", "illinoislottery.com"),
    "IN": ("indiana", "Indiana", "hoosierlottery.com"),
    "IA": ("iowa", "Iowa", "ialottery.com"),
    "KS": ("kansas", "Kansas", "kslottery.com"),
    "KY": ("kentucky", "Kentucky", "kylottery.com"),
    "LA": ("louisiana", "Louisiana", "louisianalottery.com"),
    "ME": ("maine", "Maine", "mainelottery.com"),
    "MD": ("maryland", "Maryland", "mdlottery.com"),
    "MA": ("massachusetts", "Massachusetts", "masslottery.com"),
    "MI": ("michigan", "Michigan", "michiganlottery.com"),
    "MN": ("minnesota", "Minnesota", "mnlottery.com"),
    "MS": ("mississippi", "Mississippi", "mslotteryhome.com"),
    "MO": ("missouri", "Missouri", "molottery.com"),
    "NE": ("nebraska", "Nebraska", "nelottery.com"),
    "NH": ("new-hampshire", "New Hampshire", "nhlottery.com"),
    "NJ": ("new-jersey", "New Jersey", "njlottery.com"),
    "NM": ("new-mexico", "New Mexico", "nmlottery.com"),
    "NY": ("new-york", "New York", "nylottery.ny.gov"),
    "NC": ("north-carolina", "North Carolina", "nclottery.com"),
    "OH": ("ohio", "Ohio", "ohiolottery.com"),
    "OK": ("oklahoma", "Oklahoma", "lottery.ok.gov"),
    "OR": ("oregon", "Oregon", "oregonlottery.org"),
    "PA": ("pennsylvania", "Pennsylvania", "palottery.pa.gov"),
    "RI": ("rhode-island", "Rhode Island", "rilot.com"),
    "SC": ("south-carolina", "South Carolina", "sceducationlottery.com"),
    "SD": ("south-dakota", "South Dakota", "lottery.sd.gov"),
    "TX": ("texas", "Texas", "texaslottery.com"),
    "VA": ("virginia", "Virginia", "valottery.com"),
    "WA": ("washington", "Washington", "walottery.com"),
}
STATE_NAMES = {abbr: v[1] for abbr, v in STATES.items()}

HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_DELAY_SECONDS = 1.5
WORKERS = 1
RETRIES = 4
BLOCK_BACKOFF_SECONDS = 90

# ---------- Parsing helpers ----------
MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*([KMB])?\b", re.I)
ODDS_RE = re.compile(r"1\s*in\s*([\d,]*\.?\d+)", re.I)
INT_RE = re.compile(r"\d[\d,]*")
# Prizes paid over time ("$1K/WK/LIFE", "$500 A Week For Life") have no single cash value.
ANNUITY_RE = re.compile(r"/|\blife\b|\bweek\b|\bwk\b|\bmonth\b|\bmo\b|\byear\b|\byr\b", re.I)
SUFFIX_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


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
    if not m:
        return None
    return float(m.group(1).replace(",", "")) * SUFFIX_MULTIPLIERS.get((m.group(2) or "").upper(), 1)


def to_float_odds(s: Optional[str]) -> Optional[float]:
    m = ODDS_RE.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def to_int_any(s: Optional[str]) -> Optional[int]:
    m = INT_RE.search(s or "")
    return int(m.group().replace(",", "")) if m else None


# ---------- Data model ----------
@dataclass
class ScratchRow:
    state: str
    scrape_ts: str
    source_updated: Optional[str]
    game_id: int
    game_number: Optional[int]
    game_name: Optional[str]
    price: Optional[float]
    overall_odds_1_in: Optional[float]
    top_prize_text: Optional[str]
    top_prize_amount: Optional[float]
    top_prizes_remaining: Optional[int]
    detail_url: str
    prize_data: List[Dict] = field(default_factory=list)
    # Calculated from the prize tiers listed on the detail page.
    tracked_prizes_remaining: Optional[int] = None
    tracked_prizes_total: Optional[int] = None
    prize_pool_remaining_pct: Optional[float] = None
    est_tickets_printed: Optional[int] = None


# ---------- Fetching ----------
_session = requests.Session()
_session.headers.update(HDRS)


def fetch_html(url: str) -> str:
    for attempt in range(RETRIES):
        try:
            r = _session.get(url, timeout=30)
            if r.status_code in (403, 429, 503) and attempt < RETRIES - 1:
                wait = BLOCK_BACKOFF_SECONDS * (attempt + 1)
                log(f"  rate limited ({r.status_code}); waiting {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}")


def list_game_urls(slug: str) -> List[str]:
    soup = BeautifulSoup(fetch_html(f"{BASE_URL}/{slug}/current-games"), "lxml")
    pattern = re.compile(rf"^(?:{re.escape(BASE_URL)})?/{re.escape(slug)}/(\d+)/?$")
    urls, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = pattern.match(a["href"])
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            urls.append(f"{BASE_URL}/{slug}/{m.group(1)}")
    return urls


def parse_tiers(soup) -> List[Dict]:
    table = None
    for tbl in soup.find_all("table"):
        if tbl.find("td", attrs={"data-title": "Prize"}):
            table = tbl
            break
    if table is None:
        return []

    def cell(tr, title):
        td = tr.find("td", attrs={"data-title": title})
        return clean(td.get_text(" ")) if td else None

    tiers = []
    for tr in table.find_all("tr"):
        prize_text, remaining = cell(tr, "Prize"), to_int_any(cell(tr, "Remaining"))
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


def parse_game(html: str, url: str, state: str, scrape_ts: str) -> ScratchRow:
    soup = BeautifulSoup(html, "lxml")
    boxes = {}
    for box in soup.select(".content-box"):
        label, value = box.select_one(".text"), box.select_one(".title")
        if label and value:
            boxes[clean(label.get_text()).lower()] = clean(value.get_text(" "))

    name, number, updated = None, None, None
    h1 = soup.find("h1")
    if h1:
        spans = h1.find_all("span")
        name = clean("".join(t for t in h1.find_all(string=True, recursive=False)))
        for sp in spans:
            text = clean(sp.get_text())
            if text.startswith("#"):
                number = to_int_any(text)
            elif "updated" in text.lower():
                updated = re.sub(r"(?i)^data last updated on\s*", "", text)

    top_text = boxes.get("biggest prize") or boxes.get("top prize")
    row = ScratchRow(
        state=state,
        scrape_ts=scrape_ts,
        source_updated=updated,
        game_id=int(url.rstrip("/").rsplit("/", 1)[1]),
        game_number=number,
        game_name=name or None,
        price=to_float_money(boxes.get("price")),
        overall_odds_1_in=to_float_odds(boxes.get("overall odds")),
        top_prize_text=top_text,
        top_prize_amount=to_float_money(top_text),
        top_prizes_remaining=None,
        detail_url=url,
        prize_data=parse_tiers(soup),
    )
    apply_prize_metrics(row)
    return row


def apply_prize_metrics(row: ScratchRow) -> None:
    """Fill tier-derived fields. Free-ticket prizes are valued at the ticket price."""
    tiers = row.prize_data
    if not tiers:
        return
    if row.top_prize_text is None:
        row.top_prize_text, row.top_prize_amount = tiers[0]["prize_text"], tiers[0]["prize"]
    row.top_prizes_remaining = tiers[0]["remaining"]
    row.tracked_prizes_remaining = sum(t["remaining"] for t in tiers)

    if not all(t["quantity"] is not None for t in tiers):
        return
    row.tracked_prizes_total = sum(t["quantity"] for t in tiers)
    if row.overall_odds_1_in:
        # Overall odds = tickets printed / total prizes, so the print run follows directly.
        row.est_tickets_printed = round(row.tracked_prizes_total * row.overall_odds_1_in)

    value_remaining = value_total = 0.0
    for t in tiers:
        value = row.price if t["is_ticket"] else t["prize"]
        if value is None:
            continue
        value_remaining += value * t["remaining"]
        value_total += value * t["quantity"]
    if value_total > 0:
        row.prize_pool_remaining_pct = round(100 * value_remaining / value_total, 2)


def scrape_state(state: str) -> List[ScratchRow]:
    state = state.upper()
    if state not in STATES:
        raise ValueError(f"State {state} not supported. Available: {', '.join(STATES)}")
    slug = STATES[state][0]
    urls = list_game_urls(slug)
    log(f"{state}: {len(urls)} games")
    scrape_ts = datetime.now(timezone.utc).isoformat()

    def work(url: str) -> Optional[ScratchRow]:
        try:
            row = parse_game(fetch_html(url), url, state, scrape_ts)
        except Exception as e:
            log(f"  {state}: failed {url}: {e}")
            row = None
        time.sleep(REQUEST_DELAY_SECONDS)
        return row

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = [r for r in pool.map(work, urls) if r is not None]
    return rows


# ---------- I/O ----------
def rows_to_dataframe(rows: List[ScratchRow]) -> pd.DataFrame:
    """prize_data is serialized as a JSON string so it round-trips through CSV."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([asdict(r) for r in rows])
    df["prize_data"] = df["prize_data"].apply(json.dumps)
    return df


def csv_path(state: str) -> Path:
    return DATA_DIR / f"scratchoffs_{state.upper()}.csv"


def scrape_state_df(state: str) -> pd.DataFrame:
    return rows_to_dataframe(scrape_state(state))


def save_state(state: str, path: Optional[Path] = None) -> int:
    df = scrape_state_df(state)
    if df.empty:
        log(f"{state}: no rows, not writing")
        return 0
    path = Path(path) if path else csv_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    log(f"{state}: wrote {len(df)} rows to {path}")
    return len(df)


def main():
    ap = argparse.ArgumentParser(description="Scrape scratch-off data from scratchcards.net")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--state", default="NJ", help=f"State abbreviation ({', '.join(STATES)})")
    group.add_argument("--all", action="store_true", help="scrape every supported state")
    ap.add_argument("--skip-existing", action="store_true", help="with --all, skip states that already have a CSV")
    ap.add_argument("--csv", default=None, help="output path for a single state (default: data/scratchoffs_<STATE>.csv)")
    args = ap.parse_args()

    if args.all:
        for abbr in STATES:
            if args.skip_existing and csv_path(abbr).exists():
                continue
            try:
                save_state(abbr)
            except Exception as e:
                log(f"{abbr}: failed: {e}")
    else:
        save_state(args.state, args.csv)


if __name__ == "__main__":
    main()
