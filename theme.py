"""
Capacity Planner theme layer.

Drop this file next to your Streamlit app and call apply_theme() once, right
after st.set_page_config(). Everything else in here is optional sugar.

    import streamlit as st
    import theme

    st.set_page_config(page_title="Capacity Planner", layout="wide")
    theme.apply_theme()

Requires: streamlit >= 1.29, plotly
"""

from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

INK = "#141B34"      # headings, metric values
SLATE = "#79819A"    # labels, axis text, secondary copy
PAPER = "#FFFFFF"    # panel surface
BONE = "#F7F5F2"     # page background
RULE = "#EAE7E1"     # hairlines and borders

# Data palette. These carry meaning in a capacity dashboard, so use them
# consistently: green = healthy, sand = approaching full, rose = over
# allocated, periwinkle = neutral volume bars.
GREEN = "#2FA36B"
SAND = "#E8A33D"
ROSE = "#D8436E"
CORAL = "#E8734A"
PERI = "#8CA6E8"

SEQUENCE = [PERI, GREEN, CORAL, ROSE, SAND, "#6C7A96"]

_FONT_HEAD = "Outfit"
_FONT_BODY = "Inter"


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600&family=Inter:wght@400;500;600&display=swap');

html, body, .stApp, button, input, textarea, select,
[data-testid="stMarkdownContainer"], [data-testid="stText"] {{
    font-family: '{_FONT_BODY}', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    -webkit-font-smoothing: antialiased;
}}

/* Streamlit draws its icons as ligatures in a Material font. A broad
   font-family rule silently replaces that font, and the ligature name then
   prints as literal text ("keyboard_double_arrow_right"). Put the icon font
   back on anything that draws one. */
span[data-testid="stIconMaterial"],
.material-icons, .material-icons-outlined,
[class*="material-symbols"], [class*="material-icons"] {{
    font-family: 'Material Symbols Rounded', 'Material Icons' !important;
}}

.stApp {{ background: {BONE}; }}

/* Strip the default Streamlit chrome */
#MainMenu, footer {{ visibility: hidden; }}
header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
.block-container {{ padding: 2.25rem 2.5rem 4rem; max-width: 1480px; }}

/* Headings */
h1, h2, h3, h4 {{
    font-family: '{_FONT_HEAD}', sans-serif;
    color: {INK};
    letter-spacing: -0.02em;
    font-weight: 600;
}}
h1 {{ font-size: 2.05rem; line-height: 1.15; margin: 0 0 .15rem; }}
h2 {{ font-size: 1.15rem; margin: 2.2rem 0 .9rem; }}
h3 {{ font-size: .98rem; margin: 0 0 .75rem; }}
p, li, .stMarkdown {{ color: {INK}; font-size: .93rem; line-height: 1.55; }}

/* Page intro line under the h1 */
.cp-sub {{
    color: {SLATE};
    font-size: .95rem;
    margin: 0 0 1.9rem;
}}

/* --- KPI tiles -------------------------------------------------------- */
/* Deliberately borderless: whitespace and a single accent rule carry the
   hierarchy, so the panels below stay the heavier element on the page. */
.cp-kpi {{
    padding: .1rem 0 .2rem;
    border-top: 2px solid var(--accent, {PERI});
    margin-top: .2rem;
}}
.cp-kpi .lbl {{
    display: block;
    color: {SLATE};
    font-size: .74rem;
    font-weight: 500;
    letter-spacing: .01em;
    margin: .7rem 0 .35rem;
}}
.cp-kpi .val {{
    font-family: '{_FONT_HEAD}', sans-serif;
    color: {INK};
    font-size: 2.15rem;
    font-weight: 600;
    line-height: 1;
    letter-spacing: -0.03em;
}}
.cp-kpi .val .unit {{
    font-size: 1.05rem;
    font-weight: 500;
    color: {SLATE};
    margin-left: .12rem;
    letter-spacing: 0;
}}
.cp-kpi .note {{
    display: block;
    color: {SLATE};
    font-size: .76rem;
    margin-top: .45rem;
}}
.cp-kpi .note.up {{ color: {GREEN}; }}
.cp-kpi .note.down {{ color: {ROSE}; }}

/* Tiles sized for the full-width page break mid-number in a dialog, which
   is how "352.5h" ended up as "352." over "5h". Scale them down there and
   never let a figure wrap. */
