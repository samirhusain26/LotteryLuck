import html
import json
import math
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlencode

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from scrape import DATA_DIR, STATE_NAMES, STATES, csv_path, scrape_state_df

st.set_page_config(page_title="Lottery Luck · Scratch-off odds", page_icon="🎟️", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(f"<style>{(DATA_DIR.parent / 'theme.css').read_text()}</style>", unsafe_allow_html=True)

INK, MUTED, LINE, SOFT, BLUE, GREEN, AMBER, RED = (
    "#0d1117", "#77808c", "#e7e9ee", "#f0f2f5", "#1652f0", "#12a150", "#e8930c", "#e5484d")
PRICE_COLORS = [(1, "#0ea5e9"), (2, "#14b8a6"), (3, "#10b981"), (5, "#f59e0b"), (10, "#f97316"),
                (20, "#ef4444"), (25, "#ec4899"), (30, "#8b5cf6"), (50, "#4f46e5"), (100, "#0d1117")]
COMPARE_COLORS = [BLUE, "#f97316", "#12a150", "#8b5cf6"]
THRESHOLDS = [(100, "$100+"), (1_000, "$1K+"), (10_000, "$10K+"), (100_000, "$100K+"), (1_000_000, "$1M+")]
SORTS = {
    "Most value left": ("prize_pool_remaining_pct", False),
    "Best odds": ("overall_odds_1_in", True),
    "Best $1K+ odds": ("odds_1k", True),
    "Biggest top prize": ("top_prize_amount", False),
    "Lowest price": ("price", True),
}
PAGE_SIZE = 24
MAX_COMPARE = 4
DEFAULT_STATE = "NJ"


# ---------- Formatting ----------
def is_missing(x) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def money(x) -> str:
    if is_missing(x):
        return "—"
    x = float(x)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= size:
            return f"${x / size:.1f}".rstrip("0").rstrip(".") + suffix
    return f"${x:,.0f}"


def price_label(x) -> str:
    return "—" if is_missing(x) else f"${x:g}"


def odds_label(x) -> str:
    if is_missing(x):
        return "—"
    if x >= 1_000_000:
        return f"1 in {x / 1e6:.1f}M"
    if x >= 10_000:
        return f"1 in {x / 1e3:.0f}K"
    return f"1 in {x:,.0f}" if x >= 100 else f"1 in {x:.2f}"


def count(x) -> str:
    return "—" if is_missing(x) else f"{int(x):,}"


def esc(s) -> str:
    return "—" if is_missing(s) else html.escape(str(s))


def price_color(p) -> str:
    if is_missing(p):
        return MUTED
    color = PRICE_COLORS[0][1]
    for level, c in PRICE_COLORS:
        if p >= level:
            color = c
    return color


def game_num_suffix(g) -> str:
    return "" if is_missing(g["game_number"]) else f" · #{int(g['game_number'])}"


def game_key(state: str, game_id) -> str:
    return f"{state}-{int(game_id)}"


# ---------- Data ----------
def parse_tiers(value) -> list:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        tiers = json.loads(value)
    except ValueError:
        return []
    return tiers if isinstance(tiers, list) else []


def tier_value(t: dict, price) -> float:
    """Cash value used for 'at least $X' buckets. Free tickets count at face value; prizes with no single
    cash value (annuities, special events) are top-tier prizes, so they count as larger than any threshold."""
    if t.get("is_ticket"):
        return float(price or 0)
    return float(t["prize"]) if t.get("prize") is not None else math.inf


def win_ladder(row) -> list:
    """Odds of winning at least each threshold, from printed quantities (i.e. over the full print run)."""
    tiers, printed = row["tiers"], row["est_tickets_printed"]
    if not tiers or is_missing(printed) or any(t.get("quantity") is None for t in tiers):
        return []
    top_value = max(tier_value(t, row["price"]) for t in tiers)
    out = []
    for threshold, label in THRESHOLDS:
        if threshold > top_value:
            break
        hits = [t for t in tiers if tier_value(t, row["price"]) >= threshold]
        qty = sum(t["quantity"] for t in hits)
        if qty:
            out.append({"label": label, "odds": printed / qty, "qty": qty,
                        "left": sum(t["remaining"] for t in hits)})
    top = tiers[0]
    if top.get("quantity"):
        out.append({"label": "Top prize", "odds": printed / top["quantity"], "qty": top["quantity"],
                    "left": top["remaining"]})
    # Thresholds that cover exactly the same prizes add nothing; keep the highest (most specific) one.
    deduped = []
    for item in out:
        if deduped and deduped[-1]["qty"] == item["qty"]:
            deduped[-1] = item
        else:
            deduped.append(item)
    return deduped


@st.cache_data(show_spinner=False)
def read_csv(path: str, mtime: float) -> pd.DataFrame:
    df = pd.read_csv(path)
    for c in ("game_id", "game_number", "price", "overall_odds_1_in", "top_prize_amount", "top_prizes_remaining",
              "tracked_prizes_remaining", "tracked_prizes_total", "prize_pool_remaining_pct", "est_tickets_printed"):
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("game_name", "top_prize_text", "detail_url", "source_updated", "scrape_ts", "prize_data"):
        if c not in df.columns:
            df[c] = pd.NA
    df = df.dropna(subset=["game_id"]).copy()
    df["tiers"] = df["prize_data"].map(parse_tiers)
    df["win_pct"] = (100 / df["overall_odds_1_in"]).where(df["overall_odds_1_in"] > 0)
    df["status"] = df["top_prizes_remaining"].map(lambda n: "unknown" if is_missing(n) else ("dead" if n == 0 else "live"))
    df["top_label"] = [money(a) if not is_missing(a) else (t if isinstance(t, str) else "—")
                       for a, t in zip(df["top_prize_amount"], df["top_prize_text"])]
    df["ladder"] = [win_ladder(r) for _, r in df.iterrows()]
    df["odds_1k"] = df["ladder"].map(lambda L: next((x["odds"] for x in L if x["label"] == "$1K+"), math.nan))
    df["key"] = [game_key(s, g) for s, g in zip(df["state"], df["game_id"])]
    return df.set_index("key", drop=False)


def load_state(state: str) -> pd.DataFrame:
    path = csv_path(state)
    if not path.exists():
        return pd.DataFrame()
    return read_csv(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def state_summary(mtimes: tuple) -> pd.DataFrame:
    rows = []
    for state, _ in mtimes:
        df = load_state(state)
        if df.empty:
            continue
        rows.append({"state": state, "name": STATE_NAMES[state], "games": len(df),
                     "value_left": df["prize_pool_remaining_pct"].mean(),
                     "best_odds": df["overall_odds_1_in"].min(),
                     "live_pct": 100 * (df["status"] == "live").mean()})
    return pd.DataFrame(rows)


def available_states() -> tuple:
    return tuple((s, csv_path(s).stat().st_mtime) for s in STATES if csv_path(s).exists())


def find_game(key: str):
    state = key.split("-", 1)[0]
    df = load_state(state) if state in STATES else pd.DataFrame()
    return df.loc[key] if not df.empty and key in df.index else None


# ---------- URL state ----------
def init_from_url():
    if st.session_state.get("_init"):
        return
    st.session_state["_init"] = True
    qp = st.query_params
    state = qp.get("state", DEFAULT_STATE).upper()
    st.session_state["state"] = state if state in STATES else DEFAULT_STATE
    if qp.get("sort") in SORTS:
        st.session_state["sort"] = qp["sort"]
    st.session_state["q"] = qp.get("q", "")
    st.session_state["live_only"] = qp.get("live") == "1"
    try:
        st.session_state["budget_req"] = float(qp["budget"]) if "budget" in qp else None
    except ValueError:
        st.session_state["budget_req"] = None
    st.session_state["cmp"] = [k for k in qp.get("cmp", "").split(",") if k][:MAX_COMPARE]
    st.session_state["pending_game"] = qp.get("game")


def base_url() -> str:
    try:
        return (st.context.url or "").split("?")[0]
    except Exception:
        return ""


def share_params(**extra) -> dict:
    s = st.session_state
    params = {"state": s["state"]}
    if s.get("budget") is not None and s.get("budget") != s.get("_max_price"):
        params["budget"] = f"{s['budget']:g}"
    if s.get("sort") and s["sort"] != next(iter(SORTS)):
        params["sort"] = s["sort"]
    if s.get("q"):
        params["q"] = s["q"]
    if s.get("live_only"):
        params["live"] = "1"
    if s.get("cmp"):
        params["cmp"] = ",".join(s["cmp"])
    params.update(extra)
    return params


def share_url(**extra) -> str:
    return f"{base_url()}?{urlencode(share_params(**extra))}"


# ---------- Visual pieces ----------
def gauge(pct, label="left") -> str:
    r, cx, cy = 28, 35, 36
    length = math.pi * r
    if is_missing(pct):
        value, color, text = 0, LINE, "—"
    else:
        value = max(0.0, min(100.0, float(pct)))
        color = GREEN if value >= 50 else AMBER if value >= 25 else RED
        text = f"{value:.0f}%"
    arc = f"M {cx - r} {cy} A {r} {r} 0 0 1 {cx + r} {cy}"
    return (f'<div class="gauge"><svg viewBox="0 0 70 44" width="70" height="44">'
            f'<path d="{arc}" fill="none" stroke="{SOFT}" stroke-width="7" stroke-linecap="round"/>'
            f'<path d="{arc}" fill="none" stroke="{color}" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{length * value / 100:.1f} {length:.1f}"/></svg>'
            f'<b>{text}</b><small>{label}</small></div>')


def icon(price, cls="gc-icon") -> str:
    return f'<div class="{cls}" style="background:{price_color(price)}"><span>{price_label(price)}</span></div>'


def status_pill(status: str) -> str:
    return {"live": '<span class="pill live">● Top prize live</span>',
            "dead": '<span class="pill dead">Top prizes gone</span>'}.get(status, "")


def card_html(g, ribbon: str = "") -> str:
    sub = f"Top prize {esc(g['top_label'])}"
    top_left = "—" if is_missing(g["top_prizes_remaining"]) else f"{int(g['top_prizes_remaining']):,} left"
    rib = f'<div class="ribbon">{ribbon}</div>' if ribbon else ""
    return (
        f'{rib}<div class="gc-top">{icon(g["price"])}'
        f'<div><div class="gc-name">{esc(g["game_name"])}</div><div class="gc-sub">{sub}</div></div>'
        f'{gauge(g["prize_pool_remaining_pct"])}</div>'
        f'<div class="gc-stats"><div><small>Odds</small><b>{odds_label(g["overall_odds_1_in"])}</b></div>'
        f'<div><small>$1K+ odds</small><b>{odds_label(g["odds_1k"])}</b></div>'
        f'<div><small>Top prizes</small><b>{top_left}</b></div></div>'
    )


def toggle_compare(key: str):
    cmp = st.session_state.setdefault("cmp", [])
    if key in cmp:
        cmp.remove(key)
    elif len(cmp) < MAX_COMPARE:
        cmp.append(key)
    else:
        st.toast(f"You can compare up to {MAX_COMPARE} games.")


def render_card(g, prefix: str, ribbon: str = ""):
    key = g["key"]
    with st.container(key=f"gcard-{prefix}-{key}"):
        st.markdown(card_html(g, ribbon), unsafe_allow_html=True)
        b1, b2 = st.columns(2, gap="small")
        if b1.button("View details", key=f"view-{prefix}-{key}", type="primary", width="stretch"):
            game_dialog(key)
        in_cmp = key in st.session_state.get("cmp", [])
        b2.button("Comparing" if in_cmp else "Compare", key=f"cmp-{prefix}-{key}", width="stretch",
                  icon=":material/check:" if in_cmp else ":material/add:", on_click=toggle_compare, args=(key,))


def chart_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Figtree, sans-serif", color=INK, size=12), showlegend=False,
        hoverlabel=dict(bgcolor="white", bordercolor=LINE, font=dict(family="Figtree", color=INK)),
    )
    fig.update_xaxes(gridcolor=SOFT, zeroline=False, linecolor=LINE, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=SOFT, zeroline=False, linecolor=LINE, tickfont=dict(color=MUTED))
    return fig


# ---------- Dialogs ----------
def game_dialog(key: str):
    g = find_game(key)
    if g is None:
        st.toast("That game isn't in the current data.")
        return
    st.dialog(" ", width="large")(_game_body)(key)


def _game_body(key: str):
    g = find_game(key)
    state = g["state"]
    num = "" if is_missing(g["game_number"]) else f"Game #{int(g['game_number'])} · "
    updated = f" · Updated {esc(g['source_updated'])}" if isinstance(g["source_updated"], str) else ""
    st.markdown(
        f'<div class="dlg-head">{icon(g["price"])}<div><div class="dlg-name">{esc(g["game_name"])}</div>'
        f'<div class="dlg-sub">{num}{esc(STATE_NAMES[state])}{updated}</div></div>'
        f'{gauge(g["prize_pool_remaining_pct"], "value left")}</div>',
        unsafe_allow_html=True,
    )
    printed = g["est_tickets_printed"]
    win = "—" if is_missing(g["win_pct"]) else f"{g['win_pct']:.1f}%"
    st.markdown(
        '<div class="kv">'
        f'<div><small>Overall odds</small><b>{odds_label(g["overall_odds_1_in"])}</b></div>'
        f'<div><small>Top prize</small><b>{esc(g["top_label"])}</b></div>'
        f'<div><small>Top prizes left</small><b>{count(g["top_prizes_remaining"])}</b></div>'
        f'<div><small>Status</small><b>{status_pill(g["status"]) or "—"}</b></div>'
        f'<div><small>Win chance</small><b>{win}</b></div>'
        f'<div><small>Prizes left</small><b>{count(g["tracked_prizes_remaining"])}</b></div>'
        f'<div><small>Prizes printed</small><b>{count(g["tracked_prizes_total"])}</b></div>'
        f'<div><small>Tickets printed (est.)</small><b>{"—" if is_missing(printed) else money(printed).lstrip("$")}</b></div>'
        "</div>",
        unsafe_allow_html=True,
    )

    ladder = g["ladder"]
    if ladder:
        best = min(x["odds"] for x in ladder)
        rows = "".join(
            f'<div class="lr"><span class="lbl">{x["label"]}</span>'
            f'<div class="track"><i style="width:{max(3, 100 * math.log10(1 + best) / math.log10(1 + x["odds"])):.0f}%"></i></div>'
            f'<span class="odds">{odds_label(x["odds"])}</span>'
            f'<span class="left">{100 * x["left"] / x["qty"]:.0f}% still out</span></div>'
            for x in ladder
        )
        st.markdown(f'<div class="ladder-h">Your odds of winning big<span>per ticket, over the full print run</span></div>'
                    f'<div class="ladder">{rows}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="note">Big-win odds need printed prize counts, which this state doesn\'t publish.</div>',
                    unsafe_allow_html=True)

    tiers = pd.DataFrame(g["tiers"])
    if not tiers.empty:
        st.markdown('<div class="ladder-h">Prizes left by tier</div>', unsafe_allow_html=True)
        labels = tiers["prize_text"].fillna("?")
        fig = go.Figure()
        if "quantity" in tiers and tiers["quantity"].notna().all():
            left = (100 * tiers["remaining"] / tiers["quantity"]).where(tiers["quantity"] > 0).fillna(0)
            fig.add_bar(y=labels, x=left, orientation="h", marker=dict(color=BLUE, cornerradius=4),
                        customdata=tiers[["remaining", "quantity"]],
                        hovertemplate="<b>%{y}</b><br>%{customdata[0]:,} of %{customdata[1]:,} left<extra></extra>")
            fig.add_bar(y=labels, x=100 - left, orientation="h", marker=dict(color=SOFT), hoverinfo="skip")
            fig.update_xaxes(range=[0, 100.5], ticksuffix="%", dtick=25)
        else:
            fig.add_bar(y=labels, x=tiers["remaining"], orientation="h", marker=dict(color=BLUE, cornerradius=4),
                        hovertemplate="<b>%{y}</b><br>%{x:,} left<extra></extra>")
            fig.update_xaxes(type="log")
        fig.update_layout(barmode="stack", bargap=0.38)
        fig.update_yaxes(autorange="reversed", type="category", showgrid=False)
        st.plotly_chart(chart_layout(fig, max(200, 28 * len(tiers) + 40)), config={"displayModeBar": False})

    domain = STATES[state][2]
    search = f"https://www.google.com/search?q=site%3A{domain}+{quote_plus(str(g['game_name']))}"
    if not is_missing(g["game_number"]):
        search += f"+{int(g['game_number'])}"
    c1, c2, c3 = st.columns(3)
    c1.link_button(f"Find on {domain}", search, icon=":material/verified:", width="stretch")
    c2.link_button("Source data", str(g["detail_url"]), icon=":material/open_in_new:", width="stretch")
    in_cmp = key in st.session_state.get("cmp", [])
    if c3.button("In compare" if in_cmp else "Add to compare", width="stretch", key=f"dlg_cmp_{key}",
                 icon=":material/check:" if in_cmp else ":material/add:"):
        toggle_compare(key)
        st.rerun()
    with st.expander("Share this game"):
        st.code(share_url(state=state, game=key), language=None)


def compare_dialog():
    st.dialog("Compare games", width="large")(_compare_body)()


def _compare_body():
    games = [g for g in (find_game(k) for k in st.session_state.get("cmp", [])) if g is not None]
    if len(games) < 2:
        st.info("Add at least two games to compare.")
        return

    def best_idx(values, lower_is_better):
        valid = [(v, i) for i, v in enumerate(values) if not is_missing(v)]
        if len(valid) < 2:
            return None
        return (min if lower_is_better else max)(valid)[1]

    metrics = [
        ("Price", [g["price"] for g in games], price_label, True),
        ("Overall odds", [g["overall_odds_1_in"] for g in games], odds_label, True),
        ("$1K+ odds", [g["odds_1k"] for g in games], odds_label, True),
        ("Value left", [g["prize_pool_remaining_pct"] for g in games], lambda v: "—" if is_missing(v) else f"{v:.0f}%", False),
        ("Top prize", [g["top_prize_amount"] for g in games], money, False),
        ("Top prizes left", [g["top_prizes_remaining"] for g in games], count, False),
    ]
    head = "".join(
        f'<td><div class="cmp-head">{icon(g["price"])}<div style="text-align:left"><b>{esc(g["game_name"])}</b>'
        f'<small>{g["state"]}{game_num_suffix(g)}</small></div></div></td>'
        for g in games)
    body = ""
    for label, values, fmt, lower in metrics:
        b = best_idx(values, lower)
        body += f"<tr><th>{label}</th>" + "".join(
            f'<td class="{"best" if i == b else ""}">{fmt(v)}</td>' for i, v in enumerate(values)) + "</tr>"
    st.markdown(f'<table class="cmp"><tr><th></th>{head}</tr>{body}</table>', unsafe_allow_html=True)

    labels = [lbl for _, lbl in THRESHOLDS] + ["Top prize"]
    fig = go.Figure()
    for i, g in enumerate(games):
        by_label = {x["label"]: 100 / x["odds"] for x in g["ladder"]}
        ys = [by_label.get(lbl) for lbl in labels]
        if not any(y is not None for y in ys):
            continue
        fig.add_bar(x=labels, y=ys, name=str(g["game_name"]), marker=dict(color=COMPARE_COLORS[i], cornerradius=4),
                    hovertemplate=f"<b>{esc(g['game_name'])}</b><br>%{{x}}: %{{y:.4g}}% per ticket<extra></extra>")
    if fig.data:
        st.markdown('<div class="ladder-h" style="margin-top:1.2rem">Chance of winning at least…'
                    '<span>per ticket, log scale</span></div>', unsafe_allow_html=True)
        ticks = [100, 10, 1, 0.1, 0.01, 0.001, 0.0001, 0.00001]
        fig.update_yaxes(type="log", tickvals=ticks, ticktext=[f"{v:g}%" for v in ticks])
        chart_layout(fig, 300).update_layout(showlegend=True, barmode="group", bargap=0.25,
                                             legend=dict(orientation="h", y=-0.15, font=dict(color=MUTED)))
        st.plotly_chart(fig, config={"displayModeBar": False})
    if st.button("Clear comparison", icon=":material/close:"):
        st.session_state["cmp"] = []
        st.rerun()


# ---------- Page ----------
init_from_url()
states_with_data = available_states()
summary = state_summary(states_with_data)


def on_map_select():
    ev = st.session_state.get("map")
    points = ev["selection"]["points"] if ev and ev.get("selection") else []
    if points and points[0].get("location") in STATES:
        st.session_state["state"] = points[0]["location"]
        st.session_state["limit"] = PAGE_SIZE


top_l, top_state, top_share = st.columns([6, 2.2, 1], vertical_alignment="center")
with top_l:
    st.markdown('<div class="brand"><div class="brand-logo">L</div><b>Lottery Luck</b></div>', unsafe_allow_html=True)
with top_state:
    state = st.selectbox("State", list(STATES), format_func=lambda s: STATE_NAMES[s], key="state",
                         label_visibility="collapsed")

df = load_state(state)
prices = sorted(df["price"].dropna().unique().tolist()) if not df.empty else []

# Budget slider options depend on the state; reset when the state changes.
if st.session_state.get("_budget_state") != state:
    st.session_state["_budget_state"] = state
    req = st.session_state.pop("budget_req", None)
    st.session_state["budget"] = (max([p for p in prices if p <= req], default=prices[-1]) if req and prices
                                  else (prices[-1] if prices else None))
    st.session_state["limit"] = PAGE_SIZE
st.session_state["_max_price"] = prices[-1] if prices else None

# ---------- Hero: state stats + clickable map ----------
with st.container(key="hero"):
    h_left, h_map = st.columns([1, 1.35], gap="large", vertical_alignment="center")
    with h_left:
        st.markdown(f'<div class="hero-state">{esc(STATE_NAMES[state])}</div>', unsafe_allow_html=True)
        if df.empty:
            st.markdown('<div class="hero-meta">No data saved for this state yet.</div>', unsafe_allow_html=True)
            if st.button(f"Fetch {STATE_NAMES[state]} now", type="primary", icon=":material/download:"):
                with st.spinner("Fetching games (about a minute)…"):
                    fresh = scrape_state_df(state)
                if fresh.empty:
                    st.error("Couldn't fetch this state right now.")
                else:
                    csv_path(state).parent.mkdir(parents=True, exist_ok=True)
                    fresh.to_csv(csv_path(state), index=False)
                    st.cache_data.clear()
                    st.rerun()
        else:
            updated = df["source_updated"].dropna()
            updated = updated.iloc[0].split(" at ")[0] if len(updated) else "recently"
            live = int((df["status"] == "live").sum())
            best = df.dropna(subset=["overall_odds_1_in"]).nsmallest(1, "overall_odds_1_in")
            vl = df["prize_pool_remaining_pct"].mean()
            st.markdown(
                f'<div class="hero-meta">{len(df)} scratch-offs · updated {esc(updated)}</div>'
                '<div class="hero-stats">'
                f'<div class="hs accent"><b>{live}</b><span>games with a top prize left</span></div>'
                f'<div class="hs"><b>{"—" if is_missing(vl) else f"{vl:.0f}%"}</b><span>avg prize value left</span></div>'
                f'<div class="hs"><b>{odds_label(best.iloc[0]["overall_odds_1_in"]) if not best.empty else "—"}</b>'
                f'<span>best odds{(" · " + esc(best.iloc[0]["game_name"])) if not best.empty else ""}</span></div>'
                f'<div class="hs"><b>{price_label(prices[0]) if prices else "—"}–{price_label(prices[-1]) if prices else ""}</b>'
                '<span>ticket prices</span></div></div>',
                unsafe_allow_html=True,
            )
    with h_map:
        locs = list(STATES)
        have = summary.set_index("state") if not summary.empty else pd.DataFrame()
        z = [have.at[s, "value_left"] if s in have.index and not is_missing(have.at[s, "value_left"]) else None for s in locs]
        text = [f"<b>{STATE_NAMES[s]}</b><br>" + (f"{int(have.at[s, 'games'])} games" if s in have.index else "No data yet")
                + (f" · {have.at[s, 'value_left']:.0f}% value left" if s in have.index and not is_missing(have.at[s, 'value_left']) else "")
                for s in locs]
        fig = go.Figure()
        fig.add_choropleth(  # base: every supported state, clickable
            locations=locs, locationmode="USA-states", z=[1] * len(locs), text=text, hoverinfo="text",
            colorscale=[[0, "#dfe6f7"], [1, "#dfe6f7"]], showscale=False, marker_line_color="white", marker_line_width=1,
            selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)),
        )
        valued = [(s, v, t) for s, v, t in zip(locs, z, text) if v is not None]
        if valued:
            fig.add_choropleth(
                locations=[v[0] for v in valued], locationmode="USA-states", z=[v[1] for v in valued],
                text=[v[2] for v in valued], hoverinfo="text", zmin=10, zmax=60,
                colorscale=[[0, "#c9d6fb"], [1, "#1652f0"]], showscale=False, marker_line_color="white",
                marker_line_width=1, selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)),
            )
        fig.add_choropleth(  # highlight the active state
            locations=[state], locationmode="USA-states", z=[1], colorscale=[[0, INK], [1, INK]], showscale=False,
            hoverinfo="skip", marker_line_color="white", marker_line_width=1.5,
            selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)),
        )
        fig.update_geos(scope="usa", bgcolor="rgba(0,0,0,0)", showlakes=False, landcolor="#eef0f4",
                        subunitcolor="white", showsubunits=True)
        fig.update_layout(height=330, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)",
                          dragmode=False, clickmode="event+select",
                          hoverlabel=dict(bgcolor="white", bordercolor=LINE, font=dict(family="Figtree", color=INK)))
        st.plotly_chart(fig, key="map", on_select=on_map_select, selection_mode="points",
                        config={"displayModeBar": False, "scrollZoom": False})
        st.markdown('<div class="map-hint">Tap a state · darker blue = more prize value left</div>', unsafe_allow_html=True)

