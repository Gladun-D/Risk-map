"""Риски в разрезе владельцев."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import rules
from views import common


def render():
    d = common.load()
    f = common.sidebar_filters(d.reg)
    reg = common.apply_filters(d.reg, f)
    common.page_header("Владельцы рисков", "Нагрузка и состояние рисков по ответственным.")
    open_ = reg[reg["is_open"]].copy()
    if open_.empty:
        st.info("Нет открытых рисков в выборке.")
        return
    open_["owner"] = open_["owner"].replace("", "— не назначен —")
    att = rules.attention(open_.assign(owner=open_["owner"].replace("— не назначен —", "")), common.today(), d.levels)
    levels = sorted(d.levels, key=lambda x: x["min"])
    rows = []
    for owner, g in open_.groupby("owner"):
        row = {"Владелец": owner, "Открытых": len(g)}
        for lv in reversed(levels):
            row[lv["name"]] = int((g["level"] == lv["name"]).sum())
        row["Без оценки"] = int(g["score"].isna().sum())
        row["Суммарный балл"] = int(g["score"].fillna(0).sum())
        row["Выросли за неделю"] = int(g["id"].isin(att["grown"]["id"]).sum())
        row["Меры просрочены"] = int(g["id"].isin(att["overdue"]["id"]).sum())
        row["Давно не пересматривались"] = int(g["id"].isin(att["stale"]["id"]).sum())
        rows.append(row)
    table = pd.DataFrame(rows).sort_values(["Суммарный балл", "Открытых"], ascending=False)
    c = common.ink()
    fig = go.Figure()
    for lv in levels:
        fig.add_trace(go.Bar(y=table["Владелец"], x=table[lv["name"]], name=lv["name"], orientation="h", marker_color=lv["color"],
                             marker_line=dict(color=c["surface"], width=2), hovertemplate="%{y}<br>" + lv["name"] + ": %{x}<extra></extra>"))
    fig.add_trace(go.Bar(y=table["Владелец"], x=table["Без оценки"], name="Без оценки", orientation="h", marker_color="#9aa0a6",
                         marker_line=dict(color=c["surface"], width=2), hovertemplate="%{y}<br>Без оценки: %{x}<extra></extra>"))
    fig.update_layout(barmode="stack", height=max(260, 34 * len(table) + 80), margin=dict(l=4, r=4, t=4, b=4),
                      legend=dict(orientation="h", y=-0.15), yaxis=dict(autorange="reversed", fixedrange=True),
                      xaxis=dict(title="Открытых рисков", fixedrange=True))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="own_bar")
    st.dataframe(table, hide_index=True, width="stretch")
    st.subheader("Риски владельца")
    owner = st.selectbox("Владелец", table["Владелец"].tolist(), key="own_sel")
    sub = open_[open_["owner"] == owner].sort_values("score", ascending=False, na_position="last")
    sel = common.risk_table(sub, d.levels, key="own_table", select=True)
    if sel:
        common.open_card(sel)