.cp-kpi .val {{ white-space: nowrap; }}
div[role="dialog"] .cp-kpi .val {{ font-size: 1.45rem; }}
div[role="dialog"] .cp-kpi .val .unit {{ font-size: .8rem; }}
div[role="dialog"] .cp-kpi .lbl {{ font-size: .68rem; }}

/* --- Panels ----------------------------------------------------------- */
/* Targets st.container(border=True). Flat, hairline, no shadow. */
div[data-testid="stVerticalBlockBorderWrapper"] {{
    background: {PAPER};
    border: 1px solid {RULE};
    border-radius: 10px;
    padding: 1.15rem 1.3rem 1.05rem;
}}

/* --- Tables ----------------------------------------------------------- */
div[data-testid="stDataFrame"] {{ border: 1px solid {RULE}; border-radius: 8px; }}

/* --- Controls --------------------------------------------------------- */
section[data-testid="stSidebar"] {{
    background: {PAPER};
    border-right: 1px solid {RULE};
}}
.stButton > button {{
    background: {INK};
    color: {PAPER};
    border: none;
    border-radius: 7px;
    padding: .42rem 1.05rem;
    font-weight: 500;
    font-size: .88rem;
}}
.stButton > button:hover {{ background: #232C4C; color: {PAPER}; }}

/* Streamlit renders a button's label inside a <p>, which the generic body
   rule above paints {INK} - navy text on a navy button, i.e. invisible.
   Colour the inner nodes, not just the button element. */
.stButton > button,
.stButton > button p,
.stButton > button div,
.stButton > button span,
.stButton > button [data-testid="stMarkdownContainer"] {{
    color: {PAPER} !important;
}}

.stButton > button:focus-visible {{ outline: 2px solid {PERI}; outline-offset: 2px; }}

div[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input {{
    border-color: {RULE};
    border-radius: 7px;
    background: {PAPER};
}}

.stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid {RULE}; }}
.stTabs [data-baseweb="tab"] {{
    padding: 0 0 .6rem;
    color: {SLATE};
    font-size: .9rem;
    font-weight: 500;
}}
.stTabs [aria-selected="true"] {{ color: {INK}; }}

hr {{ border-color: {RULE}; margin: 2rem 0; }}

@media (prefers-reduced-motion: reduce) {{
    * {{ animation: none !important; transition: none !important; }}
}}
</style>
"""


def apply_theme() -> None:
    """Inject fonts and CSS. Call once, after st.set_page_config()."""
    st.markdown(_CSS, unsafe_allow_html=True)


def page_title(title: str, subtitle: str = "") -> None:
    st.markdown(f"# {title}", unsafe_allow_html=False)
    if subtitle:
        st.markdown(f'<p class="cp-sub">{subtitle}</p>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# KPI tiles
# ---------------------------------------------------------------------------

def kpi(label: str, value, unit: str = "", note: str = "",
        note_tone: str = "", accent: str = PERI) -> None:
    """One metric tile. Call inside a st.columns() slot.

    note_tone: "up" (green), "down" (rose), or "" (slate).
    """
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    note_html = f'<span class="note {note_tone}">{note}</span>' if note else ""
    st.markdown(
        f'<div class="cp-kpi" style="--accent:{accent}">'
        f'<span class="lbl">{label}</span>'
        f'<span class="val">{value}{unit_html}</span>'
        f"{note_html}</div>",
        unsafe_allow_html=True,
    )


def kpi_row(items: list[dict]) -> None:
    """Render a row of tiles from a list of kwargs dicts for kpi()."""
    for col, item in zip(st.columns(len(items), gap="large"), items):
        with col:
            kpi(**item)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _style(fig: go.Figure, height: int = 260, showlegend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        showlegend=showlegend,
        margin=dict(l=0, r=0, t=6, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=f"{_FONT_BODY}, sans-serif", size=12, color=SLATE),
        colorway=SEQUENCE,
        hoverlabel=dict(
            bgcolor=INK, bordercolor=INK,
            font=dict(color="#FFFFFF", family=f"{_FONT_BODY}, sans-serif", size=12),
        ),
        legend=dict(
            orientation="v", x=1.02, y=0.5, yanchor="middle",
            font=dict(size=11.5, color=SLATE),
        ),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=RULE,
                     ticks="outside", tickcolor=RULE, ticklen=4)
    fig.update_yaxes(gridcolor=RULE, zeroline=False, linecolor="rgba(0,0,0,0)")
    return fig


def show(fig: go.Figure, key: str | None = None) -> None:
    """Render a Plotly figure with the toolbar hidden.

    Pass a key when the same chart can be drawn more than once in a single
    run - Streamlit IDs charts by type and parameters, so two identical
    figures collide with StreamlitDuplicateElementId.
    """
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False}, key=key)


def donut(labels, values, center_value=None, center_label="",
          colors=None, height: int = 260) -> go.Figure:
    """Ring chart with an optional figure in the hole."""
    fig = go.Figure(
        go.Pie(
            labels=list(labels), values=list(values),
            hole=0.68, sort=False,
            marker=dict(colors=colors or SEQUENCE, line=dict(color=PAPER, width=2)),
            textinfo="none",
            hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
        )
    )
    if center_value is not None:
        fig.add_annotation(
            text=(f"<span style='font-family:{_FONT_HEAD};font-size:26px;"
                  f"color:{INK}'>{center_value}</span><br>"
                  f"<span style='font-size:11px;color:{SLATE}'>{center_label}</span>"),
            x=0.5, y=0.5, showarrow=False, align="center",
        )
    return _style(fig, height=height, showlegend=True)


def hbar(labels, values, color=PERI, suffix="", height: int = 260) -> go.Figure:
    """Horizontal bars, largest at the top."""
    pairs = sorted(zip(list(labels), list(values)), key=lambda p: p[1])
    lab = [p[0] for p in pairs]
    val = [p[1] for p in pairs]
    fig = go.Figure(
        go.Bar(
            x=val, y=lab, orientation="h",
            marker=dict(color=color, line=dict(width=0)),
            width=0.62,
            text=[f"{v:,.0f}{suffix}" for v in val],
            textposition="outside",
            textfont=dict(color=SLATE, size=11.5),
            hovertemplate="%{y}: %{x:,.0f}" + suffix + "<extra></extra>",
        )
    )
    fig = _style(fig, height=height)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(showgrid=False, ticks="", tickfont=dict(color=INK, size=12))
    fig.update_layout(margin=dict(l=0, r=44, t=6, b=0), bargap=0.38)
    return fig


def trend(x, series: dict, height: int = 260, target: float | None = None,
          target_label: str = "Target") -> go.Figure:
    """One or more soft lines over a shared x axis, with an optional target rule."""
    fig = go.Figure()
    for i, (name, ys) in enumerate(series.items()):
        fig.add_trace(
            go.Scatter(
                x=list(x), y=list(ys), name=name, mode="lines",
                line=dict(width=2.4, shape="spline", smoothing=0.6,
                          color=SEQUENCE[i % len(SEQUENCE)]),
                hovertemplate="%{x}<br>%{y:,.0f}<extra>" + name + "</extra>",
            )
        )
    if target is not None:
        fig.add_hline(y=target, line=dict(color=RULE, width=1.5, dash="dot"),
                      annotation_text=target_label,
                      annotation_position="top left",
                      annotation_font=dict(size=11, color=SLATE))
    return _style(fig, height=height, showlegend=len(series) > 1)


def stacked_bar(x, series: dict, colors=None, height: int = 260,
                suffix="h") -> go.Figure:
    """Stacked columns over a shared x axis — e.g. rough / trim / final by day."""
    fig = go.Figure()
    palette = colors or SEQUENCE
    for i, (name, ys) in enumerate(series.items()):
        fig.add_trace(
            go.Bar(
                x=list(x), y=list(ys), name=name,
                marker=dict(color=palette[i % len(palette)], line=dict(width=0)),
                hovertemplate="%{x|%b %d}<br>%{y:,.1f}" + suffix
                              + "<extra>" + name + "</extra>",
            )
        )
    fig = _style(fig, height=height, showlegend=True)
    fig.update_layout(barmode="stack", bargap=0.25,
                      legend=dict(orientation="h", x=0, y=1.14, yanchor="bottom"))
    return fig


def utilization_color(pct: float) -> str:
    """Shared rule for what a utilization number means."""
    if pct > 100:
        return ROSE
    if pct >= 90:
        return SAND
    return GREEN
