# ... (imports remain the same, but I'll make sure to keep them)
import argparse
import csv
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Union

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

BASE_URL = "https://www.lottery.net"

HDRS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Referer": "https://www.lottery.net/",
}

# ---------- Helpers ----------
MONEY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{2})?)")
ODDS_RE = re.compile(r"1\s*in\s*([\d\.]+)", re.I)
INT_RE = re.compile(r"\d+")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def to_float_money(s: str) -> Optional[float]:
    if not s: return None
    if "ticket" in s.lower() or "free" in s.lower():
         return 0.0 # Placeholder, will be handled in EV calc if needed
    m = MONEY_RE.search(s)
    return float(m.group(1).replace(",", "")) if m else None

def to_float_odds(s: str) -> Optional[float]:
    if not s: return None
    m = ODDS_RE.search(s)
    return float(m.group(1)) if m else None

def to_int_any(s: str) -> Optional[int]:
    if not s: return None
    m = INT_RE.search(s)
    return int(m.group()) if m else None

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

# ---------- Data Model ----------
@dataclass
class ScratchRow:
    state: str
    source: str
    scrape_ts: str
    game_number: Optional[int]
    game_name: Optional[str]
    price: Optional[float]
    overall_odds_1_in: Optional[float]
    top_prize_amount: Optional[float]
    top_prizes_remaining: Optional[int]
    all_prizes_remaining: Optional[int]
    status: str
    detail_url: Optional[str] = None
    prize_data: List[Dict] = field(default_factory=list)
    true_ev: Optional[float] = None

# ---------- Core scraper ----------
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=HDRS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_table(table) -> List[Dict[str, Union[str, Optional[str]]]]:
    """Return list of dicts {header: cell_text, 'href': link} for a <table>."""
    headers = []
    # header row may be in <thead> or first <tr>
    thead = table.find("thead")
    if thead:
        ths = thead.find_all("th")
        headers = [clean(th.get_text(" ")) for th in ths]
    if not headers:
        first_tr = table.find("tr")
        if first_tr:
            headers = [clean(th.get_text(" ")) for th in first_tr.find_all(["th", "td"])]
    rows = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        
        # Extract text and potential link from the first cell (assumed Game Name)
        cells = []
        row_href = None
        for i, td in enumerate(tds):
            text = clean(td.get_text(" "))
            cells.append(text)
            if i == 0: # Check for link in the first column (Game Name)
                a_tag = td.find("a", href=True)
                if a_tag:
                    row_href = a_tag["href"]

        # align to headers by position
        row = {}
        for i, val in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = val
        
        if row_href:
            row["_href"] = row_href
            
        rows.append(row)
    return rows

def normalize_row(row: Dict[str, str], state: str) -> ScratchRow:
    """
    Map varying column headers into our normalized fields.
    """
    # Build a lowercase->value map for fuzzy matching
    lower_map = {clean(k).lower(): v for k, v in row.items()}

    def pick(*candidates):
        for c in candidates:
            if c in lower_map and lower_map[c]:
                return lower_map[c]
        # fuzzy partial
        for c in candidates:
            for key, val in lower_map.items():
                if c in key and val:
                    return val
        return None

    game_name = pick("game", "scratch-off", "ticket", "name", "title")
    price_txt = pick("ticket price", "price", "cost")
    odds_txt = pick("overall odds", "odds")
    top_prize_txt = pick("top prize", "largest prize", "jackpot")
    top_remaining_txt = pick("top prizes remaining", "remaining top prizes", "top remaining")
    all_remaining_txt = pick("prizes remaining", "total prizes remaining", "remaining prizes")
    game_number_txt = pick("game number", "number", "no.", "id", "game #")
    detail_url = row.get("_href")

    return ScratchRow(
        state=state,
        source="lottery_net",
        scrape_ts=now_iso(),
        game_number=to_int_any(game_number_txt or (game_name or "")),
        game_name=game_name,
        price=to_float_money(price_txt),
        overall_odds_1_in=to_float_odds(odds_txt),
        top_prize_amount=to_float_money(top_prize_txt),
        top_prizes_remaining=to_int_any(top_remaining_txt),
        all_prizes_remaining=to_int_any(all_remaining_txt),
        status="active",
        detail_url=detail_url
    )

