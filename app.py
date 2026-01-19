import os
import pandas as pd
import streamlit as st
import plotly.express as px
from datetime import datetime
from zoneinfo import ZoneInfo
import math

from scrape import scrape_lottery_net_df, STATE_SLUGS

st.set_page_config(page_title="Lottery Luck Dashboard", page_icon="🍀", layout="wide")

# --- Custom CSS Import ---
with open("theme.css") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# --- Helper Functions ---
def format_large_number(num):
    """Formats a number with K, M, B suffixes."""
    if num is None or math.isnan(num):
        return "N/A"
    num = float(num)
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return f"{num:.0f}"

def format_currency(num):
    """Formats a number as currency with K, M, B suffixes if large."""
    if num is None or pd.isna(num) or (isinstance(num, float) and math.isnan(num)):
        return "Unknown"
    if num >= 1000:
        return f"${format_large_number(num)}"
    return f"${num:.0f}"

def format_percent(num):
    """Formats a float as a percentage with 1 decimal place."""
    if num is None or math.isnan(num):
        return "0%"
    return f"{num:.1f}%"

# --- Session State for User Guide ---
if "first_visit" not in st.session_state:
    st.session_state["first_visit"] = True
if "show_guide" not in st.session_state:
    st.session_state["show_guide"] = False

# --- Dialog Function for User Guide ---
@st.dialog("User Guide 📚")
def user_guide_dialog():
    st.markdown("""
    ### Welcome to Lottery Luck! 🍀
    
    Make data-driven decisions when playing scratch-offs.
    
    ---
    #### 1. Key Metrics 📊
    *   **True EV (Expected Value)**: The theoretical worth of a ticket based on remaining prizes. **Higher is better.**
    *   **Win Probability**: Your math chance of winning *any* prize.
    *   **Saturation Index**: > 1.0 means the game is "rich" with prizes. < 1.0 means it's been picked clean.

    #### 2. Strategy 🧠
    *   ✅ **Look for High EV**: Get the best bang for your buck.
    *   🛑 **Avoid Dead Games**: Rows highlighted in **RED** have **0 Top Prizes** left. Don't chase a jackpot that doesn't exist!

    ---
    > **Note**: Data is scraped from public sources. Always double-check with official state lottery sites.
    """)
    if st.button("Got it! Let's Play"):
        st.session_state["first_visit"] = False
        st.rerun()

# --- Auto-show User Guide on First Visit ---
if st.session_state["first_visit"]:
    user_guide_dialog()

# --- Header & Controls Layout ---
# Use columns to layout the top controls: Title, Region Selector, Refresh, Help
col_title, col_state_select, col_refresh, col_help = st.columns([4, 2, 1, 1], vertical_alignment="bottom")

with col_title:
    st.title("Lottery Luck 🍀")

with col_state_select:
    selected_state = st.selectbox(
        "Region",
        options=list(STATE_SLUGS.keys()),
        index=0,
        label_visibility="visible"
    )

DATA_FILE = f"scratchoffs_{selected_state}.csv"

with col_refresh:
    if st.button("🔄 Refresh", help="Force scrape fresh data"):
        with st.spinner(f"Scraping {selected_state}..."):
            st.cache_data.clear()
            fresh_df = scrape_lottery_net_df(selected_state)
            if not fresh_df.empty:
                fresh_df.to_csv(DATA_FILE, index=False)
            st.success("Updated!")
            st.rerun()

with col_help:
    if st.button("❓ Guide"):
        user_guide_dialog()

# --- Data Loading Logic ---
@st.cache_data
def load_data_cached(state_abbr):
    """
    Loads data from CSV if exists, otherwise scrapes.
    Cached by Streamlit to prevent re-loading/re-scraping on every interaction.
    """
    filename = f"scratchoffs_{state_abbr}.csv"
    if os.path.exists(filename):
        try:
            return pd.read_csv(filename)
        except Exception:
            return pd.DataFrame()
    
    # If no file exists, scrape
    df = scrape_lottery_net_df(state_abbr)
    if not df.empty:
        df.to_csv(filename, index=False)
    return df

