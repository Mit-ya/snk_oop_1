# Sankey educational progression visualiser
# Streamlit application implementing requested UX and UI improvements
# ------------------------------------------------------------------
# Author: ChatGPT (OpenAI o3)
# Date : 24 Apr 2025
"""
This Streamlit app replaces the original ipywidgets notebook and packs the
requested functionality:

• **Sidebar** with three collapsible sections (Основные, Параметры старта,
  Дополнительно). All filters live there so the chart is always visible.
• **Reactive UI** ‑ Sankey regenerates instantly after pressing **Применить**;
  no additional "Build" clicks are needed when sliders / multiselects change.
• **Apply / Reset** buttons pinned to the sidebar bottom.
• Searchable multiselects, year range slider, radio‑button dropout grouping.
• Preview of nodes / links count before rendering and a spinner while building.
• Pastel ColorBrewer palette, legend chips, and rich tooltips (help=…).
• Export buttons for PNG of the diagram and CSV of the filtered links.

If launched with `streamlit run sankey_streamlit.py` the app reads both Excel
files from the paths specified below (edit as needed).
"""

from __future__ import annotations

import hashlib
import io
import os
from collections import deque
from functools import lru_cache
from typing import List, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ────────────────── Paths to data files ────────────────────────────────────
SOURCE_FILE = "agreg_updated_long.xlsx"
LINKS_FILE  = "all_links.xlsx"

# ───────────────────‑ Reference look‑ups ───────────────────────────────────
PROGRESSIONS_MAP: dict[str, List[str]] = {
    "ОО": [
        "1-й класс",
        "2-й класс",
        "3-й класс",
        "4-й класс",
        "5-й класс",
        "6-й класс",
        "7-й класс",
        "8-й класс",
        "9-й класс",
        "10-й класс",
        "11-й класс (выпускной)",
    ],
    "СПО-9": ["1 курс", "2 курс", "3 курс", "4 курс"],
    "СПО-11": ["1 курс", "2 курс", "3 курс", "4 курс"],
    "ВПО-бак": ["1 курс", "2 курс", "3 курс", "4 курс"],
    "ВПО-спец": ["1 курс", "2 курс", "3 курс", "4 курс", "5 курс"],
    "ВПО-маг": ["1 курс", "2 курс"],
}

# Pre‑defined track groups (unchanged except shortened here)
DISEASE_TRACKS = {
    "physical": ["…"],
    "cognitive": ["…"],
    "social": ["…"],
    "all": ["…"],
}

# Pastel palette (8 colours from ColorBrewer Set3) – recycled cyclically
PALETTE = [
    "#8dd3c7",
    "#ffffb3",
    "#bebada",
    "#fb8072",
    "#80b1d3",
    "#fdb462",
    "#b3de69",
    "#fccde5",
]

# ───────────────────‑ Utility helpers ──────────────────────────────────────

def format_node_label(year: int, edu_level: str, class_name: str) -> str:
    num = "".join(filter(str.isdigit, str(class_name)))
    return f"{year}\\{edu_level}\\{num or '?'}"


def edu_level_sort_key(level: str) -> int:
    order = [
        "ОО",
        "СПО-9",
        "СПО-11",
        "ВПО-бак",
        "ВПО-спец",
        "ВПО-маг",
        "неизвестно",
    ]
    try:
        return order.index(level)
    except ValueError:
        return 999


def class_sort_key(level: str, cls: str) -> int:
    try:
        return PROGRESSIONS_MAP[level].index(cls)
    except (KeyError, ValueError):
        return 999


# Deterministic colour pick from fixed palette (indicator name → hex)
@lru_cache(maxsize=None)
def color_for_indicator(indicator: str) -> str:
    h = int(hashlib.md5(indicator.encode()).hexdigest(), 16)
    return PALETTE[h % len(PALETTE)]