with top_share:
    with st.popover("Share", icon=":material/ios_share:", width="stretch"):
        st.caption("Link to this exact view")
        st.code(share_url(), language=None)

if df.empty:
    st.stop()

# ---------- Spotlight ----------
pool = df[df["status"] != "dead"]
picks, used = [], set()
for label, col, asc in (("Most value left", "prize_pool_remaining_pct", False),
                        ("Best odds", "overall_odds_1_in", True),
                        ("Best shot at $1K+", "odds_1k", True)):
    ranked = pool.dropna(subset=[col]).sort_values(col, ascending=asc)
    ranked = ranked[~ranked.index.isin(used)]
    if not ranked.empty:
        picks.append((label, ranked.iloc[0]))
        used.add(ranked.index[0])
if picks:
    st.markdown('<div class="sec"><b>Top picks</b><span>with a top prize still out</span></div>', unsafe_allow_html=True)
    cols = st.columns(3, gap="medium")
    for c, (label, g) in zip(cols, picks):
        with c:
            render_card(g, "spot", ribbon=label)

# ---------- Filters ----------
st.markdown('<div class="sec"><b>All games</b></div>', unsafe_allow_html=True)
with st.container(key="filters"):
    f1, f2, f3, f4 = st.columns([2.4, 2, 1.8, 1.4], gap="medium", vertical_alignment="center")
    with f1:
        if len(prices) > 1:
            st.select_slider("Budget per ticket", options=prices, key="budget", format_func=lambda p: f"Up to ${p:g}")
        else:
            st.session_state["budget"] = prices[0] if prices else None
    with f2:
        st.text_input("Search", key="q", placeholder="Search by name or #", label_visibility="visible")
    with f3:
        st.selectbox("Sort by", list(SORTS), key="sort")
    with f4:
        st.toggle("Top prize left", key="live_only")

