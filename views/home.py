"""Обзор: что требует внимания сегодня."""
import streamlit as st

from core import config, rules
from views import common

SIGNALS = [
    ("critical", "Критические", "балл в зоне «Критический»"),
    ("grown", "Выросли за неделю", "балл выше, чем на конец прошлой недели"),
    ("overdue", "Просрочены меры", "срок мер прошёл"),
    ("due_soon", f"Срок мер ≤ {config.DUE_SOON_DAYS} дн.", "срок мер в ближайшие дни"),
    ("unassessed", "Без оценки", "нет ни одной оценки В × Вл"),
    ("no_owner", "Без владельца", "не назначен ответственный"),
    ("stale", f"Не пересматривались > {config.STALE_DAYS} дн.", "последняя оценка давно"),
]


def render():
    d = common.load()
    f = common.sidebar_filters(d.reg)
    reg = common.apply_filters(d.reg, f)
    common.page_header("Обзор рисков", f"На {common.fmt_date(common.today())} · открытых рисков в выборке: {int(reg['is_open'].sum())}")
    if d.risks.empty:
        st.info("Реестр пуст. Загрузите Word или Excel на странице «Импорт» или добавьте риск в «Реестре».")
        if st.button("Перейти к импорту", type="primary"):
            st.switch_page(common.PAGES["import"])
        return
    att = rules.attention(reg, common.today(), d.levels)
    open_ = reg[reg["is_open"]]
    exposure = int(open_["score"].fillna(0).sum())
    prev_exposure = int(open_["prev_score"].fillna(0).sum())
    k = st.columns(4)
    k[0].metric("Открытых рисков", len(open_), border=True)
    k[1].metric("Критических", len(att["critical"]), border=True)
    k[2].metric("Суммарный балл", exposure, delta=exposure - prev_exposure, delta_color="inverse", border=True,
                help="Сумма баллов открытых рисков; дельта — к концу прошлой недели. Рост — плохо.")
    k[3].metric("Выросли за неделю", len(att["grown"]), border=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Карта текущих рисков")
        prev = open_[["id", "prev_p", "prev_i"]].rename(columns={"prev_p": "p", "prev_i": "i"})
        sel = common.heatmap(open_, d.levels, prev=prev, height=480, key="home_map")
        if isinstance(sel, int):
            common.open_card(sel)
        elif isinstance(sel, tuple):
            st.session_state["map_cell"] = sel[1:]
            st.switch_page(common.PAGES["heatmap"])
    with right:
        st.subheader("Требует внимания")
        for code, label, hint in SIGNALS:
            df = att[code]
            with st.expander(f"{label} — **{len(df)}**", expanded=code in ("critical", "overdue") and len(df) > 0):
                st.caption(hint)
                if df.empty:
                    st.write("Нет")
                    continue
                for _, r in df.sort_values("score", ascending=False, na_position="last").head(15).iterrows():
                    c1, c2 = st.columns([6, 1], vertical_alignment="center")
                    extra = f" · срок {common.fmt_date(r['due_date'])}" if code in ("overdue", "due_soon") else ""
                    c1.markdown(f"**{r['code']}** {r['title']}  \n{common.level_chip(r['score'], d.levels)} "
                                f"{r['owner'] or '—'}{extra}")
                    if c2.button("", icon=":material/open_in_new:", key=f"att_{code}_{r['id']}", help="Открыть карточку"):
                        common.open_card(r["id"])
