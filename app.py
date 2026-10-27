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
STALE_AFTER_DAYS = 7

GOLD, EMERALD, RED, MUTED, TEXT = "#f5c451", "#34d399", "#f87171", "#8ba396", "#e8f0ea"

EXPECTED_COLUMNS = [
    "game_number", "game_name", "price", "overall_odds_1_in", "top_prize_text", "top_prize_amount",
    "top_prizes_remaining", "detail_url", "prize_data", "tracked_prizes_remaining",
    "tracked_prizes_total", "prize_pool_remaining_pct", "scrape_ts",
]
NUMERIC_COLUMNS = [
    "game_number", "price", "overall_odds_1_in", "top_prize_amount", "top_prizes_remaining",
    "tracked_prizes_remaining", "tracked_prizes_total", "prize_pool_remaining_pct",
]

st.set_page_config(page_title="Lottery Luck · Scratch-off Insights", page_icon="🍀", layout="wide")
st.markdown(f"<style>{(APP_DIR / 'theme.css').read_text()}</style>", unsafe_allow_html=True)


# ---------- Formatting ----------
def is_missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA


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
    return html.escape(str(s)) if not is_missing(s) else "—"


# ---------- Data ----------
def csv_path(state: str) -> Path:
    return APP_DIR / f"scratchoffs_{state}.csv"


@st.cache_data(show_spinner=False)
def read_state_csv(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path)


def load_state(state: str) -> pd.DataFrame:
    path = csv_path(state)
    if not path.exists():
        with st.spinner(f"No saved data for {STATE_NAMES[state]} yet. Scraping lottery.net (this takes a minute or two)…"):
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


# ---------- Charts ----------
def style_fig(fig: go.Figure, height: int = 420) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Manrope, sans-serif", color=TEXT, size=13),
        margin=dict(l=10, r=10, t=30, b=10),
        hoverlabel=dict(bgcolor="#17221c", bordercolor=GOLD, font=dict(family="Manrope", color=TEXT)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=None, bgcolor="rgba(0,0,0,0)"),
    )
    fig.update_xaxes(gridcolor="rgba(232,240,234,0.06)", zeroline=False, linecolor="rgba(232,240,234,0.12)")
    fig.update_yaxes(gridcolor="rgba(232,240,234,0.06)", zeroline=False, linecolor="rgba(232,240,234,0.12)")
    return fig


PLOT_CONFIG = {"displayModeBar": False}


# ---------- Sidebar ----------
with st.sidebar:
    st.markdown(
        """<div class="brand"><div class="brand-mark">🍀</div>
        <div><div class="brand-name">Lottery Luck</div><div class="brand-tag">Scratch-off insights</div></div></div>""",
        unsafe_allow_html=True,
    )
    state = st.selectbox("State", list(STATE_SLUGS), format_func=lambda s: f"{STATE_NAMES[s]} ({s})", key="state")

df = load_state(state)
if df.empty:
    st.error("No data could be loaded for this state. lottery.net may be unavailable; try refreshing later.")
    st.stop()

with st.sidebar:
    st.markdown('<div class="side-label">Filters</div>', unsafe_allow_html=True)
    prices = sorted(df["price"].dropna().unique())
    picked_prices = st.pills(
        "Ticket price", prices, selection_mode="multi", default=prices, format_func=fmt_price,
        key=f"prices_{state}",
    )
    query = st.text_input("Search games", placeholder="e.g. Cash, Diamond, 1785")
    hide_dead = st.toggle("Hide games with no top prizes left", value=False)

    st.markdown('<div class="side-label">Data</div>', unsafe_allow_html=True)
    if st.button("Refresh from lottery.net", icon=":material/refresh:", width="stretch"):
        with st.spinner(f"Scraping {STATE_NAMES[state]}… about one second per game."):
            fresh = scrape_lottery_net_df(state)
        if fresh.empty:
            st.warning("Scrape returned no rows; keeping the saved data.")
        else:
            fresh.to_csv(csv_path(state), index=False)
            st.cache_data.clear()
            st.rerun()
    st.markdown(
        '<div class="side-note">Source: <a href="https://www.lottery.net" target="_blank">lottery.net</a> and '
        '<a href="https://scratchcards.net" target="_blank">scratchcards.net</a>. Always confirm with the official '
        "state lottery before buying.</div>",
        unsafe_allow_html=True,
    )