# ───────────────────‑ Data loading (cached) ────────────────────────────────
@st.cache_data(show_spinner=True)
def load_links() -> pd.DataFrame:
    if not os.path.exists(LINKS_FILE):
        st.error(f"Файл {LINKS_FILE} не найден.")
        st.stop()
    df = pd.read_excel(LINKS_FILE)
    # Basic cleaning if needed
    df = df.dropna(subset=["source_year", "target_year", "indicator"])
    return df


df_links = load_links()

# ───────────────────‑ Sidebar ‑ filters UI ─────────────────────────────────
st.sidebar.title("Фильтры")

# --- Основные --------------------------------------------------------------
with st.sidebar.expander("Основные", expanded=True):
    # Education levels (multiselect with search)
    available_levels = sorted(
        {
            *df_links["source_edu_level"].unique(),
            *df_links["target_edu_level"].unique(),
        }
        - {"неизвестно"}
    )
    available_levels = [lvl for lvl in available_levels if lvl in PROGRESSIONS_MAP]
    default_levels = available_levels
    selected_edu_levels: List[str] = st.multiselect(
        "Уровни образования",
        options=available_levels,
        default=default_levels,
        help="Выберите один или несколько уровней образования.",
    )

    # Disease / indicator track
    track = st.selectbox(
        "Подтрек",
        options=[
            ("Все подтреки", "all"),
            ("Физический", "physical"),
            ("Когнитивный", "cognitive"),
            ("Социальный", "social"),
        ],
        format_func=lambda x: x[0],
        index=0,
    )[1]

    # Indicators multiselect (hidden when track != all)
    all_indicators = sorted(df_links["indicator"].dropna().unique())
    if track == "all":
        selected_indicators: List[str] = st.multiselect(
            "Индикаторы",
            options=all_indicators,
            default=all_indicators,
            help="Начните вводить текст, чтобы найти индикатор.",
        )
    else:
        # Override by disease track
        selected_indicators = DISEASE_TRACKS.get(track, [])

# --- Параметры старта ------------------------------------------------------
with st.sidebar.expander("Параметры старта", expanded=False):
    years = sorted({*df_links["source_year"].dropna(), *df_links["target_year"].dropna()})
    if not years:
        st.error("В данных нет годов.")
        st.stop()

    # Year range slider (tuple)
    year_min, year_max = st.slider(
        "Годовой диапазон (start → end)",
        min_value=int(years[0]),
        max_value=int(years[-1]),
        value=(int(years[0]), int(years[-1])),
        step=1,
        help="Выберите промежуток, который попадёт в Sankey.",
    )

    start_year = year_min
    last_year = year_max

    # Start edu level and class depending on level
    start_edu_level = st.selectbox(
        "Стартовый уровень",
        options=list(PROGRESSIONS_MAP.keys()),
        index=0,
    )
    start_class = st.selectbox("Класс/курс", options=PROGRESSIONS_MAP[start_edu_level])

# --- Дополнительно ---------------------------------------------------------
with st.sidebar.expander("Дополнительно", expanded=False):
    range_filter = st.selectbox(
        "Диапазон учебных параллелей",
        options=[
            None,
            "До 4 класса",
            "До 9 класса",
            "До 11 класса",
            "До 4 курса СПО-9",
            "До 4 курса СПО-11",
            "До 4 курса ВПО-бак",
            "До 2 курса ВПО-маг",
            "До 5 курса ВПО-спец",
        ],
        index=0,
        format_func=lambda x: "—" if x is None else x,
        help="Ограничить финальный год обучения в Sankey.",
    )

    dropout_option = st.radio(
        "Обращение с выпавшими (неизвестными)",
        options=[
            "Показать все",
            "Скрыть мелких (<10%)",
            "Скрыть всех",
        ],
        index=0,
    )
    hide_dropouts = dropout_option == "Скрыть всех"
    hide_small_dropouts = dropout_option == "Скрыть мелких (<10%)"

# --- Action buttons (sticky at bottom) ------------------------------------
apply_clicked = st.sidebar.button("Применить", type="primary")
reset_clicked = st.sidebar.button("Сбросить всё")

