"""Карточка риска: сведения, переоценка, история, траектория на карте, ИИ-подсказка, редактирование."""
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import access, ai, config, rules
from views import common


def _trajectory(hist: pd.DataFrame, risk: dict, levels) -> go.Figure:
    c = common.ink()
    fig = go.Figure()
    for p in range(1, 6):
        for i in range(1, 6):
            lv = rules.level_for(p * i, levels)
            fig.add_shape(type="rect", x0=i - 0.5, x1=i + 0.5, y0=p - 0.5, y1=p + 0.5, layer="below",
                          line=dict(color=c["surface"], width=2), fillcolor=common._rgba(lv["color"], 0.42 if c["dark"] else 0.32))
    if not hist.empty:
        h = hist.sort_values(["assessed_on", "id"])
        # сдвигаем совпадающие позиции, чтобы путь был виден
        seen: dict = {}
        xs, ys = [], []
        for r in h.itertuples():
            k = (r.probability, r.impact)
            n = seen.get(k, 0)
            seen[k] = n + 1
            xs.append(r.impact + (n % 3 - 1) * 0.12)
            ys.append(r.probability + (n // 3) * 0.1 - 0.05)
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", line=dict(color=c["muted"], width=2),
                                 marker=dict(size=[7] * (len(xs) - 1) + [14], color=[rules.level_color(p * i, levels)
                                             for p, i in zip(h["probability"], h["impact"])], line=dict(color=c["text"], width=1.5)),
                                 customdata=[common.fmt_date(x) for x in h["assessed_on"]],
                                 hovertemplate="%{customdata}: В %{y:.0f} × Вл %{x:.0f}<extra></extra>", showlegend=False))
    if risk.get("target_p") and risk.get("target_i"):
        fig.add_trace(go.Scatter(x=[risk["target_i"]], y=[risk["target_p"]], mode="markers+text", text=["цель"],
                                 textposition="top center", textfont=dict(color=c["text"], size=11),
                                 marker=dict(size=14, symbol="diamond-open", color=c["text"], line=dict(width=2)),
                                 hovertemplate="Целевое положение<extra></extra>", showlegend=False))
    fig.update_layout(height=330, margin=dict(l=4, r=4, t=4, b=4), dragmode=False,
                      xaxis=dict(title="Влияние", range=[0.5, 5.5], tickvals=list(range(1, 6)), showgrid=False, zeroline=False, fixedrange=True),
                      yaxis=dict(title="Вероятность", range=[0.5, 5.5], tickvals=list(range(1, 6)), showgrid=False, zeroline=False, fixedrange=True))
    return fig


def _score_by_week(d, risk_id: int) -> go.Figure:
    weeks = common.week_options(d)
    snaps = rules.weekly_snapshots(d.risks[d.risks["id"] == risk_id], d.assessments[d.assessments["risk_id"] == risk_id], weeks)
    c = common.ink()
    fig = go.Figure()
    for lv in sorted(d.levels, key=lambda x: x["min"]):  # полосы уровней
        upper = min([x["min"] for x in d.levels if x["min"] > lv["min"]] or [26]) - 0.5
        fig.add_hrect(y0=lv["min"] - 0.5, y1=upper, fillcolor=common._rgba(lv["color"], 0.16), line_width=0, layer="below")
    if not snaps.empty:
        fig.add_trace(go.Scatter(x=[rules.week_short(w) for w in snaps["week"]], y=snaps["score"], mode="lines+markers",
                                 line=dict(color=c["text"], width=2), marker=dict(size=8),
                                 hovertemplate="неделя с %{x}: балл %{y}<extra></extra>", showlegend=False))
    fig.update_layout(height=260, margin=dict(l=4, r=4, t=4, b=4), yaxis=dict(title="Балл", range=[0, 26], fixedrange=True),
                      xaxis=dict(fixedrange=True, type="category"))
    return fig