view = df[df["price"].isin(picked_prices)] if picked_prices else df
if query:
    q = query.strip().lower()
    view = view[
        view["game_name"].fillna("").str.lower().str.contains(q, regex=False)
        | view["game_number"].map(lambda n: "" if is_missing(n) else str(int(n))).str.contains(q, regex=False)
    ]
if hide_dead:
    view = view[view["status"] != "dead"]


# ---------- Hero ----------
ts = scraped_at(df)
if ts:
    stale = datetime.now(timezone.utc) - ts > pd.Timedelta(days=STALE_AFTER_DAYS)
    ts_label = ts.astimezone(DISPLAY_TZ).strftime("%b %-d, %Y · %-I:%M %p ET")
else:
    stale, ts_label = True, "unknown"

st.markdown(
    f"""<div class="hero"><div class="hero-row">
      <div>
        <div class="eyebrow">{esc(STATE_NAMES[state])} · {len(df)} active games</div>
        <h1>Find the tickets with <em>prizes left</em>.</h1>
        <p>Published odds side by side with how much of each game's tracked prize pool is still unclaimed,
        so you can skip games where the big prizes are already gone.</p>
      </div>
      <div class="pill {'stale' if stale else ''}"><span class="dot"></span>Data as of {esc(ts_label)}</div>
    </div></div>""",
    unsafe_allow_html=True,
)


# ---------- KPIs ----------
def kpi(label: str, tag: str, value: str, sub: str) -> str:
    return (
        f'<div class="kpi"><div class="kpi-label"><span>{label}</span>'
        f'<span class="kpi-tag {tag}">{tag}</span></div>'
        f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>'
    )


live_known = view[view["status"] != "unknown"]
n_live = int((live_known["status"] == "live").sum())
candidates = view[view["status"] != "dead"]
best_odds = candidates.dropna(subset=["overall_odds_1_in"]).nsmallest(1, "overall_odds_1_in")
best_pool = candidates.dropna(subset=["prize_pool_remaining_pct"]).nlargest(1, "prize_pool_remaining_pct")

cards = [
    kpi("Games shown", "published", f"{len(view)}<small> / {len(df)}</small>",
        f"{len(prices)} price points from {fmt_price(min(prices)) if prices else '—'} to {fmt_price(max(prices)) if prices else '—'}"),
    kpi("Top prize left", "published", f"{n_live}<small> / {len(live_known)}</small>",
        f"{len(live_known) - n_live} games have no top prizes left"),
]
if not best_odds.empty:
    g = best_odds.iloc[0]
    cards.append(kpi("Best odds", "published", f"1 in {g['overall_odds_1_in']:.2f}",
                     f"<b>{esc(g['game_name'])}</b> · {fmt_price(g['price'])}"))
else:
    cards.append(kpi("Best odds", "published", "—", "No odds published"))
if not best_pool.empty:
    g = best_pool.iloc[0]
    cards.append(kpi("Prize value left", "calculated", f"{g['prize_pool_remaining_pct']:.0f}%",
                     f"<b>{esc(g['game_name'])}</b> · {fmt_price(g['price'])}"))
else:
    cards.append(kpi("Prize value left", "calculated", "—", "Tier quantities not published"))
st.markdown(f'<div class="kpis">{"".join(cards)}</div>', unsafe_allow_html=True)


# ---------- Tabs ----------
tab_games, tab_explore, tab_detail, tab_method = st.tabs(
    ["🎟️  Games", "📈  Explore", "🔎  Game detail", "📘  How it works"]
)

STATUS_LABEL = {"live": "🟢 Top prize left", "dead": "🔴 Top prizes gone", "unknown": "⚪ Unknown"}