# Load data
df = load_data_cached(selected_state)

# Display Last Fetched Date
last_fetched_str = "Unknown"
if not df.empty and "scrape_ts" in df.columns:
    try:
        # Assuming scrape_ts is isoformat str
        ts_str = df["scrape_ts"].iloc[0] 
        # Convert to datetime to format nicely
        ts_dt = datetime.fromisoformat(ts_str)
        last_fetched_str = ts_dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        pass

st.caption(f"Last Fetched: **{last_fetched_str}**")


# --- Pre-processing / Calculations ---
if not df.empty:
    # Ensure relevant columns exist and are numeric
    cols_to_numeric = ["price", "overall_odds_1_in", "top_prize_amount", "top_prizes_remaining", "all_prizes_remaining", "true_ev"]
    for c in cols_to_numeric:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Win Probability (1 / odds) * 100 for percentage
    if "overall_odds_1_in" in df.columns:
        df["win_probability"] = (1 / df["overall_odds_1_in"] * 100).where(df["overall_odds_1_in"] > 0)
    
    # Is Dead?
    if "top_prizes_remaining" in df.columns:
        df["is_dead"] = df["top_prizes_remaining"] == 0
    elif "all_prizes_remaining" in df.columns:
        df["is_dead"] = df["all_prizes_remaining"] == 0
    else:
        df["is_dead"] = False

    # EV (Prefer true_ev, fallback to estimated)
    if "true_ev" not in df.columns:
        df["true_ev"] = None
    
    # Fallback EV calc for display if true_ev is missing
    df["estimated_ev"] = (df["top_prize_amount"] / df["overall_odds_1_in"]).where((df["overall_odds_1_in"] > 0))
    df["display_ev"] = df["true_ev"].fillna(df["estimated_ev"])


# --- Main Dashboard Layout ---

if df.empty:
    st.warning("No data available. Try refreshing.")
    st.stop()

# 1. Analytics Hero Section
st.markdown("### 📊 Market Overview")
col_chart, col_stats = st.columns([3, 1])

with col_chart:
    # Prepare data for plot
    plot_df = df.dropna(subset=["price", "display_ev"]).copy()
    if not plot_df.empty:
        # Add formatted columns for hover BEFORE filling NaNs for size
        # This ensures the tooltip shows "Unknown" but the size calc doesn't crash
        plot_df["formatted_jackpot"] = plot_df["top_prize_amount"].apply(format_currency)
        plot_df["formatted_ev"] = plot_df["display_ev"].apply(lambda x: f"${x:.2f}")
        plot_df["formatted_win"] = plot_df["win_probability"].apply(lambda x: f"{x:.1f}%")

        # Now fill NaN jackpots with 0 so the bubble size logic works (Value cannot be NaN for size)
        plot_df["top_prize_amount"] = plot_df["top_prize_amount"].fillna(0)

        fig = px.scatter(
            plot_df,
            x="price",
            y="display_ev",
            color="win_probability",
            size="top_prize_amount", # Bubble size based on jackpot
            hover_name="game_name",
            hover_data={
                "game_name": False, # Shown in title
                "price": ":$,.0f",
                "formatted_ev": True,
                "formatted_win": True,
                "formatted_jackpot": True,
                "display_ev": False,
                "win_probability": False,
                "top_prize_amount": False
            },
            labels={
                "price": "Ticket Price ($)",
                "formatted_ev": "True EV",
                "formatted_win": "Win Prob",
                "formatted_jackpot": "Jackpot"
            },
            title=None, # Clean look
            color_continuous_scale="Viridis",
            template="plotly_dark"
        )
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=10, r=10, t=10, b=10), # Tight margins
            height=450, # Fixed height to fit
            xaxis_title="Ticket Price ($)",
            yaxis_title="Expected Value ($)",
        )
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    else:
        st.info("Not enough data for chart.")

