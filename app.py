import html
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from scrape import STATE_NAMES, STATE_SLUGS, scrape_lottery_net_df

APP_DIR = Path(__file__).parent
DISPLAY_TZ = ZoneInfo("America/New_York")

INK, MUTED, LINE, TRACK, GREEN, RED, GREY = "#111111", "#6b6b66", "#e3e1d9", "#ecebe4", "#1a7f4b", "#d1453b", "#bdbbb2"

EXPECTED_COLUMNS = [
    "game_number", "game_name", "price", "overall_odds_1_in", "top_prize_text", "top_prize_amount",
    "top_prizes_remaining", "detail_url", "prize_data", "tracked_prizes_remaining",
    "tracked_prizes_total", "prize_pool_remaining_pct", "scrape_ts",
]
NUMERIC_COLUMNS = [
    "game_number", "price", "overall_odds_1_in", "top_prize_amount", "top_prizes_remaining",
    "tracked_prizes_remaining", "tracked_prizes_total", "prize_pool_remaining_pct",
]

st.set_page_config(page_title="Lottery Luck", page_icon="🍀", layout="wide", initial_sidebar_state="collapsed")
st.markdown(f"<style>{(APP_DIR / 'theme.css').read_text()}</style>", unsafe_allow_html=True)


# ---------- Formatting ----------
def is_missing(x) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def compact_money(x) -> str:
    if is_missing(x):
        return "—"
    x = float(x)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= size:
            return f"${x / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"${x:,.0f}"


def fmt_price(x) -> str:
    return "—" if is_missing(x) else f"${x:g}"


def fmt_int(x) -> str:
    return "—" if is_missing(x) else f"{int(x):,}"


def esc(s) -> str:
    return "—" if is_missing(s) else html.escape(str(s))


# ---------- Data ----------
def csv_path(state: str) -> Path:
    return APP_DIR / f"scratchoffs_{state}.csv"


@st.cache_data(show_spinner=False)
def read_state_csv(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


def load_state(state: str) -> pd.DataFrame:
    path = csv_path(state)
    if not path.exists():
        with st.spinner(f"Fetching {STATE_NAMES[state]} for the first time…"):
            fresh = scrape_lottery_net_df(state)
        if fresh.empty:
            return fresh
        fresh.to_csv(path, index=False)
    return prepare(read_state_csv(str(path), os.path.getmtime(path)))


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in EXPECTED_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    for c in NUMERIC_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    odds = df["overall_odds_1_in"]
    df["win_pct"] = (100 / odds).where(odds > 0)
    df["top_prize_label"] = [
        compact_money(a) if not is_missing(a) else (t if isinstance(t, str) and t.strip() else "—")
        for a, t in zip(df["top_prize_amount"], df["top_prize_text"])
    ]
    df["status"] = df["top_prizes_remaining"].map(
        lambda n: "unknown" if is_missing(n) else ("dead" if n == 0 else "live")
    )
    return df


def parse_tiers(value) -> list:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        tiers = json.loads(value)
    except ValueError:
        return []
    return tiers if isinstance(tiers, list) else []


def scraped_at(df: pd.DataFrame):
    try:
        ts = datetime.fromisoformat(str(df["scrape_ts"].dropna().iloc[0]))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (IndexError, ValueError):
        return None


def style_fig(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        template="simple_white", height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Schibsted Grotesk, sans-serif", color=INK, size=12),
        margin=dict(l=8, r=8, t=8, b=8), showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor=LINE, font=dict(family="IBM Plex Mono", color=INK, size=12)),
    )
    axis = dict(gridcolor=TRACK, linecolor=LINE, tickcolor=LINE, zeroline=False, title_font=dict(color=MUTED, size=11),
                tickfont=dict(family="IBM Plex Mono", color=MUTED, size=11))
    fig.update_xaxes(**axis)
    fig.update_yaxes(showgrid=True, **axis)
    return fig


PLOT_CONFIG = {"displayModeBar": False}


# ---------- Header ----------
head_l, head_state, head_btn = st.columns([6, 2, 0.5], vertical_alignment="center")
with head_state:
    state = st.selectbox("State", list(STATE_SLUGS), format_func=lambda s: STATE_NAMES[s],
                         key="state", label_visibility="collapsed")

df = load_state(state)
if df.empty:
    st.error("Couldn't load data for this state. Try again later.")
    st.stop()

ts = scraped_at(df)
updated = ts.astimezone(DISPLAY_TZ).strftime("%b %-d, %Y") if ts else "unknown"
with head_l:
    st.markdown(f'<div class="wordmark"><b>Lottery Luck</b><span>updated {updated}</span></div>',
                unsafe_allow_html=True)
with head_btn:
    if st.button(":material/refresh:", help="Re-scrape this state (about a second per game)"):
        with st.spinner("Scraping…"):
            fresh = scrape_lottery_net_df(state)
        if not fresh.empty:
            fresh.to_csv(csv_path(state), index=False)
            st.cache_data.clear()
            st.rerun()