with tab_games:
    st.markdown('<div class="section-title">Every game, side by side</div>'
                '<div class="section-sub">Click a column header to sort. Prize value left covers the prize tiers '
                "scratchcards.net tracks; it is blank when original tier quantities aren't published.</div>",
                unsafe_allow_html=True)
    table = view.assign(status_label=view["status"].map(STATUS_LABEL)).sort_values(
        ["prize_pool_remaining_pct", "overall_odds_1_in"], ascending=[False, True], na_position="last"
    )
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        height=560,
        column_order=[
            "status_label", "game_name", "game_number", "price", "overall_odds_1_in", "win_pct",
            "top_prize_label", "top_prizes_remaining", "prize_pool_remaining_pct",
        ],
        column_config={
            "status_label": st.column_config.TextColumn("Status"),
            "game_name": st.column_config.TextColumn("Game", width="medium"),
            "game_number": st.column_config.NumberColumn("Game #", format="%d"),
            "price": st.column_config.NumberColumn("Price", format="$%d"),
            "overall_odds_1_in": st.column_config.NumberColumn("Odds (1 in)", format="%.2f",
                                                               help="Published odds of winning any prize"),
            "win_pct": st.column_config.NumberColumn("Win chance", format="%.1f%%",
                                                     help="1 ÷ published odds"),
            "top_prize_label": st.column_config.TextColumn("Top prize"),
            "top_prizes_remaining": st.column_config.NumberColumn("Top prizes left", format="%d"),
            "prize_pool_remaining_pct": st.column_config.ProgressColumn(
                "Prize value left", format="%.0f%%", min_value=0, max_value=100,
                help="Calculated: unclaimed prize value ÷ original prize value across tracked tiers",
            ),
        },
    )
    st.download_button(
        "Download CSV", view.drop(columns=["prize_data"]).to_csv(index=False).encode(),
        file_name=f"lottery_luck_{state}.csv", mime="text/csv", icon=":material/download:",
    )

with tab_explore:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown('<div class="section-title">Odds vs. prizes remaining</div>'
                    '<div class="section-sub">Up and to the right is better: higher win chance and more prize '
                    "value still in play. Bubble size is ticket price.</div>", unsafe_allow_html=True)
        plot = view.dropna(subset=["win_pct", "prize_pool_remaining_pct"]).copy()
        if plot.empty:
            st.info("No games with both published odds and tier quantities in this selection.")
        else:
            plot["Status"] = plot["status"].map({"live": "Top prize left", "dead": "Top prizes gone", "unknown": "Unknown"})
            plot["price_label"] = plot["price"].map(fmt_price)
            fig = px.scatter(
                plot, x="win_pct", y="prize_pool_remaining_pct", size="price", size_max=28,
                color="Status", hover_name="game_name",
                color_discrete_map={"Top prize left": EMERALD, "Top prizes gone": RED, "Unknown": MUTED},
                custom_data=["price_label", "overall_odds_1_in", "top_prize_label", "top_prizes_remaining"],
            )
            fig.update_traces(
                marker=dict(line=dict(width=1, color="rgba(10,15,12,0.8)"), opacity=0.85),
                hovertemplate="<b>%{hovertext}</b><br>%{customdata[0]} ticket · 1 in %{customdata[1]:.2f}"
                              "<br>Prize value left: %{y:.0f}%<br>Top prize: %{customdata[2]} "
                              "(%{customdata[3]} left)<extra></extra>",
            )
            fig.update_xaxes(title="Win chance (published odds)", ticksuffix="%")
            fig.update_yaxes(title="Prize value left (calculated)", ticksuffix="%", range=[0, 105])
            st.plotly_chart(style_fig(fig, 460), config=PLOT_CONFIG)

    with right:
        st.markdown('<div class="section-title">By price point</div>'
                    '<div class="section-sub">Average published win chance for each ticket price.</div>',
                    unsafe_allow_html=True)
        by_price = (
            view.dropna(subset=["price", "win_pct"]).groupby("price")
            .agg(win_pct=("win_pct", "mean"), games=("game_name", "count")).reset_index()
        )
        if by_price.empty:
            st.info("No published odds in this selection.")
        else:
            by_price["label"] = by_price["price"].map(fmt_price)
            fig = go.Figure(go.Bar(
                x=by_price["label"], y=by_price["win_pct"],
                marker=dict(color=by_price["win_pct"], colorscale=[[0, "#1f3a2d"], [1, GOLD]], line=dict(width=0)),
                text=by_price["win_pct"].map(lambda v: f"{v:.1f}%"), textposition="outside",
                customdata=by_price["games"],
                hovertemplate="<b>%{x} tickets</b><br>Avg win chance %{y:.1f}%<br>%{customdata} games<extra></extra>",
            ))
            fig.update_yaxes(ticksuffix="%", range=[0, by_price["win_pct"].max() * 1.2])
            fig.update_xaxes(type="category", title=None, tickangle=0)
            st.plotly_chart(style_fig(fig, 460), config=PLOT_CONFIG)

