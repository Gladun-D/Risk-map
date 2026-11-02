"""Методика, шкалы, пороги уровней, ограничения матриц, источники и управление данными."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import access, config
from core.seed import seed_demo
from views import common

SOURCES = [
    ("ГОСТ Р ИСО 31000-2019 «Менеджмент риска. Принципы и руководство» (идентичен ISO 31000:2018)", "https://www.normacs.info/ntds/11424"),
    ("ГОСТ Р 58771-2019 «Менеджмент риска. Технологии оценки риска» (на основе IEC 31010:2019)", "https://www.normacs.info/ntds/11447"),
    ("Типовые колонки реестра рисков", "https://resources.rework.com/vi/libraries/project-management/risk-register"),
    ("Критика матриц рисков (Cox, 2008 и др.)", "https://en.wikipedia.org/wiki/Risk_matrix"),
    ("Движение рисков на тепловой карте", "https://www.smartsuite.com/what-is/what-is-a-risk-heat-map"),
    ("Расчёт стрелок тренда «до начала / до конца периода» (CAMMS)", "https://camms.atlassian.net/wiki/spaces/CD/pages/166592818"),
]


def render():
    d = common.load()
    common.sidebar_filters(d.reg)
    common.page_header("Методика и настройки")
    st.markdown(
        "Процесс — по ГОСТ Р ИСО 31000-2019: **идентификация → анализ → оценивание → обработка → мониторинг и пересмотр**. "
        "Стандарт не задаёт формат реестра и шкалы — это решение организации, поэтому пороги ниже настраиваются.\n\n"
        "- **Балл риска** = вероятность (1–5) × влияние (1–5), от 1 до 25.\n"
        "- **Положение на неделю** = последняя оценка, сделанная не позднее конца недели (пн–вс). Если риск в неделю не "
        "переоценивали, переносится прошлая оценка. Закрытые риски выбывают с даты закрытия.\n"
        "- **Тренд** — сравнение текущего балла с баллом на конец прошлой недели: ↑ вырос, ↓ снизился, → без изменений.\n"
        "- **Оценка по уровню**: если в файле только «Уровень риска», риск условно ставится на диагональ карты "
        "(низкий 2×2, средний 3×3, высокий 4×4, критический 5×5) с пометкой — уточните В и Вл.")
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Шкала вероятности")
        st.table(pd.DataFrame([(k, v[0], v[1]) for k, v in config.PROBABILITY_SCALE.items()],
                              columns=["Балл", "Название", "Вероятность"]).set_index("Балл"))
    with c2:
        st.subheader("Шкала влияния")
        st.table(pd.DataFrame([(k, v[0], v[1]) for k, v in config.IMPACT_SCALE.items()],
                              columns=["Балл", "Название", "Критерии"]).set_index("Балл"))
    st.caption("Формулировки шкал — типовые (допущение прототипа). Перед использованием согласуйте их с владельцами рисков.")

    st.subheader("Пороги уровней")
    levels = sorted(d.levels, key=lambda x: x["min"])
    editable = access.can_configure(common.role())
    with st.form("levels"):
        cols = st.columns(len(levels))
        mins = []
        for col, lv in zip(cols, levels):
            mins.append(col.number_input(f"{lv['name']}: от", min_value=1, max_value=25, value=int(lv["min"]), step=1,
                                         disabled=not editable or lv["min"] == 1, key=f"lv_{lv['code']}"))
        if st.form_submit_button("Сохранить пороги", disabled=not editable):
            if mins != sorted(mins) or len(set(mins)) != len(mins) or mins[0] != 1:
                st.error("Пороги должны строго возрастать, первый уровень начинается с 1.")
            else:
                common.get_db().set_setting("levels", [{**lv, "min": m} for lv, m in zip(levels, mins)])
                common.get_db().audit("Изменены пороги уровней", common.actor(), common.role(), None, {"пороги": mins})
                st.rerun()
    if editable and st.button("Вернуть пороги по умолчанию"):
        common.get_db().set_setting("levels", config.DEFAULT_LEVELS)
        st.rerun()
    st.caption("По умолчанию: низкий 1–4, средний 5–9, высокий 10–16, критический 20–25 (распространённое соглашение, не норма стандарта).")

    st.subheader("Ограничения матрицы рисков")
    st.markdown(
        "- Шкалы порядковые: балл 12 не «вдвое хуже» балла 6; разные по величине риски могут попасть в одну ячейку.\n"
        "- Словесные метки люди понимают по-разному — поэтому рядом с полем оценки показаны критерии, а к оценке пишется комментарий.\n"
        "- Оценки часто скучиваются в середине шкалы (2–4). Проверьте распределение ниже: если почти всё в 3, шкалы стоит уточнить.")
    open_ = d.reg[d.reg["is_open"] & d.reg["score"].notna()]
    if not open_.empty:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=list(range(1, 6)), y=[int((open_["p"] == k).sum()) for k in range(1, 6)], name="Вероятность",
                             marker_color="#2a78d6", marker_line=dict(color=common.ink()["surface"], width=2)))
        fig.add_trace(go.Bar(x=list(range(1, 6)), y=[int((open_["i"] == k).sum()) for k in range(1, 6)], name="Влияние",
                             marker_color="#eb6834", marker_line=dict(color=common.ink()["surface"], width=2)))
        fig.update_layout(barmode="group", height=260, margin=dict(l=4, r=4, t=4, b=4), legend=dict(orientation="h", y=-0.25),
                          xaxis=dict(title="Балл шкалы", tickvals=list(range(1, 6)), fixedrange=True), yaxis=dict(title="Рисков", fixedrange=True))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="meth_dist")

    st.subheader("Источники")
    for name, url in SOURCES:
        st.markdown(f"- [{name}]({url})")
    st.caption("Полные тексты ГОСТ платные и в прототипе не цитируются; ссылки — на карточки стандартов. Подробнее — docs/RESEARCH.md.")

    st.subheader("Данные")
    st.markdown("Данные хранятся в файле SQLite на сервере. На Streamlit Community Cloud он **сбрасывается при перезапуске** "
                "приложения — для постоянной работы нужна внешняя СУБД. Выгружайте реестр в Excel.")
    if not editable:
        return
    a, b = st.columns(2)
    with a.popover("Очистить реестр", icon=":material/delete_sweep:"):
        st.write("Будут удалены все риски и история оценок. Журнал изменений сохранится.")
        if st.button("Да, очистить", type="primary", key="clear_all"):
            common.get_db().clear_all(common.actor(), common.role())
            st.rerun()
    with b.popover("Загрузить демо-проект", icon=":material/science:"):
        st.write("Текущий реестр будет заменён условным проектом «Внедрение ERP-системы» с историей за 12 недель.")
        if st.button("Да, загрузить демо", type="primary", key="load_demo"):
            db = common.get_db()
            db.clear_all(common.actor(), common.role())
            seed_demo(db)
            st.rerun()
