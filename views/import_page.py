"""Импорт рисков из Word и Excel: источник → сопоставление колонок → предпросмотр и оценка → загрузка."""
import hashlib

import pandas as pd
import streamlit as st

from core import access, ai, config, export, importer, rules
from views import common

AI_BATCH = 25


def _parsed(name: str, data: bytes) -> importer.ParsedFile:
    sig = hashlib.sha1(data).hexdigest()
    cache = st.session_state.setdefault("imp_parsed", {})
    if sig not in cache:
        cache.clear()
        cache[sig] = importer.read_file(name, data)
    return cache[sig]


def _mapping_ui(table: pd.DataFrame, auto: dict, prefix: str) -> dict:
    cols = list(table.columns)
    opts = [None] + cols
    mapping = {}
    st.caption("Колонки сопоставлены автоматически по заголовкам — поправьте, если нужно.")
    grid = st.columns(4)
    for n, fld in enumerate(importer.FIELDS):
        key = f"{prefix}_{fld}"
        st.session_state.setdefault(key, auto.get(fld))
        mapping[fld] = grid[n % 4].selectbox(config.FIELD_LABELS[fld], opts, key=key, format_func=lambda c: c or "— нет —")
    used = {c for c in mapping.values() if c}
    key = f"{prefix}_extra"
    st.session_state.setdefault(key, [c for c in auto.get(importer.EXTRA, []) if c not in used])
    st.session_state[key] = [c for c in st.session_state[key] if c in cols and c not in used]
    mapping[importer.EXTRA] = st.multiselect("Дописать в описание (колонки без своего поля)", [c for c in cols if c not in used],
                                             key=key, placeholder="ничего")
    return mapping


def _assessment_label(r) -> str:
    if pd.isna(r["probability"]) or pd.isna(r["impact"]):
        return "нет"
    if r.get("ai"):
        return "ИИ (проверьте)"
    if r.get("level_based"):
        return "по уровню (условно)"
    return "из файла"


