"""Реестр рисков: таблица, добавление, выгрузка в Excel."""
from datetime import date

import streamlit as st

from core import access, config, export, rules
from views import common


def render():
    d = common.load()
    f = common.sidebar_filters(d.reg)
    reg = common.apply_filters(d.reg, f)
    common.page_header("Реестр рисков", "Выберите строку, чтобы открыть карточку риска.")
    top = st.columns([2, 2, 2, 3])
    top[0].metric("В выборке", len(reg))
    top[1].metric("Оценено", int(reg["score"].notna().sum()))
    top[2].metric("Без оценки", int(reg["score"].isna().sum()))
    with top[3]:
        weeks = common.week_options(d)
        snaps = rules.weekly_snapshots(d.risks, d.assessments, weeks)
        hist = d.assessments.merge(d.risks[["id", "code", "title"]], left_on="risk_id", right_on="id", how="left")
        hist = hist[["code", "title", "assessed_on", "probability", "impact", "comment", "author", "source"]].rename(columns={
            "code": "Код", "title": "Риск", "assessed_on": "Дата", "probability": "Вероятность", "impact": "Влияние",
            "comment": "Комментарий", "author": "Автор", "source": "Источник"})
        st.download_button("Выгрузить в Excel", data=export.register_to_excel(
            reg.sort_values("code"), hist, rules.weekly_summary(snaps, weeks, d.levels).drop(columns=["Ось"]), d.levels),
            file_name=f"реестр_рисков_{date.today():%Y%m%d}.xlsx", icon=":material/download:", width="stretch")
        st.download_button("Выгрузить CSV", data=reg.drop(columns=["level_rank", "is_open"]).to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"реестр_рисков_{date.today():%Y%m%d}.csv", icon=":material/csv:", width="stretch")
    order = reg.sort_values(["level_rank", "score", "code"], ascending=[False, False, True])
    sel = common.risk_table(order, d.levels, key="reg_table", select=True, height=min(640, 38 + 35 * max(len(order), 1)))
    if sel:
        common.open_card(sel)

    can_add = common.role() in ("Риск-менеджер", "Владелец риска")
    with st.expander("Добавить риск", icon=":material/add:", expanded=False):
        if not can_add:
            st.info("Наблюдатель не может добавлять риски.")
            return
        with st.form("add_risk", clear_on_submit=True):
            title = st.text_input("Название риска *", placeholder="Например: Задержка поставки оборудования")
            desc = st.text_area("Описание", placeholder="Причина → событие → последствие")
            c = st.columns(3)
            cat = c[0].selectbox("Категория", [""] + config.CATEGORIES, format_func=lambda x: x or "—")
            owners = sorted({o for o in d.risks["owner"] if o})
            owner_default = common.me() if common.role() == "Владелец риска" else ""
            owner = c[1].text_input("Владелец", value=owner_default, help=("Существующие: " + ", ".join(owners[:12])) if owners else None,
                                    disabled=common.role() == "Владелец риска")
            strategy = c[2].selectbox("Стратегия", [""] + config.STRATEGIES, format_func=lambda x: x or "—")
            c2 = st.columns(3)
            assess = c2[0].toggle("Сразу оценить", value=True)
            p = c2[1].select_slider("Вероятность", options=list(range(1, 6)), value=3,
                                    format_func=lambda v: f"{v} — {config.PROBABILITY_SCALE[v][0]}")
            i = c2[2].select_slider("Влияние", options=list(range(1, 6)), value=3,
                                    format_func=lambda v: f"{v} — {config.IMPACT_SCALE[v][0]}")
            plan = st.text_area("Меры реагирования")
            due = st.date_input("Срок мер", value=None, format="DD.MM.YYYY")
            if st.form_submit_button("Добавить", type="primary"):
                if not title.strip():
                    st.error("Укажите название риска.")
                else:
                    rid = common.get_db().create_risk({"title": title, "description": desc, "category": cat, "owner": owner,
                                                       "strategy": strategy, "response_plan": plan, "due_date": due},
                                                      common.actor(), common.role())
                    if assess:
                        common.get_db().add_assessment(rid, p, i, comment="Первичная оценка", author=common.actor(), role=common.role())
                    st.toast(f"Риск {common.get_db().risk(rid)['code']} добавлен", icon=":material/check:")
                    st.rerun()
    if not access.can_import(common.role()):
        st.caption("Импорт из Word/Excel доступен риск-менеджеру.")