def render():
    d = common.load()
    common.sidebar_filters(d.reg)
    if d.risks.empty:
        common.page_header("Карточка риска")
        st.info("Реестр пуст.")
        return
    opts = d.reg.sort_values("code")
    ids = opts["id"].astype(int).tolist()
    labels = {int(r.id): f"{r.code} · {r.title}" for r in opts.itertuples()}
    want = st.session_state.pop("card_risk_id", None)
    if want in ids:
        st.session_state["card_sel"] = want
    if st.session_state.get("card_sel") not in ids:
        st.session_state["card_sel"] = ids[0]
    rid = st.selectbox("Риск", ids, format_func=labels.get, key="card_sel", label_visibility="collapsed")
    row = d.reg[d.reg["id"] == rid].iloc[0]
    risk = common.get_db().risk(rid)
    hist = d.assessments[d.assessments["risk_id"] == rid]
    editable = common.can_edit_risk(risk)

    common.page_header(f"{row['code']} · {row['title']}")
    trend = f" · тренд {row['trend']}" if row["trend"] else ""
    st.markdown(f"{common.level_chip(row['score'], d.levels)} **{row['status']}** · владелец: {row['owner'] or '—'} · "
                f"категория: {row['category'] or '—'}{trend}")
    if not editable:
        st.caption("Только просмотр: " + ("вы не владелец этого риска" if common.role() == "Владелец риска" else "роль «Наблюдатель»"))

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("**Описание**")
        st.write(risk["description"] or "_не заполнено_")
        info = {
            "Стратегия": risk["strategy"] or "—", "Меры": risk["response_plan"] or "—",
            "Срок мер": common.fmt_date(risk["due_date"]), "Триггер": risk["trigger"] or "—",
            "Текущая оценка": f"В {row['p']} × Вл {row['i']} = {row['score']}" if pd.notna(row["score"]) else "нет",
            "Целевая оценка": f"В {risk['target_p']} × Вл {risk['target_i']} = {risk['target_p'] * risk['target_i']}" if risk["target_p"] and risk["target_i"] else "не задана",
            "Источник": config.SOURCES.get(risk["source"], risk["source"]), "Выявлен": common.fmt_date(risk["created_on"]),
        }
        st.table(pd.DataFrame({"Поле": list(info), "Значение": list(info.values())}).set_index("Поле"))
    with right:
        st.markdown("**Траектория на карте**")
        st.plotly_chart(_trajectory(hist, risk, d.levels), width="stretch", config={"displayModeBar": False}, key="card_traj")
        st.caption("Путь от первой оценки (маленькие точки) к текущей (крупная); ромб — целевое положение.")

    t_assess, t_hist, t_ai, t_edit = st.tabs(["Переоценить", "История", "ИИ-подсказка", "Редактировать"])
    with t_assess:
        if not editable:
            st.info("Переоценка доступна риск-менеджеру и владельцу риска.")
        else:
            with st.form(f"assess_{rid}"):
                c = st.columns(2)
                cur_p = int(row["p"]) if pd.notna(row["p"]) else 3
                cur_i = int(row["i"]) if pd.notna(row["i"]) else 3
                p = c[0].radio("Вероятность", list(range(1, 6)), index=cur_p - 1,
                               format_func=lambda v: f"{v} — {config.PROBABILITY_SCALE[v][0]} ({config.PROBABILITY_SCALE[v][1]})")
                i = c[1].radio("Влияние", list(range(1, 6)), index=cur_i - 1,
                               format_func=lambda v: f"{v} — {config.IMPACT_SCALE[v][0]}",
                               captions=[config.IMPACT_SCALE[v][1] for v in range(1, 6)])
                c2 = st.columns([1, 3])
                on = c2[0].date_input("Дата оценки", value=date.today(), max_value=date.today(), format="DD.MM.YYYY")
                comment = c2[1].text_input("Комментарий", placeholder="Что изменилось с прошлой оценки")
                if st.form_submit_button("Сохранить оценку", type="primary"):
                    common.get_db().add_assessment(rid, p, i, on, comment, common.actor(), "manual", common.role())
                    st.toast(f"Оценка сохранена: {p} × {i} = {p * i}", icon=":material/check:")
                    st.rerun()
    with t_hist:
        st.plotly_chart(_score_by_week(d, rid), width="stretch", config={"displayModeBar": False}, key="card_weeks")
        if hist.empty:
            st.write("Оценок пока нет.")
        else:
            h = hist.sort_values(["assessed_on", "id"], ascending=False).copy()
            h["score"] = h["probability"] * h["impact"]
            h["level"] = [rules.level_name(s, d.levels) for s in h["score"]]
            h["source"] = h["source"].map(lambda s: config.SOURCES.get(s, s))
            st.dataframe(h[["assessed_on", "probability", "impact", "score", "level", "comment", "author", "source"]], hide_index=True,
                         width="stretch", column_config={
                             "assessed_on": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"), "probability": "В",
                             "impact": "Вл", "score": "Балл", "level": "Уровень", "comment": "Комментарий", "author": "Автор",
                             "source": "Источник"})
    with t_ai:
        key = common.api_key()
        if not key:
            st.info("ИИ-подсказка выключена: задайте ключ `ANTHROPIC_API_KEY` в переменных окружения или в secrets Streamlit.")
        elif not editable:
            st.info("Доступно риск-менеджеру и владельцу риска.")
        else:
            st.caption(f"Модель {config.AI_MODEL} предлагает оценку по названию и описанию. Оценка попадёт в историю только после подтверждения.")
            if st.button("Предложить оценку", icon=":material/auto_awesome:"):
                try:
                    with st.spinner("Запрашиваю Claude…"):
                        res = ai.suggest_assessments(key, [{"idx": 0, "title": risk["title"], "description": risk["description"]}])
                    st.session_state[f"ai_{rid}"] = res[0] if res else None
                except ai.AIError as e:
                    st.error(str(e))
            sug = st.session_state.get(f"ai_{rid}")
            if sug:
                st.markdown(f"Предложение: **В {sug['probability']} × Вл {sug['impact']} = {sug['probability'] * sug['impact']}** "
                            f"({rules.level_name(sug['probability'] * sug['impact'], d.levels)}), категория «{sug['category']}».")
                st.write(sug["rationale"])
                if st.button("Принять оценку", type="primary"):
                    common.get_db().add_assessment(rid, sug["probability"], sug["impact"], comment="ИИ: " + sug["rationale"][:300],
                                                   author=common.actor(), source="ai", role=common.role())
                    if not risk["category"] and sug["category"]:
                        common.get_db().update_risk(rid, {"category": sug["category"]}, common.actor(), common.role())
                    st.session_state.pop(f"ai_{rid}", None)
                    st.rerun()
    with t_edit:
        if not editable:
            st.info("Редактирование доступно риск-менеджеру и владельцу риска.")
        else:
            _edit_form(rid, risk)