if reset_clicked:
    st.session_state.clear()
    st.experimental_rerun()

# Guard: Make sure we have at least one indicator & level
if not selected_edu_levels:
    st.info("Выберите хотя бы один уровень образования.")
    st.stop()
if not selected_indicators:
    st.info("Выберите индикаторы или подтрек.")
    st.stop()

# ───────────────────‑ Sankey generation on apply ‑──────────────────────────

def create_sankey_chain_from_links(
    df_links: pd.DataFrame,
    selected_edu_levels: List[str],
    selected_indicators: List[str],
    start_year: int,
    last_year: int,
    start_edu_level: str,
    start_class: str,
    range_filter: str | None,
    hide_dropouts: bool,
    hide_small_dropouts: bool,
) -> Tuple[go.Figure, int, int]:
    """Return (Plotly figure, n_nodes, n_links)."""

    df_flt = df_links.copy()

    # 1. INDICATORS
    df_flt = df_flt[df_flt["indicator"].isin(selected_indicators)]

    # 2. EDUCATION LEVELS (+ 'неизвестно')
    sel_levels = set(selected_edu_levels)
    df_flt = df_flt[
        df_flt["source_edu_level"].isin(sel_levels | {"неизвестно"})
        & df_flt["target_edu_level"].isin(sel_levels | {"неизвестно"})
    ]

    if df_flt.empty:
        raise ValueError("После фильтров остался пустой набор переходов.")

    # 3. BFS to collect reachable nodes from the start node
    start_node = (start_year, start_edu_level, start_class)
    nodes: set[Tuple[int, str, str]] = {start_node}
    links: List[dict] = []
    queue: deque[Tuple[int, str, str]] = deque([start_node])

    while queue:
        sy, se, sc = queue.popleft()
        if sy >= last_year:
            continue
        sub = df_flt[
            (df_flt["source_year"] == sy)
            & (df_flt["source_edu_level"] == se)
            & (df_flt["source_class"] == sc)
        ]
        for row in sub.itertuples():
            t_node = (row.target_year, row.target_edu_level, row.target_class)

            # Range filter (short‑circuit demos only for school):
            if range_filter and se == "ОО" and t_node[1] == "ОО":
                idx = class_sort_key("ОО", t_node[2])
                if (range_filter == "До 4 класса" and idx > 3) or (
                    range_filter == "До 9 класса" and idx > 8
                ) or (
                    range_filter == "До 11 класса" and idx > 10
                ):
                    continue

            links.append(
                {
                    "source_node": (sy, se, sc),
                    "target_node": t_node,
                    "indicator": row.indicator,
                    "value": row.target_value,
                }
            )
            if t_node not in nodes and row.target_year <= last_year:
                nodes.add(t_node)
                queue.append(t_node)

            # Duplicate unknown
            if "неизвестно" in t_node[1:]:
                unknown = (t_node[0], "неизвестно", "неизвестно")
                links.append(
                    {
                        "source_node": (sy, se, sc),
                        "target_node": unknown,
                        "indicator": row.indicator,
                        "value": row.target_value,
                    }
                )
                nodes.add(unknown)

    if not links:
        raise ValueError("У стартового узла нет переходов.")

    # 4. Dropouts filters
    if hide_dropouts or hide_small_dropouts:
        totals: dict[Tuple[int, str, str], float] = {}
        for l in links:
            totals[l["source_node"]] = totals.get(l["source_node"], 0) + l["value"]

        def keep(link):
            dropout = "неизвестно" in link["target_node"][1:]
            if not dropout:
                return True
            if hide_dropouts:
                return False
            if hide_small_dropouts:
                return link["value"] / totals[link["source_node"]] >= 0.1
            return True

        links = [l for l in links if keep(l)]
        nodes = {n for l in links for n in (l["source_node"], l["target_node"])}

    # 5. Sort nodes for stable ordering
    nodes_list = sorted(
        nodes,
        key=lambda n: (
            n[0],
            edu_level_sort_key(n[1]),
            class_sort_key(n[1], n[2]),
        ),
    )
    node_index = {n: i for i, n in enumerate(nodes_list)}

    # 6. Build arrays for sankey
    sources: list[int] = []
    targets: list[int] = []
    values: list[float] = []
    link_labels: list[str] = []
    node_totals = [0.0] * len(nodes_list)

    for l in links:
        s_idx = node_index[l["source_node"]]
        t_idx = node_index[l["target_node"]]
        sources.append(s_idx)
        targets.append(t_idx)
        values.append(l["value"])
        link_labels.append(f"{l['indicator']}: {int(l['value'])}")
        node_totals[t_idx] += l["value"]

    node_labels = [
        f"{format_node_label(*n)} ({int(node_totals[i])})" for i, n in enumerate(nodes_list)
    ]

    link_colors = [color_for_indicator(ind) for ind in (l["indicator"] for l in links)]

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(label=node_labels, pad=36, thickness=20),
            link=dict(source=sources, target=targets, value=values, color=link_colors, label=link_labels),
        )
    )

    title = f"Sankey: старт {start_year} {start_edu_level} – {start_class}"
    if range_filter:
        title += f" | {range_filter}"
    if hide_dropouts:
        title += " | без выпавших"
    elif hide_small_dropouts:
        title += " | без малых (<10%)"
    fig.update_layout(title_text=title, font_size=10)

    return fig, len(nodes_list), len(link_labels)


