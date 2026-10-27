# 🎟️ Lottery Luck

A Streamlit app for comparing scratch-off tickets across US states, using data from [scratchcards.net](https://scratchcards.net).

## What you can do

- **Pick a state on the map**, or from the dropdown. States are shaded by how much prize value is left.
- **Browse game cards**, each with odds, a gauge for prize value left, and your odds of winning $1K or more.
- **Filter by budget** (max ticket price), search by name or game number, and sort by value, odds, or top prize.
- **Open any game** for the full picture: your odds of winning big ($100+, $1K+, $10K+, top prize), prizes left in each tier, and links to the official state lottery and the source data.
- **Compare up to 4 games** side by side.
- **Share any view.** The address bar always holds the current state, filters, comparison and open game.

## Metrics

| Metric | Type | Definition |
| --- | --- | --- |
| Overall odds | Published | Odds of winning any prize ("1 in X"). |
| Top prizes left | Published | Remaining count of the top prize tier. |
| Prize value left | Calculated | `Σ(prize × remaining) / Σ(prize × printed)` across listed tiers. Free tickets count at face value; annuity prizes are left out. |
| Tickets printed | Calculated | `total prizes printed × overall odds`. |
| Odds of winning $X+ | Calculated | `tickets printed / prizes worth at least $X printed`. This covers the full print run, not just the tickets still unsold. |

Calculated metrics need printed prize counts, and some states (for example, PA) don't publish them. No true expected value is shown, because no source publishes how many tickets are unsold.

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app reads saved snapshots from `data/scratchoffs_<STATE>.csv`. If a state has no snapshot, you can fetch it from the app.

## Updating the data

```bash
python scrape.py --state NJ                 # one state
python scrape.py --all --skip-existing      # fill in missing states
python scrape.py --all                      # refresh everything
```

The scraper sends one request at a time, pauses between requests, and waits it out when the site rate-limits it. A full refresh takes a while.

## Project layout

- `app.py`: Streamlit UI (map, cards, details, compare, share links).
- `scrape.py`: scraping, parsing, and the tier-based metrics.
- `theme.css` and `.streamlit/config.toml`: styling.
- `data/`: saved snapshots for each state.

## Disclaimer

The data comes from a third-party aggregator and may lag official state lottery reports. This is an informational tool, not financial advice. On average, scratch-offs lose money. Need help? Call 1-800-GAMBLER.
