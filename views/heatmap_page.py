"""Тепловая карта на выбранную неделю со стрелками движения и сравнением недель."""
from dataclasses import replace
from datetime import timedelta

import pandas as pd
import streamlit as st

from core import rules
from views import common


def _points(d, reg, as_of) -> pd.DataFrame:
    snap = rules.snapshot_at(d.risks, d.assessments, as_of)
    pts = snap.rename(columns={"risk_id": "id", "probability": "p", "impact": "i"})
    meta = reg[["id", "code", "title", "owner", "category", "status", "trend", "description"]]
    pts = pts.merge(meta, on="id", how="inner")
    pts["level"] = [rules.level_name(s, d.levels) for s in pts["score"]]
    return pts


def render():
    d = common.load()
    f = common.sidebar_filters(d.reg)
    # статус и уровень зависят от недели: открытость берётся из снимка, уровень — из оценки на ту неделю
    reg = common.apply_filters(d.reg, replace(f, statuses=(), levels=()))
    common.page_header("Тепловая карта рисков", "Положение открытых рисков на конец недели (для текущей — на сегодня): последняя оценка до этой даты. "
                       "Сравнение «неделя назад» — с положением на 7 дней раньше. Фильтр по статусу здесь не применяется.")
    weeks = common.week_options(d)
    labels = {w: rules.week_label(w) for w in weeks}
    c1, c2, c3 = st.columns([5, 5, 3], vertical_alignment="bottom")
    week = c1.select_slider("Неделя", options=weeks, value=weeks[-1], format_func=labels.get, key="hm_week")
    mode = c2.segmented_control("Сравнение", ["неделя назад", "другая неделя", "нет"],
                                default="неделя назад", key="hm_mode")
    at = rules.week_snapshot_date(week, common.today())
    base_at = at - timedelta(days=7)
    if mode == "другая неделя":
        older = [w for w in weeks if w < week] or [week]
        base_week = c3.selectbox("Базовая неделя", older, index=0, format_func=labels.get, key="hm_base")
        base_at = rules.week_snapshot_date(base_week, common.today())
    show_codes = c3.toggle("Коды на карте", value=True, key="hm_codes") if mode != "другая неделя" else True

    pts = _points(d, reg, at)
    if f.levels:
        pts = pts[pts["level"].isin(f.levels)]
    prev = None
    if mode not in ("нет", None):
        prev = _points(d, reg, base_at)[["id", "p", "i", "score"]]
    note = f"На {common.fmt_date(at)} ({labels[week]})" + (f" · сравнение с {common.fmt_date(base_at)}" if prev is not None else "")

    left, right = st.columns([3, 2], gap="large")
    with left:
        sel = common.heatmap(pts, d.levels, prev=prev, show_codes=show_codes, key="hm_map", title_note=note)
    with right:
        cell = st.session_state.pop("map_cell", None)
        if isinstance(sel, int):
            r = pts[pts["id"] == sel].iloc[0]
            st.subheader(f"{r['code']} · {r['title']}")
            st.markdown(f"{common.level_chip(r['score'], d.levels)} В {r['p']} × Вл {r['i']} · владелец: {r['owner'] or '—'}")
            st.write(r["description"] or "_Описание не заполнено_")
            if st.button("Открыть карточку", type="primary"):
                common.open_card(sel)
        else:
            if isinstance(sel, tuple):
                cell = sel[1:]
            if cell:
                p, i = cell
                sub = pts[(pts["p"] == p) & (pts["i"] == i)]
                st.subheader(f"Ячейка: вероятность {p} × влияние {i}")
                st.caption(f"Балл {p * i} — {rules.level_name(p * i, d.levels)}")
            else:
                sub = pts
                st.subheader("Риски на карте")
            if sub.empty:
                st.write("Рисков нет")
            else:
                show = sub.sort_values("score", ascending=False)
                if prev is not None:
                    show = show.merge(prev[["id", "score"]].rename(columns={"score": "base"}), on="id", how="left")
                    show["Δ"] = show["score"] - show["base"]
                cols = ["code", "title", "owner", "score", "level"] + (["Δ"] if prev is not None else [])
                st.dataframe(show[cols], hide_index=True, width="stretch", height=420, column_config={
                    "code": "Код", "title": st.column_config.TextColumn("Риск", width="large"), "owner": "Владелец",
                    "score": st.column_config.NumberColumn("Балл", format="%d"), "level": "Уровень",
                    "Δ": st.column_config.NumberColumn("Δ", help="Изменение балла к базовой неделе", format="%+d")})
        if prev is not None and not pts.empty:
            m = pts.merge(prev, on="id", how="left", suffixes=("", "_b"))
            up = int((m["score"] > m["score_b"]).sum())
            down = int((m["score"] < m["score_b"]).sum())
            new = int(m["score_b"].isna().sum())
            gone = len(set(prev["id"]) - set(pts["id"]))
            st.caption(f"С {common.fmt_date(base_at)}: ↑ выросли {up} · ↓ снизились {down} · новые {new} · ушли с карты {gone}")
    not_assessed = reg[reg["is_open"] & reg["score"].isna()]
    if len(not_assessed) and week == weeks[-1]:
        st.warning(f"Не на карте — нет оценки: {len(not_assessed)} риск(ов): "
                   + ", ".join(not_assessed["code"].head(12)) + (" …" if len(not_assessed) > 12 else ""))