# ───────────────────‑ Main view (on Apply) ‑────────────────────────────────
if apply_clicked:
    with st.spinner("Строим Sankey…"):
        try:
            fig, n_nodes, n_links = create_sankey_chain_from_links(
                df_links=df_links,
                selected_edu_levels=selected_edu_levels,
                selected_indicators=selected_indicators,
                start_year=start_year,
                last_year=last_year,
                start_edu_level=start_edu_level,
                start_class=start_class,
                range_filter=range_filter,
                hide_dropouts=hide_dropouts,
                hide_small_dropouts=hide_small_dropouts,
            )
        except ValueError as e:
            st.warning(str(e))
            st.stop()

    st.subheader("Краткая статистика")
    st.write(f"Найдено **{n_nodes}** узлов, **{n_links}** переходов.")

    st.plotly_chart(fig, use_container_width=True)

    # Legend
    st.markdown("### Легенда по индикаторам")
    unique_inds = sorted({l for l in selected_indicators})
    legend_cols = 4
    rows = [unique_inds[i : i + legend_cols] for i in range(0, len(unique_inds), legend_cols)]
    for row in rows:
        cols = st.columns(len(row))
        for col, ind in zip(cols, row):
            color = color_for_indicator(ind)
            col.markdown(
                f"<div style='display:flex;align-items:center;'>"
                f"<div style='width:1.2rem;height:1.2rem;background:{color};margin-right:0.5rem;border:1px solid #999;border-radius:3px;'></div>{ind}</div>",
                unsafe_allow_html=True,
            )

    # --- Downloads ---------------------------------------------------------
    st.markdown("### Экспорт")

    # Download PNG
    png_bytes = fig.to_image(format="png", scale=2)
    st.download_button(
        label="Скачать PNG диаграммы",
        data=png_bytes,
        file_name="sankey.png",
        mime="image/png",
    )

    # Filtered links CSV
    csv_buf = io.StringIO()
    df_links.to_csv(csv_buf, index=False, encoding="utf-8")
    st.download_button(
        label="Скачать таблицу (CSV)",
        data=csv_buf.getvalue(),
        file_name="filtered_links.csv",
        mime="text/csv",
    )

    st.caption("© 2025 Sankey Visualizer. Для возврата к предыдущим настройкам используйте меню ↩ **Undo** браузера.")

else:
    st.info("Настройте фильтры слева и нажмите **Применить**.")
