"""Общие элементы интерфейса: БД, фильтры, тепловая карта, форматирование."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import access, config, rules
from core.db import RiskDB
from core.seed import seed_demo

PAGES: dict = {}  # имя → st.Page, заполняет app.py (для переходов между страницами)
PROB_SHORT = {1: "Редко", 2: "Маловер.", 3: "Возможно", 4: "Вероятно", 5: "Почти<br>наверняка"}
IMPACT_SHORT = {1: "Незначит.", 2: "Малое", 3: "Умеренное", 4: "Значит.", 5: "Катастроф."}
BADGE = {"low": "green", "medium": "yellow", "high": "orange", "critical": "red"}  # цвет бейджа Streamlit по уровню


# ---------------------------------------------------------------- данные
@st.cache_resource(show_spinner="Готовлю реестр…")
def get_db() -> RiskDB:
    db = RiskDB(config.DB_PATH)
    with db._lock:  # первые сессии в облаке стартуют одновременно — наполняем один раз
        if not db.get_setting("initialized"):
            if db.count() == 0:
                seed_demo(db)
            db.set_setting("initialized", True)
    return db


def today() -> date:
    return date.today()


@dataclass(frozen=True)
class Data:
    risks: pd.DataFrame
    assessments: pd.DataFrame
    reg: pd.DataFrame
    levels: list


def load() -> Data:
    db = get_db()
    risks, ass, levels = db.risks(), db.assessments(), db.levels()
    return Data(risks, ass, rules.register_view(risks, ass, today(), levels), levels)


def role() -> str:
    return st.session_state.get("role", "Риск-менеджер")


def me() -> str:
    return st.session_state.get("me", "")


def actor() -> str:
    return me() or role()


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        try:
            key = st.secrets.get("ANTHROPIC_API_KEY", "")
        except Exception:  # secrets.toml нет — это нормально
            key = ""
    return key


# ---------------------------------------------------------------- фильтры
@dataclass(frozen=True)
class Filters:
    categories: tuple = ()
    owners: tuple = ()
    statuses: tuple = tuple(config.OPEN_STATUSES)
    levels: tuple = ()
    query: str = ""
    mine: bool = False


def sidebar_filters(reg: pd.DataFrame) -> Filters:
    sb = st.sidebar
    sb.markdown("**Фильтры**")
    cats = sorted(c for c in reg["category"].unique() if c)
    owners = sorted(o for o in reg["owner"].unique() if o)
    st.session_state.setdefault("f_status", list(config.OPEN_STATUSES))
    q = sb.text_input("Поиск", key="f_query", placeholder="название, описание, код")
    status = sb.multiselect("Статус", config.STATUSES, key="f_status", placeholder="все статусы")
    lv = sb.multiselect("Уровень", [lv["name"] for lv in sorted(get_db().levels(), key=lambda x: -x["min"])] + ["Не оценён"],
                        key="f_level", placeholder="все уровни")
    cat = sb.multiselect("Категория", cats, key="f_cat", placeholder="все категории")
    own = sb.multiselect("Владелец", owners, key="f_owner", placeholder="все владельцы")
    mine = role() == "Владелец риска" and bool(me()) and sb.toggle("Только мои риски", value=True, key="f_mine")
    return Filters(tuple(cat), tuple(own), tuple(status), tuple(lv), q.strip(), mine)


def apply_filters(df: pd.DataFrame, f: Filters) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    if f.categories:
        m &= df["category"].isin(f.categories)
    if f.owners:
        m &= df["owner"].isin(f.owners)
    if f.statuses:
        m &= df["status"].isin(f.statuses)
    if f.levels:
        m &= df["level"].isin(f.levels)
    if f.mine:
        m &= df["owner"] == me()
    if f.query:
        q = f.query.lower()
        m &= (df["title"].str.lower().str.contains(q, regex=False) | df["description"].str.lower().str.contains(q, regex=False)
              | df["code"].str.lower().str.contains(q, regex=False))
    return df[m]


# ---------------------------------------------------------------- оформление
def ink() -> dict:
    dark = getattr(getattr(st.context, "theme", None), "type", "light") == "dark"
    return {"text": "#f0efec" if dark else "#0b0b0b", "muted": "#898781", "surface": "#1a1a19" if dark else "#fcfcfb",
            "grid": "#2c2c2a" if dark else "#e1e0d9", "dark": dark}


def demo_badge():
    if get_db().get_setting("demo"):
        st.badge("Демо-данные: условный проект, синтетика", icon=":material/science:", color="orange")


def page_header(title: str, caption: str = ""):
    st.title(title)
    cols = st.columns([3, 1])
    if caption:
        cols[0].caption(caption)
    with cols[1]:
        demo_badge()


def fmt_date(d) -> str:
    d = rules.to_date(d)
    return d.strftime("%d.%m.%Y") if d else "—"


def level_chip(score, levels) -> str:
    lv = rules.level_for(score, levels)
    if lv is None:
        return ":gray-badge[Не оценён]"
    return ":%s-badge[%s · %d]" % (BADGE.get(lv["code"], "gray"), lv["name"], int(score))


def open_card(risk_id: int):
    st.session_state["card_risk_id"] = int(risk_id)
    st.switch_page(PAGES["card"])


def can_edit_risk(risk: dict | pd.Series) -> bool:
    return access.can_edit(role(), risk.get("owner") if isinstance(risk, dict) else risk["owner"], me())


# ---------------------------------------------------------------- тепловая карта
def _rgba(hex_color: str, a: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{a})"


def short_code(code: str) -> str:
    m = re.fullmatch(r"R-0*(\d+)", str(code))
    return m.group(1) if m else str(code)[:4]


def _offsets(n: int) -> list[tuple[float, float]]:
    """Раскладка n точек внутри ячейки (сетка до 3×3, дальше плотнее)."""
    if n <= 0:
        return []
    cols = 1 if n == 1 else 2 if n <= 4 else 3 if n <= 9 else 4
    rows = -(-n // cols)
    step_x, step_y = 0.72 / max(cols, 1), 0.6 / max(rows, 1)
    out = []
    for k in range(n):
        r, c = divmod(k, cols)
        in_row = min(cols, n - r * cols)
        x = (c - (in_row - 1) / 2) * step_x
        y = ((rows - 1) / 2 - r) * step_y - 0.06
        out.append((x, y))
    return out


def heatmap(points: pd.DataFrame, levels: list, prev: pd.DataFrame | None = None, height: int = 560,
            show_codes: bool = True, key: str | None = None, title_note: str = ""):
    """Матрица 5×5: ячейки — уровень, точки — риски, стрелки — движение с прошлого снимка.

    points: id, code, title, owner, p, i, score, level. prev: id, p, i (позиция в прошлом снимке).
    Возвращает id выбранного риска или ("cell", p, i), если кликнули по ячейке.
    """
    c = ink()
    fig = go.Figure()
    pts = points.dropna(subset=["p", "i"]).copy()
    pts["p"] = pts["p"].astype(int)
    pts["i"] = pts["i"].astype(int)
    counts = pts.groupby(["p", "i"]).size().to_dict()
    for p in range(1, config.SCALE + 1):
        for i in range(1, config.SCALE + 1):
            lv = rules.level_for(p * i, levels)
            fig.add_shape(type="rect", x0=i - 0.5, x1=i + 0.5, y0=p - 0.5, y1=p + 0.5, layer="below",
                          line=dict(color=c["surface"], width=3), fillcolor=_rgba(lv["color"], 0.42 if c["dark"] else 0.32))
            n = counts.get((p, i), 0)
            if n:
                fig.add_annotation(x=i + 0.44, y=p + 0.42, text=f"<b>{n}</b>", showarrow=False, xanchor="right", yanchor="top",
                                   font=dict(size=13, color=c["text"]))
    # невидимые точки-ячейки: подсказка и выбор ячейки кликом
    cells = [(p, i) for p in range(1, config.SCALE + 1) for i in range(1, config.SCALE + 1)]
    fig.add_trace(go.Scatter(
        x=[i for p, i in cells], y=[p for p, i in cells], mode="markers",
        marker=dict(size=60, symbol="square", color="rgba(0,0,0,0)"),
        customdata=[["cell", p, i] for p, i in cells],
        hovertemplate=[f"Вероятность {p} ({config.PROBABILITY_SCALE[p][0]}) · Влияние {i} ({config.IMPACT_SCALE[i][0]})<br>Балл {p * i} — {rules.level_name(p * i, levels)}"
                       f"<br>Рисков: {counts.get((p, i), 0)}<extra></extra>" for p, i in cells],
        showlegend=False, name="cells"))
    # позиции рисков внутри ячеек
    pts = pts.sort_values(["p", "i", "score", "code"], ascending=[True, True, False, True])
    xs, ys = [], []
    for _, grp in pts.groupby(["p", "i"], sort=False):
        for (dx, dy), (_, r) in zip(_offsets(len(grp)), grp.iterrows()):
            xs.append(r["i"] + dx)
            ys.append(r["p"] + dy)
    pts["x"], pts["y"] = xs, ys
    # стрелки движения
    if prev is not None and not prev.empty and not pts.empty:
        pv = prev.dropna(subset=["p", "i"]).set_index("id")
        for _, r in pts.iterrows():
            if r["id"] in pv.index:
                pp, pi = int(pv.at[r["id"], "p"]), int(pv.at[r["id"], "i"])
                if (pp, pi) != (r["p"], r["i"]):
                    worse = pp * pi < r["score"]
                    fig.add_annotation(x=r["x"], y=r["y"], ax=pi, ay=pp, xref="x", yref="y", axref="x", ayref="y",
                                       showarrow=True, arrowhead=2, arrowsize=1.1, arrowwidth=1.6, standoff=7,
                                       arrowcolor=c["text"] if worse else c["muted"], opacity=0.85, text="")
    if not pts.empty:
        trend = pts.get("trend", pd.Series("", index=pts.index)).fillna("")
        lvs = [rules.level_for(s, levels) for s in pts["score"]]
        fig.add_trace(go.Scatter(
            x=pts["x"], y=pts["y"], mode="markers+text" if show_codes else "markers",
            # в точке — короткий номер (R-007 → 7), полный код и название — в подсказке
            text=[short_code(x) for x in pts["code"]], textposition="middle center",
            textfont=dict(size=10, color=["#0b0b0b" if lv["code"] == "medium" else "#ffffff" for lv in lvs]),
            marker=dict(size=22 if show_codes else 13, color=[lv["color"] for lv in lvs], line=dict(color=c["surface"], width=2)),
            customdata=pts[["id"]].assign(c=pts["code"], t=pts["title"].str.slice(0, 70), o=pts["owner"].replace("", "—"),
                                          lv=pts["level"] + " · балл " + pts["score"].astype(str), tr=trend).values,
            hovertemplate="<b>%{customdata[1]}</b> %{customdata[2]}<br>Владелец: %{customdata[3]}<br>"
                          "%{customdata[4]} · тренд %{customdata[5]}<extra></extra>",
            showlegend=False, name="risks"))
    # длинные подписи Plotly скрывает при нехватке места — на оси короткие, полные — в подсказке ячейки
    ticks_i = [f"{k}<br>{IMPACT_SHORT.get(k, v[0])}" for k, v in config.IMPACT_SCALE.items()]
    ticks_p = [f"{PROB_SHORT.get(k, v[0])} {k}" for k, v in config.PROBABILITY_SCALE.items()]
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=30 if title_note else 8, b=8), dragmode=False, clickmode="event+select",
        title=dict(text=title_note, font=dict(size=13)) if title_note else None,
        xaxis=dict(title="Влияние →", range=[0.5, 5.5], tickvals=list(range(1, 6)), ticktext=ticks_i, showgrid=False,
                   zeroline=False, fixedrange=True),
        yaxis=dict(title="Вероятность →", range=[0.5, 5.5], tickvals=list(range(1, 6)), ticktext=ticks_p, showgrid=False,
                   zeroline=False, fixedrange=True),
        hoverlabel=dict(align="left"),
    )
    event = st.plotly_chart(fig, width="stretch", key=key, on_select="rerun" if key else "ignore",
                            selection_mode=("points",), config={"displayModeBar": False})
    legend = " ".join(":%s-badge[%s %s+]" % (BADGE.get(lv["code"], "gray"), lv["name"], lv["min"])
                      for lv in sorted(levels, key=lambda x: x["min"]))
    st.caption(f"{legend} Балл = вероятность × влияние. Стрелка — откуда риск переместился с прошлого снимка; "
               "тёмная — ухудшение, серая — улучшение. Клик по точке или ячейке — подробности.")
    if key and event and event.selection and event.selection.points:
        pt = event.selection.points[-1]
        cd = pt.get("customdata")
        if cd:
            if cd[0] == "cell":
                return ("cell", int(cd[1]), int(cd[2]))
            return int(cd[0])
    return None


def risk_table(df: pd.DataFrame, levels, key: str | None = None, height: int | None = None, select: bool = False):
    cols = ["code", "title", "owner", "category", "status", "p", "i", "score", "level", "trend", "due_date", "assessed_on"]
    view = df[cols].copy()
    view["due_date"] = view["due_date"].apply(rules.to_date)
    view["assessed_on"] = view["assessed_on"].apply(rules.to_date)
    cfg = {
        "code": st.column_config.TextColumn("Код", width="small"),
        "title": st.column_config.TextColumn("Риск", width="large"),
        "owner": "Владелец", "category": "Категория", "status": "Статус",
        "p": st.column_config.NumberColumn("В", help="Вероятность 1–5", width="small"),
        "i": st.column_config.NumberColumn("Вл", help="Влияние 1–5", width="small"),
        "score": st.column_config.ProgressColumn("Балл", min_value=0, max_value=25, format="%d", width="small"),
        "level": "Уровень", "trend": st.column_config.TextColumn("Тренд", help="Изменение балла за 7 дней", width="small"),
        "due_date": st.column_config.DateColumn("Срок мер", format="DD.MM.YYYY"),
        "assessed_on": st.column_config.DateColumn("Оценён", format="DD.MM.YYYY"),
    }
    kw = dict(hide_index=True, width="stretch", column_config=cfg)
    if height:
        kw["height"] = height
    if select and key:
        ev = st.dataframe(view, key=key, on_select="rerun", selection_mode="single-row", **kw)
        rows = ev.selection.rows if ev and ev.selection else []
        return int(df.iloc[rows[0]]["id"]) if rows else None
    st.dataframe(view, key=key, **kw)
    return None


def week_options(d: Data, n_weeks: int = 12) -> list[date]:
    if d.assessments.empty:
        return [rules.week_start(today())]
    start = max(d.assessments["assessed_on"].min(), today() - timedelta(weeks=n_weeks * 4))
    return rules.weeks_between(start, today())