# ---------- KPIs ----------
live = int((df["status"] == "live").sum())
known = int((df["status"] != "unknown").sum())
best = df[df["status"] != "dead"].dropna(subset=["overall_odds_1_in"]).nsmallest(1, "overall_odds_1_in")
avg_left = df["prize_pool_remaining_pct"].mean()
kpis = [
    (f"{len(df)}", "games"),
    (f"{live}<small> / {known}</small>", "still have a top prize"),
    (f"1 in {best.iloc[0]['overall_odds_1_in']:.2f}" if not best.empty else "—",
     f"best odds · {esc(best.iloc[0]['game_name'])}" if not best.empty else "best odds"),
    (f"{avg_left:.0f}%" if not is_missing(avg_left) else "—", "avg prize value left"),
]
st.markdown('<div class="kpis">' + "".join(f'<div class="kpi"><div class="v">{v}</div><div class="l">{l}</div></div>'
                                          for v, l in kpis) + "</div>", unsafe_allow_html=True)


# ---------- Filters ----------
prices = sorted(df["price"].dropna().unique())
f1, f2, f3 = st.columns([4.6, 2.4, 2.4], vertical_alignment="center")
with f1:
    picked = st.pills("Price", prices, selection_mode="multi", format_func=fmt_price,
                      key=f"prices_{state}", label_visibility="collapsed")
with f2:
    query = st.text_input("Search", placeholder="Search games", label_visibility="collapsed")
with f3:
    hide_dead = st.toggle("Top prize left only", value=False)

view = df[df["price"].isin(picked)] if picked else df
if query:
    q = query.strip().lower()
    view = view[view["game_name"].fillna("").str.lower().str.contains(q, regex=False)
                | view["game_number"].map(lambda n: "" if is_missing(n) else str(int(n))).str.contains(q, regex=False)]
if hide_dead:
    view = view[view["status"] != "dead"]


# ---------- Charts ----------
c1, c2 = st.columns([2, 1], gap="medium")
with c1:
    st.markdown('<div class="h">Odds vs. prize value left</div>', unsafe_allow_html=True)
    plot = view.dropna(subset=["win_pct", "prize_pool_remaining_pct"])
    if plot.empty:
        plot = view.dropna(subset=["win_pct", "price"])
        y, y_title, y_suffix = "price", "Ticket price", ""
    else:
        y, y_title, y_suffix = "prize_pool_remaining_pct", "Prize value left (est.)", "%"
    fig = px.scatter(
        plot, x="win_pct", y=y, size="price", size_max=22, color="status", hover_name="game_name",
        color_discrete_map={"live": GREEN, "dead": RED, "unknown": GREY},
        custom_data=[plot["price"].map(fmt_price), "overall_odds_1_in", "top_prize_label"],
    )
    fig.update_traces(marker=dict(opacity=0.75, line=dict(width=1, color="white")),
                      hovertemplate="<b>%{hovertext}</b><br>%{customdata[0]} · 1 in %{customdata[1]:.2f}"
                                    "<br>Top prize %{customdata[2]}<extra></extra>")
    fig.update_xaxes(title="Win chance", ticksuffix="%")
    fig.update_yaxes(title=y_title, ticksuffix=y_suffix, tickprefix="$" if y == "price" else "")
    st.plotly_chart(style_fig(fig, 320), config=PLOT_CONFIG)

with c2:
    st.markdown('<div class="h">Top prizes</div>', unsafe_allow_html=True)
    counts = view["status"].value_counts()
    labels = {"live": "Still available", "dead": "All claimed", "unknown": "Unknown"}
    keys = [k for k in ("live", "dead", "unknown") if counts.get(k, 0)]
    fig = go.Figure(go.Pie(
        labels=[labels[k] for k in keys], values=[int(counts[k]) for k in keys], hole=0.78, sort=False,
        marker=dict(colors=[{"live": GREEN, "dead": RED, "unknown": GREY}[k] for k in keys], line=dict(color="white", width=2)),
        textinfo="none", hovertemplate="%{label}: %{value} games<extra></extra>",
    ))
    pct_live = 100 * counts.get("live", 0) / max(len(view), 1)
    fig.add_annotation(text=f"<b>{pct_live:.0f}%</b><br><span style='font-size:11px;color:{MUTED}'>available</span>",
                       showarrow=False, font=dict(family="IBM Plex Mono", size=26, color=INK))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.05, x=0.5, xanchor="center",
                                                   font=dict(color=MUTED, size=11)))
    style_fig(fig, 320).update_layout(showlegend=True)
    st.plotly_chart(fig, config=PLOT_CONFIG)


# ---------- Leaderboard ----------
sort_opts = {"Value left": ("prize_pool_remaining_pct", False), "Odds": ("overall_odds_1_in", True),
             "Price": ("price", True), "Top left": ("top_prizes_remaining", False)}
lh, ls = st.columns([1, 1], vertical_alignment="bottom")
with lh:
    st.markdown(f'<div class="h">Games <span class="muted" style="font-weight:400">{len(view)}</span></div>',
                unsafe_allow_html=True)
with ls:
    sort_by = st.segmented_control("Sort", list(sort_opts), default="Value left",
                                   key="sort", label_visibility="collapsed") or "Value left"