with tab_detail:
    if view.empty:
        st.info("No games match the current filters.")
    else:
        options = view.sort_values(["price", "game_name"]).index.tolist()
        pick = st.selectbox(
            "Choose a game", options, key=f"game_{state}",
            format_func=lambda i: f"{view.at[i, 'game_name']}  ·  {fmt_price(view.at[i, 'price'])}"
                                  + ("" if is_missing(view.at[i, 'game_number']) else f"  ·  #{int(view.at[i, 'game_number'])}"),
        )
        g = view.loc[pick]
        badge = {"live": ("live", "Top prize available"), "dead": ("dead", "No top prizes left"),
                 "unknown": ("unknown", "Top-prize status unknown")}[g["status"]]
        game_no = "" if is_missing(g["game_number"]) else f"Game #{int(g['game_number'])} · "
        st.markdown(
            f"""<div class="detail-head"><div>
                <div class="detail-name">{esc(g['game_name'])}</div>
                <div class="detail-meta">{game_no}{fmt_price(g['price'])} ticket ·
                Top prize {esc(g['top_prize_label'])}</div></div>
                <span class="badge {badge[0]}">{badge[1]}</span></div>""",
            unsafe_allow_html=True,
        )
        odds_txt = "—" if is_missing(g["overall_odds_1_in"]) else f"1 in {g['overall_odds_1_in']:.2f}"
        win_txt = "" if is_missing(g["win_pct"]) else f"{g['win_pct']:.1f}% chance to win something"
        pool_txt = "—" if is_missing(g["prize_pool_remaining_pct"]) else f"{g['prize_pool_remaining_pct']:.0f}%"
        st.markdown(
            '<div class="kpis">'
            + kpi("Overall odds", "published", odds_txt, win_txt)
            + kpi("Top prizes left", "published", fmt_int(g["top_prizes_remaining"]), esc(g["top_prize_label"]))
            + kpi("Prizes left", "calculated", fmt_int(g["tracked_prizes_remaining"]),
                  f"of {fmt_int(g['tracked_prizes_total'])} tracked" if not is_missing(g["tracked_prizes_total"]) else "tracked tiers")
            + kpi("Prize value left", "calculated", pool_txt, "unclaimed ÷ original value")
            + "</div>",
            unsafe_allow_html=True,
        )

        tiers = parse_tiers(g["prize_data"])
        if not tiers:
            st.info("No prize-tier data was scraped for this game.")
        else:
            tiers_df = pd.DataFrame(tiers)
            for c in ("tier", "prize_text", "quantity", "claimed", "remaining"):
                if c not in tiers_df.columns:
                    tiers_df[c] = pd.NA
            has_qty = tiers_df["quantity"].notna().all()
            if has_qty:
                tiers_df["left_pct"] = (100 * tiers_df["remaining"] / tiers_df["quantity"]).where(tiers_df["quantity"] > 0)

            chart_col, table_col = st.columns([1, 1], gap="large")
            with chart_col:
                st.markdown('<div class="section-title">Prize tiers</div>'
                            '<div class="section-sub">Share of each tier still unclaimed.</div>', unsafe_allow_html=True)
                labels = tiers_df["prize_text"].fillna("?")
                fig = go.Figure()
                if has_qty:
                    fig.add_bar(y=labels, x=tiers_df["left_pct"], orientation="h", name="Left", marker_color=EMERALD,
                                customdata=tiers_df[["remaining", "quantity"]],
                                hovertemplate="<b>%{y}</b><br>%{customdata[0]:,} of %{customdata[1]:,} left<extra></extra>")
                    fig.add_bar(y=labels, x=100 - tiers_df["left_pct"], orientation="h", name="Claimed",
                                marker_color="rgba(232,240,234,0.10)", hoverinfo="skip")
                    fig.update_layout(barmode="stack")
                    fig.update_xaxes(range=[0, 100], ticksuffix="%")
                else:
                    fig.add_bar(y=labels, x=tiers_df["remaining"], orientation="h", name="Remaining", marker_color=EMERALD,
                                hovertemplate="<b>%{y}</b><br>%{x:,} left<extra></extra>")
                    fig.update_xaxes(type="log", title="Prizes remaining (log scale)")
                fig.update_yaxes(autorange="reversed", type="category")
                st.plotly_chart(style_fig(fig, max(260, 34 * len(tiers_df) + 80)), config=PLOT_CONFIG)
            with table_col:
                st.markdown('<div class="section-title">Tier table</div>'
                            '<div class="section-sub">As listed by scratchcards.net.</div>', unsafe_allow_html=True)
                st.dataframe(
                    tiers_df, hide_index=True, width="stretch",
                    column_order=["prize_text", "quantity", "claimed", "remaining"],
                    column_config={
                        "prize_text": st.column_config.TextColumn("Prize"),
                        "quantity": st.column_config.NumberColumn("Printed", format="localized"),
                        "claimed": st.column_config.NumberColumn("Claimed", format="localized"),
                        "remaining": st.column_config.NumberColumn("Left", format="localized"),
                    },
                )
        if isinstance(g["detail_url"], str) and g["detail_url"].startswith("http"):
            st.link_button("View source page", g["detail_url"], icon=":material/open_in_new:")

