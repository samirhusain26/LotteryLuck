# LotteryLuck Agent Guide

LotteryLuck is a Python and Streamlit dashboard that scrapes scratch-off data and calculates probability and expected-value metrics.

## Working rules

- Read `README.md` before changing scraping, normalization, or metric behavior.
- Keep scraping separate from presentation so `scrape.py` remains usable as a CLI.
- Preserve source uncertainty and clearly distinguish published odds from calculated estimates.
- Use deterministic numeric calculations and validate source-column variations rather than silently fabricating missing data.
- Do not present the dashboard as financial advice or claim accuracy beyond the upstream data.

## Verify

- Run the scraper against a small sample or saved fixture when practical.
- Start the Streamlit app for a focused smoke test after UI changes.