col, asc = sort_opts[sort_by]
ranked = view.sort_values([col, "overall_odds_1_in"], ascending=[asc, True], na_position="last")


def board_row(g) -> str:
    pct = g["prize_pool_remaining_pct"]
    bar = ('<span class="muted">—</span>' if is_missing(pct) else
           f'<div class="bar"><div class="t"><i style="width:{min(max(pct, 0), 100):.0f}%"></i></div>'
           f'<span class="p">{pct:.0f}%</span></div>')
    num = "" if is_missing(g["game_number"]) else f"<small>#{int(g['game_number'])}</small>"
    odds = "—" if is_missing(g["overall_odds_1_in"]) else f"1:{g['overall_odds_1_in']:.2f}"
    return (f'<div class="r"><span class="dot {g["status"]}"></span><span class="price">{fmt_price(g["price"])}</span>'
            f'<span class="name">{esc(g["game_name"])}{num}</span><span class="num">{esc(g["top_prize_label"])}</span>'
            f'<span class="num">{odds}</span>{bar}<span class="num">{fmt_int(g["top_prizes_remaining"])}</span></div>')


head = ('<div class="r head"><span></span><span>Price</span><span>Game</span><span class="num">Top prize</span>'
        '<span class="num">Odds</span><span>Prize value left</span><span class="num">Top left</span></div>')
rows = "".join(board_row(g) for _, g in ranked.iterrows()) or '<div class="empty">No games match.</div>'
st.markdown(f'<div class="board"><div class="board-scroll">{head}{rows}</div></div>', unsafe_allow_html=True)


# ---------- Game detail ----------
if not ranked.empty:
    st.markdown('<div class="h" style="margin-top:2rem">Prize tiers</div>', unsafe_allow_html=True)
    pick = st.selectbox(
        "Game", ranked.index.tolist(), key=f"game_{state}", label_visibility="collapsed",
        format_func=lambda i: f"{ranked.at[i, 'game_name']} · {fmt_price(ranked.at[i, 'price'])}",
    )
    g = ranked.loc[pick]
    tiers = pd.DataFrame(parse_tiers(g["prize_data"]))
    odds = "—" if is_missing(g["overall_odds_1_in"]) else f"1 in {g['overall_odds_1_in']:.2f}"
    pct = "—" if is_missing(g["prize_pool_remaining_pct"]) else f"{g['prize_pool_remaining_pct']:.0f}%"
    st.markdown(
        f'<div class="stats"><div>{odds}<span>odds</span></div><div>{esc(g["top_prize_label"])}<span>top prize</span></div>'
        f'<div>{fmt_int(g["top_prizes_remaining"])}<span>top prizes left</span></div>'
        f'<div>{fmt_int(g["tracked_prizes_remaining"])}<span>prizes left</span></div>'
        f'<div>{pct}<span>value left (est.)</span></div></div>',
        unsafe_allow_html=True,
    )
    if tiers.empty:
        st.caption("No tier data for this game.")
    else:
        for c in ("prize_text", "quantity", "remaining"):
            if c not in tiers.columns:
                tiers[c] = pd.NA
        labels = tiers["prize_text"].fillna("?")
        fig = go.Figure()
        if tiers["quantity"].notna().all():
            left = (100 * tiers["remaining"] / tiers["quantity"]).where(tiers["quantity"] > 0).fillna(0)
            fig.add_bar(y=labels, x=left, orientation="h", marker_color=GREEN,
                        customdata=tiers[["remaining", "quantity"]],
                        hovertemplate="<b>%{y}</b><br>%{customdata[0]:,} of %{customdata[1]:,} left<extra></extra>")
            fig.add_bar(y=labels, x=100 - left, orientation="h", marker_color=TRACK, hoverinfo="skip")
            fig.update_layout(barmode="stack", bargap=0.45)
            fig.update_xaxes(range=[0, 100.5], ticksuffix="%", title="Share of tier left", dtick=25)
        else:
            fig.add_bar(y=labels, x=tiers["remaining"], orientation="h", marker_color=GREEN,
                        hovertemplate="<b>%{y}</b><br>%{x:,} left<extra></extra>")
            fig.update_layout(bargap=0.45)
            fig.update_xaxes(type="log", title="Prizes left")
        fig.update_yaxes(autorange="reversed", type="category", showgrid=False)
        st.plotly_chart(style_fig(fig, max(220, 30 * len(tiers) + 60)), config=PLOT_CONFIG)
    if isinstance(g["detail_url"], str) and g["detail_url"].startswith("http"):
        st.markdown(f'<a class="src" href="{esc(g["detail_url"])}" target="_blank">View on scratchcards.net ↗</a>',
                    unsafe_allow_html=True)


# ---------- Footer ----------
st.markdown(
    '<div class="foot"><span>Data: <a href="https://www.lottery.net" target="_blank">lottery.net</a> · '
    '<a href="https://scratchcards.net" target="_blank">scratchcards.net</a>. '
    'Odds are published. Value left is our estimate from listed tiers. Not financial advice.</span>'
    '<span>Gambling problem? 1-800-GAMBLER</span></div>',
    unsafe_allow_html=True,
)