def scrape_game_details(url: str) -> List[Dict]:
    """
    Visits the game detail page and scrapes the prize table.
    Returns a list of dicts: [{'prize': float, 'remaining': int, 'is_ticket': bool}]
    """
    print(f"  Fetching details: {url}")
    try:
        html = fetch_html(url)
        soup = BeautifulSoup(html, "lxml")
        
        # Look for the prize table. It often has "Tier", "Prize", "Remaining" headers.
        # Based on inspection: <td data-title="Prize"> and <td data-title="Remaining">
        
        prize_data = []
        
        # Find any table that contains prize info
        tables = soup.find_all("table")
        target_table = None
        
        for tbl in tables:
            # Check for specific headers or data attributes
            if tbl.find("td", attrs={"data-title": "Prize"}) or \
               tbl.find("th", string=re.compile("Prize", re.I)):
                target_table = tbl
                break
        
        if not target_table:
            print(f"  WARNING: No prize table found for {url}")
            return []

        # Parse rows
        rows = target_table.find_all("tr")
        for row in rows:
            # Use data-title attribute if available (more robust)
            prize_cell = row.find("td", attrs={"data-title": "Prize"})
            rem_cell = row.find("td", attrs={"data-title": "Remaining"})
            
            # Fallback to index if data-title is missing (skip header row)
            if not prize_cell or not rem_cell:
                tds = row.find_all("td")
                if len(tds) >= 3: # Assuming Tier, Prize, ..., Remaining structure
                    # Heuristics for column index could be added here if needed
                    # For now relying on data-title as primary method based on inspection
                    continue 

            if prize_cell and rem_cell:
                prize_text = clean(prize_cell.get_text())
                rem_text = clean(rem_cell.get_text())
                
                is_ticket = "ticket" in prize_text.lower() or "free" in prize_text.lower()
                prize_val = to_float_money(prize_text)
                rem_val = to_int_any(rem_text)
                
                if rem_val is not None:
                     prize_data.append({
                         "prize": prize_val if prize_val is not None else 0.0,
                         "remaining": rem_val,
                         "is_ticket": is_ticket
                     })
                     
        return prize_data

    except Exception as e:
        print(f"  ERROR scraping details for {url}: {e}")
        return []

def calculate_ev(row: ScratchRow) -> Optional[float]:
    """
    Calculate True EV = (Sum(Prize * Remaining) / Sum(Remaining))
    Note: 'Ticket' prizes are valued at the game price.
    """
    if not row.prize_data:
        return None
    
    total_remaining = 0
    total_value = 0.0
    
    for p in row.prize_data:
        rem = p["remaining"]
        val = p["prize"]
        if p["is_ticket"] and row.price:
            val = row.price
        
        total_remaining += rem
        total_value += (val * rem)
        
    if total_remaining == 0:
        return 0.0
        
    return total_value / total_remaining