def _edit_form(rid: int, risk: dict):
    is_rm = common.role() == "Риск-менеджер"
    with st.form(f"edit_{rid}"):
        title = st.text_input("Название", value=risk["title"])
        desc = st.text_area("Описание", value=risk["description"], height=120)
        c = st.columns(3)
        cats = [""] + sorted(set(config.CATEGORIES) | ({risk["category"]} if risk["category"] else set()))
        cat = c[0].selectbox("Категория", cats, index=cats.index(risk["category"] or ""), format_func=lambda x: x or "—")
        owner = c[1].text_input("Владелец", value=risk["owner"], disabled=not is_rm)
        status = c[2].selectbox("Статус", config.STATUSES, index=config.STATUSES.index(risk["status"]) if risk["status"] in config.STATUSES else 0)
        c = st.columns(3)
        strategies = [""] + config.STRATEGIES
        strategy = c[0].selectbox("Стратегия", strategies, index=strategies.index(risk["strategy"]) if risk["strategy"] in strategies else 0,
                                  format_func=lambda x: x or "—")
        due = c[1].date_input("Срок мер", value=rules.to_date(risk["due_date"]), format="DD.MM.YYYY")
        trigger = c[2].text_input("Триггер", value=risk["trigger"])
        plan = st.text_area("Меры реагирования", value=risk["response_plan"])
        c = st.columns(2)
        tp = c[0].selectbox("Целевая вероятность", [None, 1, 2, 3, 4, 5], index=[None, 1, 2, 3, 4, 5].index(risk["target_p"]),
                            format_func=lambda v: "—" if v is None else f"{v} — {config.PROBABILITY_SCALE[v][0]}")
        ti = c[1].selectbox("Целевое влияние", [None, 1, 2, 3, 4, 5], index=[None, 1, 2, 3, 4, 5].index(risk["target_i"]),
                            format_func=lambda v: "—" if v is None else f"{v} — {config.IMPACT_SCALE[v][0]}")
        if st.form_submit_button("Сохранить изменения", type="primary"):
            changes = {"title": title.strip() or risk["title"], "description": desc, "category": cat, "status": status,
                       "strategy": strategy, "due_date": due, "trigger": trigger, "response_plan": plan, "target_p": tp, "target_i": ti}
            if is_rm:
                changes["owner"] = owner
            diff = common.get_db().update_risk(rid, changes, common.actor(), common.role())
            st.toast("Сохранено" if diff else "Изменений нет", icon=":material/check:")
            st.rerun()
    if access.can_configure(common.role()):
        with st.popover("Удалить риск", icon=":material/delete:"):
            st.write("Риск и вся история оценок будут удалены. Действие попадёт в журнал.")
            if st.button("Да, удалить", type="primary", key=f"del_{rid}"):
                common.get_db().delete_risk(rid, common.actor(), common.role())
                st.session_state.pop("card_sel", None)
                st.switch_page(common.PAGES["register"])