with tab_method:
    st.markdown(
        """<div class="method-grid">
        <div class="method-card"><h4><span class="kpi-tag published">published</span>&nbsp; From the source</h4><dl>
          <dt>Overall odds</dt><dd>The printed odds of winning any prize, e.g. "1 in 3.74". Lower is better.</dd>
          <dt>Win chance</dt><dd>1 ÷ overall odds, shown as a percentage. It's the same number, just easier to compare.</dd>
          <dt>Top prize &amp; top prizes left</dt><dd>As listed on lottery.net. If a state's list leaves them out,
          they come from tier 1 of the game's prize table. Annuity prizes like "$1K/WK/LIFE" show as text because
          they don't have a single cash value.</dd>
        </dl></div>
        <div class="method-card"><h4><span class="kpi-tag calculated">calculated</span>&nbsp; Our estimates</h4><dl>
          <dt>Prize value left</dt><dd>Σ(prize × remaining) ÷ Σ(prize × printed) across the tiers scratchcards.net
          lists. Free-ticket prizes count at the ticket price; annuity prizes are left out. A low number means most of
          the money has already been won. It's blank when printed quantities aren't published (for example, PA).</dd>
          <dt>Prizes left</dt><dd>Total remaining prizes across those same tracked tiers.</dd>
          <dt>Why no "expected value"?</dt><dd>A real EV needs the number of unsold tickets, which no source publishes.
          Instead of inventing that number, we show how much of the prize pool is left. That's a relative signal,
          not a promise of a return.</dd>
        </dl></div>
        </div>""",
        unsafe_allow_html=True,
    )
    st.markdown("")
    st.caption(
        "Data comes from third-party aggregators and can lag official state lottery reports by days. "
        "Prize counts change as tickets are claimed."
    )

st.markdown(
    """<div class="footer">Lottery Luck is an informational tool, not financial advice. Every scratch-off has a
    negative expected return on average, so play for fun and only spend what you can afford to lose.<br>
    Need help? Call <a href="tel:18004262537">1-800-GAMBLER</a>.</div>""",
    unsafe_allow_html=True,
)