view = df
if st.session_state.get("budget") is not None:
    view = view[view["price"] <= st.session_state["budget"]]
q = (st.session_state.get("q") or "").strip().lower()
if q:
    view = view[view["game_name"].fillna("").str.lower().str.contains(q, regex=False)
                | view["game_number"].map(lambda n: "" if is_missing(n) else str(int(n))).str.contains(q.lstrip("#"), regex=False)]
if st.session_state.get("live_only"):
    view = view[view["status"] == "live"]
col, asc = SORTS[st.session_state.get("sort") or next(iter(SORTS))]
view = view.sort_values([col, "overall_odds_1_in"], ascending=[asc, True], na_position="last")

limit = st.session_state.setdefault("limit", PAGE_SIZE)
st.markdown(f'<div class="sec" style="margin-top:1rem"><span>{len(view)} games</span></div>', unsafe_allow_html=True)
if view.empty:
    st.info("No games match those filters.")
for start in range(0, min(limit, len(view)), 3):
    cols = st.columns(3, gap="medium")
    for c, (_, g) in zip(cols, view.iloc[start:start + 3].iterrows()):
        with c:
            render_card(g, "card")
if len(view) > limit:
    _, mid, _ = st.columns([2, 1, 2])
    if mid.button(f"Show more ({len(view) - limit})", width="stretch"):
        st.session_state["limit"] = limit + PAGE_SIZE
        st.rerun()