def scrape_lottery_net(state_abbr: str = "NJ") -> Union[List[ScratchRow], pd.DataFrame]:
    slug = STATE_SLUGS.get(state_abbr.upper())
    if not slug:
        raise ValueError(f"State {state_abbr} not supported. Available: {list(STATE_SLUGS.keys())}")
    
    url = f"{BASE_URL}/{slug}/scratch-offs"
    print(f"Scraping main list: {url}")
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    # Prefer tables with clear headers; fallback to any table with 'Odds' column
    candidate_tables = []
    for tbl in soup.find_all("table"):
        headers = [clean(h.get_text(" ")).lower() for h in tbl.find_all("th")]
        if not headers:
            # try first row cells as headers
            first_tr = tbl.find("tr")
            if first_tr:
                headers = [clean(h.get_text(" ")).lower() for h in first_tr.find_all(["th", "td"])]
        if any("odds" in h for h in headers) and any(("game" in h or "prize" in h or "price" in h) for h in headers):
            candidate_tables.append(tbl)

    rows: List[ScratchRow] = []
    for tbl in candidate_tables:
        raw_rows = parse_table(tbl)
        for raw in raw_rows:
            norm = normalize_row(raw, state_abbr)
            # must at least have a name or odds/price to be meaningful
            if norm.game_name or norm.overall_odds_1_in or norm.price:
                # Deduplication check inside the loop to avoid scraping details for dupes?
                # For simplicity, we scrape all then dedup, OR better: dedup first then scrape details.
                rows.append(norm)

    # Deduplicate by (game_name, price, top_prize_amount) heuristic
    seen = set()
    unique_rows: List[ScratchRow] = []
    for r in rows:
        key = (r.game_name or "", r.price or -1.0, r.top_prize_amount or -1.0)
        if key in seen:
            continue
        seen.add(key)
        unique_rows.append(r)
    
    print(f"Found {len(unique_rows)} games. Fetching details...")
    
    # Fetch details and calculate EV
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3
    
    print(f"Found {len(unique_rows)} games. Fetching details...")

    for r in unique_rows:
        # If we've hit too many errors (e.g. bulk 403s), skip remaining details to save time
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            # Optional: warn once
            if consecutive_failures == MAX_CONSECUTIVE_FAILURES:
                print("  WARNING: Stopping detailed scrape due to repeated failures (likely blocked).")
                consecutive_failures += 1 # increment to avoid printing again
            continue

        if r.detail_url:
            data = scrape_game_details(r.detail_url)
            if not data:
                consecutive_failures += 1
            else:
                consecutive_failures = 0
                r.prize_data = data
                r.true_ev = calculate_ev(r)
            
            time.sleep(1) # Be polite and avoid 403s
        
    return unique_rows

# ---------- I/O ----------
def rows_to_json(rows: List[ScratchRow]) -> str:
    return json.dumps([asdict(r) for r in rows], indent=2)

def rows_to_csv(rows: List[ScratchRow], path: str) -> None:
    if not rows: return
    fields = list(asdict(rows[0]).keys()) 
    # exclude prize_data from CSV if it's too nested, or leave it stringified
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))

def rows_to_dataframe(rows: List[ScratchRow]) -> pd.DataFrame:
    """Convert list of ScratchRow objects to pandas DataFrame."""
    if not rows:
        return pd.DataFrame()
    
    # Convert to list of dictionaries
    data = [asdict(row) for row in rows]
    df = pd.DataFrame(data)
    
    # Ensure proper data types
    df['game_number'] = pd.to_numeric(df['game_number'], errors='coerce')
    df['price'] = pd.to_numeric(df['price'], errors='coerce')
    df['overall_odds_1_in'] = pd.to_numeric(df['overall_odds_1_in'], errors='coerce')
    df['top_prize_amount'] = pd.to_numeric(df['top_prize_amount'], errors='coerce')
    df['top_prizes_remaining'] = pd.to_numeric(df['top_prizes_remaining'], errors='coerce')
    df['all_prizes_remaining'] = pd.to_numeric(df['all_prizes_remaining'], errors='coerce')
    df['true_ev'] = pd.to_numeric(df['true_ev'], errors='coerce')
    
    return df

def scrape_lottery_net_df(state_abbr: str = "NJ") -> pd.DataFrame:
    """Scrape lottery data and return as pandas DataFrame."""
    rows = scrape_lottery_net(state_abbr)
    return rows_to_dataframe(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", help="State abbreviation (e.g. NJ, NY, TX)", default="NJ")
    ap.add_argument("--csv", help="write normalized CSV to this path", default="lottery_net_scratchoffs.csv")
    ap.add_argument("--dataframe", action="store_true", help="return DataFrame instead of JSON")
    args = ap.parse_args()

    if args.dataframe:
        # Return DataFrame for use in other scripts
        df = scrape_lottery_net_df(args.state)
        print(f"DataFrame shape: {df.shape}")
        print(f"Columns: {list(df.columns)}")
        print(f"First few rows:")
        print(df.head())
        # return df # Removed return as main() shouldn't return in script mode
    else:
        # Original behavior - JSON output and CSV save
        rows = scrape_lottery_net(args.state)
        print(rows_to_json(rows))
        # Always write CSV (to provided path or default)
        rows_to_csv(rows, args.csv)
        print(f"Wrote CSV to {args.csv} with {len(rows)} rows")

if __name__ == "__main__":
    main()
