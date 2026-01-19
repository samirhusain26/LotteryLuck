# Lottery Luck Dashboard

A Streamlit application that scrapes and analyzes **Multi-State Lottery scratch-off** data from [lottery.net](https://www.lottery.net). It provides a clean, sortable interface to help you find the best games based on odds and expected value.

## Features

- **Live Data Scraping**: Fetches up-to-date scratch-off data directly from lottery.net for **NJ, NY, PA, TX, CA, and FL**.
- **Interactive Dashboard**: Organized into three main sections:
    - **Game Browser**: Sortable table with "Dead Game" highlighting (games with 0 top prizes remaining shown in red).
    - **Analytics**: Visual scatter plot comparing Ticket Price vs. True EV.
    - **User Guide**: Built-in documentation explaining metrics and strategies.
- **Metrics Analysis**: Automatically calculates key metrics for each game:
    - **True Expected Value (EV)** per ticket (calculated from remaining prizes).
    - **Win Probability**: Probability of winning any prize.
- **Deep Scraping**: Visits individual game pages to scrape full prize tables for accurate EV calculation.
- **Smart Filtering**:
    - Filter by Ticket Price ($1, $2, $5, $10, etc).
    - Visual indicators for "Dead Games".
- **CSV Export**: Data is automatically saved to CSV when scraped.
- **Mobile-Friendly UI**: Responsive layout built with Streamlit.

## Technology Stack

- **Python**: Core programming language.
- **Streamlit**: Web application framework for the interactive dashboard.
- **Pandas**: Data manipulation and analysis.
- **Plotly**: Interactive charts and visualizations.
- **Requests & BeautifulSoup4**: Robust web scraping and HTML parsing.

## Project Structure

- `app.py`: The main Streamlit web application. Handles UI, user inputs, and metric calculations.
- `scrape.py`: The scraping engine. Can be imported as a module or run as a standalone CLI script to save data to CSV.
- `theme.css`: Custom CSS for styling the Streamlit interface.
- `requirements.txt`: List of Python dependencies.

## Installation

1.  **Clone the repository** (if applicable) or navigate to the project folder.

2.  **Create a virtual environment** (recommended):
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

### Running the Web App
To start the interactive dashboard:
```bash
streamlit run app.py
```
The app will open in your default web browser at `http://localhost:8501`.

### Running the Scraper (CLI)
You can also run the scraper directly from the command line to generate a CSV dataset without starting the web app. You must specify the state abbreviation (default is NJ).

```bash
# Default (New Jersey)
python scrape.py --csv nj_data.csv

# New York
python scrape.py --state NY --csv ny_data.csv

# Texas
python scrape.py --state TX --csv tx_data.csv
```

## Data Metrics Explained

- **Overall Odds**: The published odds of winning *any* prize (e.g., 1 in 4.5). Lower is better.
- **Win Probability**: The percentage probability of winning a prize on a single ticket (1 / Overall Odds).
- **True EV**: The estimated return on a $1 spend, calculated by summing `(Prize Value * Remaining Count)` for all prize tiers and dividing by the total remaining tickets. This is significantly more accurate than using just the top prize.

## Notes

- **Disclaimer**: This app scrapes data from a third-party aggregator (lottery.net). Data accuracy depends on the source.
- **Status**: The scraper normalizes data from the source, handling various column naming conventions automatically.