# ---------- Compare tray ----------
cmp = [k for k in st.session_state.get("cmp", []) if find_game(k) is not None]
st.session_state["cmp"] = cmp
if cmp:
    with st.container(key="tray"):
        t1, t2, t3 = st.columns([4, 1.4, 0.9], vertical_alignment="center", gap="small")
        names = ", ".join(str(find_game(k)["game_name"]) for k in cmp)
        t1.markdown(f'<div class="tray-text">Compare {len(cmp)} <span>· {esc(names)}</span></div>', unsafe_allow_html=True)
        if t2.button("Compare now", type="primary", width="stretch", disabled=len(cmp) < 2, key="tray_open"):
            compare_dialog()
        if t3.button("Clear", width="stretch", key="tray_clear"):
            st.session_state["cmp"] = []
            st.rerun()

# ---------- Footer ----------
st.markdown(
    '<div class="foot"><span>Data from <a href="https://scratchcards.net" target="_blank">scratchcards.net</a>. '
    'Odds are published; value left and big-win odds are calculated from listed prize tiers. Not financial advice.</span>'
    '<span>Gambling problem? Call 1-800-GAMBLER</span></div>',
    unsafe_allow_html=True,
)

# Keep the address bar in sync so any view can be shared.
st.query_params.from_dict(share_params())

pending = st.session_state.pop("pending_game", None)
if pending:
    game_dialog(pending)
