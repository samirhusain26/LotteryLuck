# 🍀 Lottery Luck

A Streamlit dashboard that scrapes scratch-off data from [lottery.net](https://www.lottery.net) and its [scratchcards.net](https://scratchcards.net) detail pages. It puts published odds next to how much of each game's prize pool is still unclaimed, so you can skip games whose big prizes are already gone.

Supported states: **NJ, NY, PA, TX, CA, FL**.

## Features

- **Overview cards** for games shown, games that still have a top prize, best published odds, and the most prize value left.
- **Games table** you can sort and filter by price, name, or game number. You can hide games with no top prizes left and download the results as CSV.
- **Explore charts** comparing win chance with prize value left, plus average odds at each price point.
- **Game detail** for each game: its tiers, how many prizes were printed, claimed, and remain, and a link to the source page.
- **How it works** tab that labels every metric as *published* (from the source) or *calculated* (our estimate).

## Metrics

| Metric | Type | Definition |
| --- | --- | --- |
| Overall odds | Published | Odds of winning any prize ("1 in X"). |
| Win chance | Published | `1 / overall odds`, shown as a percentage. |
| Top prize / top prizes left | Published | From the lottery.net list, or from tier 1 of the detail page when the list leaves them out (e.g. PA). Annuity prizes like `$1K/WK/LIFE` stay as text. |
| Prizes left | Calculated | Remaining prizes summed across the tiers the source tracks. |
| Prize value left | Calculated | `Σ(prize × remaining) / Σ(prize × printed)` across tracked tiers. Free-ticket prizes count at the ticket price, and annuity prizes are left out. It's blank when printed quantities aren't published. |

The dashboard intentionally **does not report an expected value**. A real EV needs the number of unsold tickets, and no source publishes it.

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app reads `scratchoffs_<STATE>.csv` from the project folder. If a state has no CSV yet, the app scrapes it on first load. You can also click **Refresh from lottery.net** in the sidebar, which takes about a second per game.

## Scraper CLI

`scrape.py` also works on its own:

```bash
python scrape.py --state NJ                 # writes scratchoffs_NJ.csv
python scrape.py --state TX --csv tx.csv    # custom output path
python scrape.py --state NY --no-details    # list page only, no tier data
python scrape.py --state PA --json          # also print rows as JSON
```

Progress messages go to stderr. In the CSV, `prize_data` is a JSON array of tiers: `tier`, `prize_text`, `prize`, `is_ticket`, `quantity`, `claimed`, `remaining`.

## Project layout

- `app.py`: Streamlit UI and display-only derived fields.
- `scrape.py`: scraping, normalization, and the tier-based metrics.
- `theme.css` and `.streamlit/config.toml`: theme and styling.
- `scratchoffs_*.csv`: saved snapshots for each state.

## Disclaimer

Data comes from third-party aggregators and may lag official state lottery reports. This is an informational tool, not financial advice. On average, scratch-offs lose money, so play responsibly. Need help? Call 1-800-GAMBLER.
