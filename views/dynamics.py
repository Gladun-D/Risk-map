"""Динамика по неделям: уровни, суммарный балл, лента «риск × неделя», лидеры изменений."""
from dataclasses import replace

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import rules
from views import common


def render():
    d = common.load()
    f = common.sidebar_filters(d.reg)
    reg = common.apply_filters(d.reg, replace(f, statuses=(), levels=()))
    common.page_header("Динамика по неделям", "Положение на конец каждой недели: последняя оценка до даты, закрытые риски выбывают.")
    all_weeks = common.week_options(d)
    if len(all_weeks) < 2:
        st.info("Для динамики нужны оценки хотя бы за две недели.")
    n = st.select_slider("Период, недель", options=list(range(2, max(len(all_weeks), 2) + 1)), value=min(12, max(len(all_weeks), 2)),
                         key="dyn_n") if len(all_weeks) > 2 else len(all_weeks)
    weeks = all_weeks[-n:]
    snaps = rules.weekly_snapshots(d.risks[d.risks["id"].isin(reg["id"])], d.assessments, weeks)
    summary = rules.weekly_summary(snaps, weeks, d.levels)
    c = common.ink()
    levels = sorted(d.levels, key=lambda x: x["min"])

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Число рисков по уровням")
        fig = go.Figure()
        for lv in levels:
            fig.add_trace(go.Bar(x=summary["Ось"], y=summary[lv["name"]], name=lv["name"], marker_color=lv["color"],
                                 marker_line=dict(color=c["surface"], width=2), customdata=summary["Неделя"],
                                 hovertemplate="%{customdata}<br>" + lv["name"] + ": %{y}<extra></extra>"))
        fig.update_layout(barmode="stack", height=340, margin=dict(l=4, r=4, t=4, b=4), legend=dict(orientation="h", y=-0.2),
                          yaxis=dict(title="Рисков", fixedrange=True), xaxis=dict(fixedrange=True, title="Неделя с"))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="dyn_levels")
    with right:
        st.subheader("Суммарный балл открытых рисков")
        fig = go.Figure(go.Scatter(x=summary["Ось"], y=summary["Суммарный балл"], customdata=summary["Неделя"], mode="lines+markers+text",
                                   text=[str(v) if k in (0, len(summary) - 1) else "" for k, v in enumerate(summary["Суммарный балл"])],
                                   textposition="top center", line=dict(color="#2a78d6", width=2), marker=dict(size=8),
                                   hovertemplate="%{customdata}: %{y}<extra></extra>", showlegend=False))
        fig.update_layout(height=340, margin=dict(l=4, r=4, t=4, b=4), yaxis=dict(title="Сумма баллов", rangemode="tozero", fixedrange=True),
                          xaxis=dict(fixedrange=True))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="dyn_total")

    st.subheader("Лента рисков по неделям")
    st.caption("Строка — риск, столбец — неделя, число — балл, цвет — уровень. Пусто — риска ещё не было или он закрыт.")
    if snaps.empty:
        st.write("Нет данных за период.")
    else:
        piv = snaps.pivot_table(index="risk_id", columns="week", values="score", aggfunc="last")
        meta = reg.set_index("id")
        piv = piv[piv.index.isin(meta.index)]
        last = piv.ffill(axis=1).iloc[:, -1].fillna(0)
        piv = piv.loc[last.sort_values(ascending=False).index]
        names = [f"{meta.at[r, 'code']} {meta.at[r, 'title'][:38]}" for r in piv.index]
        rank = piv.map(lambda s: rules.level_rank(s, d.levels) if pd.notna(s) else None)
        n_lv = len(levels)
        scale = []
        for k, lv in enumerate(levels):  # дискретная шкала: уровень k → свой цвет
            scale += [[k / n_lv, lv["color"]], [(k + 1) / n_lv, lv["color"]]]
        fig = go.Figure(go.Heatmap(
            z=rank.values.astype(float), x=[rules.week_short(w) for w in piv.columns], y=names, zmin=0.5, zmax=n_lv + 0.5,
            colorscale=scale, showscale=False, xgap=2, ygap=2,
            text=piv.map(lambda s: "" if pd.isna(s) else str(int(s))).values, texttemplate="%{text}",
            textfont=dict(size=11, color="#0b0b0b"),
            hovertemplate="%{y}<br>неделя с %{x}: балл %{text}<extra></extra>"))
        fig.update_layout(height=max(260, 26 * len(piv) + 60), margin=dict(l=4, r=4, t=4, b=4),
                          yaxis=dict(autorange="reversed", fixedrange=True), xaxis=dict(side="top", fixedrange=True))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="dyn_ribbon")

    st.subheader(f"Изменения за период: {rules.week_label(weeks[0])} → {rules.week_label(weeks[-1])}")
    a = snaps[snaps["week"] == weeks[0]][["risk_id", "score"]].rename(columns={"score": "Было"})
    b = snaps[snaps["week"] == weeks[-1]][["risk_id", "score"]].rename(columns={"score": "Стало"})
    m = a.merge(b, on="risk_id", how="outer").merge(reg[["id", "code", "title", "owner"]], left_on="risk_id", right_on="id")
    m["Δ"] = m["Стало"].fillna(0) - m["Было"].fillna(0)
    m["Что произошло"] = ["новый" if pd.isna(x) else "ушёл с карты" if pd.isna(y) else "↑" if y > x else "↓" if y < x else "без изменений"
                          for x, y in zip(m["Было"], m["Стало"])]
    m = m[m["Что произошло"] != "без изменений"].sort_values("Δ", ascending=False)
    cfg = {"code": "Код", "title": st.column_config.TextColumn("Риск", width="large"), "owner": "Владелец",
           "Было": st.column_config.NumberColumn(format="%d"), "Стало": st.column_config.NumberColumn(format="%d"),
           "Δ": st.column_config.NumberColumn(format="%+d")}
    st.dataframe(m[["code", "title", "owner", "Было", "Стало", "Δ", "Что произошло"]], hide_index=True, width="stretch", column_config=cfg)
    with st.expander("Таблица по неделям"):
        st.dataframe(summary.drop(columns=["Начало недели", "Ось"]), hide_index=True, width="stretch")