with col_stats:
    # Quick Stats Cards - Simplified, No Arrows
    best_ev_game = df.loc[df["display_ev"].idxmax()] if not df.empty else None
    best_odds_game = df.loc[df["win_probability"].idxmax()] if not df.empty else None
    
    # Card 1: Best Value
    st.markdown("#### 💎 Best Value")
    if best_ev_game is not None:
        st.metric(
            label=f"{best_ev_game['game_name']}",
            value=format_currency(best_ev_game['display_ev']),
            help="Ticket with the highest Expected Value (Theoretical return per ticket)"
        )
    
    st.divider()

    # Card 2: Best Odds
    st.markdown("#### 🎲 Best Odds")
    if best_odds_game is not None:
        st.metric(
            label=f"{best_odds_game['game_name']}",
            value=format_percent(best_odds_game['win_probability']),
            help="Ticket with the highest probability of winning any prize"
        )


# 2. Game Browser Section
st.divider()
st.markdown("### 🎫 Tickets")

# Filters
f_col1, f_col2 = st.columns([1, 1], vertical_alignment="center")
with f_col1:
    available_prices = sorted(df["price"].dropna().unique()) if "price" in df.columns else []
    price_filter = st.multiselect("Price ($)", available_prices, default=available_prices)

filtered_df = df.copy()
if price_filter and "price" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["price"].isin(price_filter)]

# Rename columns for display
display_map = {
    "game_name": "Game",
    "price": "Price",
    "top_prize_amount": "Top Prize",
    "overall_odds_1_in": "Odds (1 in)",
    "win_probability": "Win %",
    "display_ev": "EV",
    "top_prizes_remaining": "Top Rem.",
    "is_dead": "Dead"
}

# Simplify Dataframe
display_cols = [c for c in display_map.keys() if c in filtered_df.columns]
final_df = filtered_df[display_cols].rename(columns=display_map)

# Add Status Column using boolean
if "Dead" in final_df.columns:
    final_df["Status"] = final_df["Dead"].apply(lambda x: "🔴" if x else "🟢")
else:
    final_df["Status"] = "🟢"

# Reorder
final_cols = ["Status", "Game", "Price", "EV", "Top Prize", "Win %", "Odds (1 in)", "Top Rem."]
final_df = final_df[[c for c in final_cols if c in final_df.columns]]

# Create formatted versions of large numbers for a custom displayed dataframe if we wanted full control
# But st.dataframe column_config is better for sorting.
# We will use column_config format parameters, but standard printf "%d" doesn't do "1M".
# So we rely on standard numbers for the table to keep sorting working, but maybe use a tooltip or standard formatting.
# The user asked to "round of all the numbers used and ensure the numbers are formatted well, ifi is a lrge number it shows M, B K".
# st.column_config doesn't support 'format' functions, only printf strings.
# So to get K/M/B in the table we actually MUST convert to string. If we do that, sorting breaks.
# A compromise: showing them as strings, but maybe we can keep sorting if we use a different app approach (ag-grid), but for simple streamlit:
# We will convert to formatted strings for "Top Prize". For "Price" and "EV" standard currency is fine.

# Apply custom formatting to Top Prize for the table display
if "Top Prize" in final_df.columns:
    final_df["Top Prize"] = final_df["Top Prize"].apply(format_currency)

st.dataframe(
    final_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Price": st.column_config.NumberColumn(format="$%d"),
        "EV": st.column_config.NumberColumn(format="$%.2f"),
        "Win %": st.column_config.NumberColumn(format="%.1f%%"),
        "Odds (1 in)": st.column_config.NumberColumn(format="%.2f"),
        "Game": st.column_config.TextColumn(width="medium"),
        "Status": st.column_config.TextColumn(width="small"),
        "Top Prize": st.column_config.TextColumn(label="Top Prize"), # Treat as text now
    },
    height=600
)