def render():
    d = common.load()
    common.sidebar_filters(d.reg)
    common.page_header("Импорт рисков из Word и Excel",
                       "Excel — таблица рисков на любом листе. Word — таблица или текст: заголовки, пункты списка «Название: описание», «Риск 1. …».")
    if not access.can_import(common.role()):
        st.info("Импорт доступен роли «Риск-менеджер».")
        return
    done = st.session_state.pop("imp_done", None)
    if done:
        with st.container(border=True):
            st.success(f"Загружено: создано {done['created']}, обновлено {done['updated']}, оценок добавлено {done['assessments']}, "
                       f"без оценки {done['unassessed']}.")
            b1, b2, _ = st.columns([1, 1, 2])
            if b1.button("Открыть тепловую карту", type="primary"):
                st.switch_page(common.PAGES["heatmap"])
            if b2.button("Открыть реестр"):
                st.switch_page(common.PAGES["register"])
    t1, t2, _ = st.columns([1, 1, 2])
    t1.download_button("Шаблон Excel", export.template_xlsx(), "шаблон_рисков.xlsx", icon=":material/table:", width="stretch")
    t2.download_button("Шаблон Word", export.template_docx(), "шаблон_рисков.docx", icon=":material/description:", width="stretch")

    up = st.file_uploader("Файл с рисками", type=["xlsx", "docx", "csv"], key=f"imp_file_{st.session_state.get('imp_n', 0)}")
    if up is None:
        st.caption("Что понимает импорт: колонки «Риск / Наименование», «Описание», «Вероятность», «Влияние / Последствия», "
                   "«Уровень риска», «Владелец / Ответственный», «Меры / Мероприятия», «Срок», «Категория», «Статус», «Дата оценки» "
                   "(несколько строк одного риска с разными датами = история). Оценки — числом 1–5, словами или процентом.")
        return
    data = up.getvalue()
    try:
        pf = _parsed(up.name, data)
    except Exception as e:  # повреждённый или неподдерживаемый файл
        st.error(f"Не удалось прочитать файл: {e}")
        return
    sources = list(pf.sources)
    ai_src = st.session_state.get("imp_ai_source")
    if ai_src is not None and ai_src[0] == hashlib.sha1(data).hexdigest():
        sources.insert(0, ai_src[1])
    key = common.api_key()
    if not sources:
        st.warning("В файле не найдено ни таблиц, ни структуры рисков.")
    else:
        names = [s.name for s in sources]
        idx = st.radio("Что взять из файла", range(len(sources)), format_func=lambda k: names[k], key=f"imp_src_{up.name}",
                       horizontal=len(sources) <= 3)
        src = sources[idx]
        st.caption(f"Как разобрано: {src.hint}" if src.hint else "")
        with st.expander("Исходные данные источника"):
            st.dataframe(src.raw.head(50), width="stretch", hide_index=True)
    if pf.text and key:
        with st.expander("Не нашлось нужного? Извлечь риски из текста с помощью ИИ"):
            st.caption(f"Текст документа ({len(pf.text):,} символов) будет отправлен в Claude API ({config.AI_MODEL}).".replace(",", " "))
            if len(pf.text) > 150000:
                st.warning("Документ длиннее 150 000 символов — будет обработано только начало.")
            if st.button("Извлечь риски ИИ", icon=":material/auto_awesome:"):
                try:
                    with st.spinner("Claude читает документ…"):
                        found = ai.extract_risks(key, pf.text)
                    df = pd.DataFrame([{"title": r["title"], "description": r["description"], "owner": r["owner"],
                                        "probability": r["probability"] or None, "impact": r["impact"] or None} for r in found])
                    st.session_state["imp_ai_source"] = (hashlib.sha1(data).hexdigest(), importer.Source(
                        f"ИИ: риски из текста ({len(df)})", "text", df, score=9, hint="извлечено Claude — проверьте каждый риск"))
                    st.session_state[f"imp_src_{up.name}"] = 0
                    st.rerun()
                except ai.AIError as e:
                    st.error(str(e))
    if not sources:
        return

    table = importer.table_from_source(src)
    prefix = f"map_{hashlib.sha1((up.name + src.name).encode()).hexdigest()[:8]}"
    auto = importer.auto_mapping(list(table.columns), table)
    if src.kind == "text":
        mapping = auto
    else:
        with st.expander("Сопоставление колонок", expanded=True):
            mapping = _mapping_ui(table, auto, prefix)
    if not mapping.get("title"):
        st.error("Не выбрана колонка с названием риска.")
        return
    sig = (prefix, tuple(sorted((k, str(v)) for k, v in mapping.items())))
    state = st.session_state.setdefault("imp_state", {})
    if state.get("sig") != sig:
        cands = importer.normalize(table, mapping)
        cands.insert(0, "include", True)
        cands["ai"] = False
        cands["rationale"] = ""
        state.clear()
        state.update(sig=sig, base=cands, ver=0)
    base: pd.DataFrame = state["base"]
    if base.empty:
        st.warning("После разбора не осталось ни одного риска. Проверьте, какая колонка содержит название.")
        return

    st.subheader(f"Предпросмотр: {len(base)} риск(ов)")
    upd = st.toggle("Обновлять существующие риски с тем же кодом или названием", value=True, key="imp_upd")
    # таблица строится из последней отредактированной версии, чтобы уровень и балл пересчитывались сразу
    cur = st.session_state.get(f"imp_editor_{state['ver']}_df", base)
    planned = importer.plan_import(cur.drop(columns=["include"]), common.get_db(), upd)
    view = cur.copy()
    view.insert(1, "action", planned["action"].values)
    view["assess"] = [_assessment_label(r) for _, r in view.iterrows()]
    view["score"] = [rules.score(p, i) for p, i in zip(view["probability"], view["impact"])]
    view["lvl"] = [rules.level_name(s, d.levels) for s in view["score"]]
    unassessed = view[view["probability"].isna() | view["impact"].isna() | view["level_based"]]
    m = st.columns(4)
    m[0].metric("Будет создано", int((view["action"] == "создать").sum()))
    m[1].metric("Будет обновлено", int((view["action"] == "обновить").sum()))
    m[2].metric("С оценкой из файла", int(((view["assess"] == "из файла")).sum()))
    m[3].metric("Без точной оценки", len(unassessed), help="нет В и Вл или они восстановлены по уровню")

    if len(unassessed):
        with st.container(border=True):
            st.markdown(f"**{len(unassessed)}** риск(ов) без точной оценки В × Вл. Варианты: поставить оценки в таблице ниже, "
                        "загрузить как есть (неоценённые не попадут на карту до оценки) или запросить подсказку ИИ.")
            if key:
                only_missing = st.checkbox("Только для рисков без оценки и с условной оценкой по уровню", value=True, key="imp_ai_only")
                if st.button("Предложить оценки ИИ", icon=":material/auto_awesome:", type="primary"):
                    edited = st.session_state.get(f"imp_editor_{state['ver']}_df")
                    if edited is not None:
                        base = edited
                    target = base[(base["probability"].isna() | base["impact"].isna() | base["level_based"])] if only_missing else base
                    items = [{"idx": int(k), "title": r["title"], "description": (r["description"] or "")
                              + (f"\nУровень риска в документе: {r['level']}" if r.get("level") else "")} for k, r in target.iterrows()]
                    try:
                        res = []
                        bar = st.progress(0.0, text="Запрашиваю Claude…")
                        for n in range(0, len(items), AI_BATCH):
                            res += ai.suggest_assessments(key, items[n:n + AI_BATCH])
                            bar.progress(min(1.0, (n + AI_BATCH) / len(items)), text=f"Оценено {min(n + AI_BATCH, len(items))} из {len(items)}")
                        base = base.copy()
                        for s in res:
                            k = s["idx"]
                            base.at[k, "probability"] = s["probability"]
                            base.at[k, "impact"] = s["impact"]
                            base.at[k, "ai"] = True
                            base.at[k, "level_based"] = False
                            base.at[k, "rationale"] = s["rationale"]
                            if not base.at[k, "category"]:
                                base.at[k, "category"] = s["category"]
                        state.update(base=base, ver=state["ver"] + 1)
                        st.rerun()
                    except ai.AIError as e:
                        st.error(str(e))
            else:
                st.caption("ИИ-подсказка выключена: нет ключа `ANTHROPIC_API_KEY` (переменная окружения или secrets Streamlit).")

    cols = ["include", "action", "code", "title", "description", "category", "owner", "probability", "impact", "lvl", "assess",
            "response_plan", "due_date", "issues", "rationale"]
    edited = st.data_editor(
        view[cols], key=f"imp_editor_{state['ver']}", hide_index=True, width="stretch", height=min(600, 38 + 35 * len(view)),
        disabled=["action", "lvl", "assess", "issues", "rationale"],
        column_config={
            "include": st.column_config.CheckboxColumn("✓", help="Загружать строку", width="small"),
            "action": st.column_config.TextColumn("Действие", width="small"),
            "code": st.column_config.TextColumn("Код", width="small"),
            "title": st.column_config.TextColumn("Риск", width="large", required=True),
            "description": st.column_config.TextColumn("Описание", width="medium"),
            "category": "Категория", "owner": "Владелец",
            "probability": st.column_config.NumberColumn("В", min_value=1, max_value=5, step=1, help="Вероятность 1–5", width="small"),
            "impact": st.column_config.NumberColumn("Вл", min_value=1, max_value=5, step=1, help="Влияние 1–5", width="small"),
            "lvl": "Уровень", "assess": "Оценка", "response_plan": st.column_config.TextColumn("Меры", width="medium"),
            "due_date": st.column_config.DateColumn("Срок мер", format="DD.MM.YYYY"),
            "issues": st.column_config.TextColumn("Замечания", width="medium"),
            "rationale": st.column_config.TextColumn("Обоснование ИИ", width="medium"),
        })
    full = base.copy()
    for c in ["include", "code", "title", "description", "category", "owner", "probability", "impact", "response_plan", "due_date"]:
        full[c] = edited[c].values
    # ручная правка оценки снимает пометку «по уровню», совпадение с подсказкой ИИ — оставляет источник «ИИ»
    changed = (full["probability"].astype("Int64") != base["probability"].astype("Int64")).fillna(True) | \
              (full["impact"].astype("Int64") != base["impact"].astype("Int64")).fillna(True)
    full.loc[changed, "level_based"] = False
    full.loc[changed, "ai"] = False
    st.session_state[f"imp_editor_{state['ver']}_df"] = full
    sel = full[full["include"]].copy()
    sel["_source"] = ["ai" if a else "import" for a in sel["ai"]]
    sel["comment"] = [("ИИ: " + r)[:300] if a and r else c for a, r, c in zip(sel["ai"], sel["rationale"], sel["comment"])]
    c1, c2 = st.columns([1, 2])
    on = c1.date_input("Дата оценки для строк без даты", value=common.today(), max_value=common.today(), format="DD.MM.YYYY", key="imp_date")
    sel["assessed_on"] = [x if rules.to_date(x) else on for x in sel["assessed_on"]]
    db = common.get_db()
    clear_demo = bool(db.get_setting("demo")) and c2.checkbox(
        "Перед загрузкой удалить демо-данные", value=True, key="imp_clear_demo",
        help="Сейчас в реестре условный демо-проект. Снимите галочку, чтобы загрузить риски рядом с ним.")
    if st.button(f"Загрузить в реестр: {len(sel)}", type="primary", icon=":material/upload:", disabled=sel.empty):
        if clear_demo:
            db.clear_all(common.actor(), common.role())
        stats = importer.apply_import(sel.drop(columns=["include", "ai", "rationale"]), db, common.actor(), common.role(),
                                      up.name, update_existing=upd, today=common.today())
        st.session_state.pop("imp_state", None)
        st.session_state["imp_done"] = stats
        st.session_state["imp_n"] = st.session_state.get("imp_n", 0) + 1  # очистить загрузчик
        st.rerun()
