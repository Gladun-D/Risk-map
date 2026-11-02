import io
from datetime import date, timedelta

import docx
import pandas as pd
import pytest

from core import export, importer, rules
from core.db import RiskDB
from core.seed import seed_demo


# ---------------- правила
@pytest.mark.parametrize("p,i,expected", [(1, 1, "Низкий"), (2, 2, "Низкий"), (1, 5, "Средний"), (3, 3, "Средний"),
                                          (2, 5, "Высокий"), (4, 4, "Высокий"), (4, 5, "Критический"), (5, 5, "Критический")])
def test_levels_default(p, i, expected):
    assert rules.level_name(rules.score(p, i)) == expected


def test_level_unassessed_and_custom_levels():
    assert rules.level_name(None) == "Не оценён"
    custom = [{"code": "a", "name": "А", "min": 1, "color": "#000"}, {"code": "b", "name": "Б", "min": 15, "color": "#111"}]
    assert rules.level_name(15, custom) == "Б" and rules.level_name(14, custom) == "А"
    assert rules.level_rank(None) == 0 and rules.level_rank(25) == 4


@pytest.mark.parametrize("value,kind,expected", [
    (3, "impact", 3), (4.0, "impact", 4), ("5", "impact", 5), ("4 — вероятно", "probability", 4), ("4,0", "impact", 4),
    ("высокая", "probability", 4), ("Очень высокое", "impact", 5), ("очень низкая", "probability", 1),
    ("средняя", "probability", 3), ("катастрофическое", "impact", 5),
    ("60 %", "probability", 4), ("5%", "probability", 1), ("10%", "probability", 2), ("80%", "probability", 5),
    (0.35, "probability", 3), (45, "probability", 3), ("60 %", "impact", None),
    (7, "impact", None), (0, "impact", None), ("", "impact", None), (None, "impact", None), ("непонятно", "impact", None),
    (2.5, "impact", None), (float("nan"), "impact", None), (True, "impact", None),
])
def test_parse_score(value, kind, expected):
    assert rules.parse_score(value, kind) == expected


def test_dates_and_weeks():
    assert rules.to_date("31.12.2026") == date(2026, 12, 31)
    assert rules.to_date("2026-11-02") == date(2026, 11, 2)
    assert rules.to_date("") is None and rules.to_date(None) is None and rules.to_date(pd.NaT) is None
    assert rules.week_start(date(2026, 11, 8)) == date(2026, 11, 2)  # воскресенье → понедельник
    wk = rules.weeks_between(date(2026, 12, 28), date(2027, 1, 5))  # переход года
    assert wk == [date(2026, 12, 28), date(2027, 1, 4)]
    assert rules.week_label(date(2026, 11, 2)).startswith("Нед 45")


def test_trend():
    assert rules.trend_symbol(None, 9) == "новый"
    assert rules.trend_symbol(6, 9) == "↑" and rules.trend_symbol(9, 6) == "↓" and rules.trend_symbol(6, 6) == "→"
    assert rules.trend_symbol(6, None) == ""


@pytest.fixture
def db():
    return RiskDB(":memory:")


def test_weekly_snapshots_carry_forward_and_close(db):
    mon = date(2026, 10, 5)
    a = db.create_risk({"title": "A", "created_on": mon})
    b = db.create_risk({"title": "B", "created_on": mon})
    db.add_assessment(a, 2, 2, mon)
    db.add_assessment(a, 4, 5, mon + timedelta(days=15))     # неделя 3
    db.add_assessment(b, 3, 3, mon + timedelta(days=1))
    db.update_risk(b, {"status": "Закрыт"})
    db._exec("UPDATE risk SET closed_on=? WHERE id=?", ((mon + timedelta(days=9)).isoformat(), b))  # закрыт на 2-й неделе
    weeks = rules.weeks_between(mon, mon + timedelta(days=20))
    snap = rules.weekly_snapshots(db.risks(), db.assessments(), weeks)
    by = snap.groupby("week")
    assert set(by.get_group(weeks[0])["risk_id"]) == {a, b}
    assert set(by.get_group(weeks[1])["risk_id"]) == {a}                       # B закрыт
    assert by.get_group(weeks[1]).iloc[0]["score"] == 4                        # перенос 2×2
    assert by.get_group(weeks[2]).iloc[0]["score"] == 20


def test_register_view_and_attention(db):
    today = date(2026, 11, 4)
    r1 = db.create_risk({"title": "Растущий", "owner": "РП", "created_on": today - timedelta(days=30), "due_date": today - timedelta(days=1)})
    r2 = db.create_risk({"title": "Без владельца", "created_on": today - timedelta(days=30)})
    db.create_risk({"title": "Без оценки", "owner": "РП"})
    db.add_assessment(r1, 3, 3, today - timedelta(days=10))
    db.add_assessment(r1, 4, 5, today)
    db.add_assessment(r2, 2, 2, today - timedelta(days=20))
    reg = rules.register_view(db.risks(), db.assessments(), today)
    row = reg.set_index("title").loc["Растущий"]
    assert row["score"] == 20 and row["prev_score"] == 9 and row["trend"] == "↑" and row["level"] == "Критический"
    att = rules.attention(reg, today)
    assert list(att["critical"]["title"]) == ["Растущий"]
    assert list(att["grown"]["title"]) == ["Растущий"]
    assert list(att["no_owner"]["title"]) == ["Без владельца"]
    assert list(att["unassessed"]["title"]) == ["Без оценки"]
    assert list(att["overdue"]["title"]) == ["Растущий"]
    assert list(att["stale"]["title"]) == ["Без владельца"]


# ---------------- БД
def test_db_crud_audit_and_status(db):
    rid = db.create_risk({"title": "Тест"}, actor="я")
    assert db.risk(rid)["code"] == "R-001" and db.next_code() == "R-002"
    db.add_assessment(rid, 3, 4)
    assert db.risk(rid)["status"] == "Оценён"
    diff = db.update_risk(rid, {"owner": "РП", "status": "Закрыт"})
    assert "closed_on" in diff and db.risk(rid)["closed_on"]
    db.update_risk(rid, {"status": "В обработке"})
    assert db.risk(rid)["closed_on"] is None
    with pytest.raises(ValueError):
        db.add_assessment(rid, 6, 1)
    assert len(db.audit_log()) >= 4
    db.set_setting("levels", [{"code": "x", "name": "X", "min": 1, "color": "#000"}])
    assert db.levels()[0]["name"] == "X"


def test_seed_demo(db):
    seed_demo(db, today=date(2026, 11, 2))
    risks, ass = db.risks(), db.assessments()
    assert len(risks) == 20 and len(ass) > 60
    assert (risks["status"] == "Закрыт").sum() == 2
    assert db.get_setting("demo") is True
    reg = rules.register_view(risks, ass, date(2026, 11, 2))
    assert reg["score"].isna().sum() == 1  # один неоценённый риск
    assert ass["assessed_on"].max() <= date(2026, 11, 2)


# ---------------- импорт
def _xlsx(df: pd.DataFrame, top_rows: int = 0) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="Риски", index=False, startrow=top_rows)
        if top_rows:
            xw.sheets["Риски"]["A1"] = "Реестр рисков проекта"
    return buf.getvalue()


def test_import_excel_with_title_row_and_words(db):
    df = pd.DataFrame({"№": [1, 2, 3], "Наименование риска": ["Срыв поставки", "Уход эксперта", "Рост цен"],
                       "Описание риска": ["Поставщик сорвёт срок", "Единственный носитель знаний", ""],
                       "Вероятность (1-5)": ["высокая", 2, "60%"], "Последствия": [4, "очень высокое", "непонятно"],
                       "Ответственный": ["Закупки", "РП", ""], "Срок": ["31.12.2026", "", "abc"]})
    pf = importer.read_file("r.xlsx", _xlsx(df, top_rows=2))
    src = pf.sources[0]
    assert src.header_row == 1  # пустая строка под названием документа отброшена
    table = importer.table_from_source(src)
    mp = importer.auto_mapping(list(table.columns), table)
    assert mp["title"] == "Наименование риска" and mp["impact"] == "Последствия" and mp["owner"] == "Ответственный"
    assert mp["code"] == "№" and mp["due_date"] == "Срок"
    c = importer.normalize(table, mp)
    assert list(c["probability"]) == [4, 2, 4]
    assert c["impact"].iloc[0] == 4 and c["impact"].iloc[1] == 5 and pd.isna(c["impact"].iloc[2])
    assert "не распознано" in c["issues"].iloc[2] and "срок мер" in c["issues"].iloc[2]
    assert c["due_date"].iloc[0] == date(2026, 12, 31)
    stats = importer.apply_import(c, db, actor="тест", file_name="r.xlsx")
    assert stats == {"created": 3, "updated": 0, "assessments": 2, "unassessed": 1}
    # повторный импорт не создаёт дублей и не дублирует оценки
    stats2 = importer.apply_import(c, db, actor="тест", file_name="r.xlsx")
    assert stats2["created"] == 0 and stats2["updated"] == 3 and stats2["assessments"] == 0
    assert db.count() == 3


def test_text_consequences_column_is_not_impact():
    df = pd.DataFrame({"Риск": ["А", "Б"], "Последствия": ["срыв сроков проекта", "штрафы"]})
    pf = importer.read_file("r.xlsx", _xlsx(df))
    table = importer.table_from_source(pf.sources[0])
    mp = importer.auto_mapping(list(table.columns), table)
    assert mp["impact"] is None and mp["description"] == "Последствия"


def test_import_history_by_date(db):
    df = pd.DataFrame({"Код": ["R-7", "R-7", "R-7"], "Риск": ["Сбой", "Сбой", "Сбой"], "Вероятность": [2, 3, 4],
                       "Влияние": [3, 3, 4], "Дата оценки": ["05.10.2026", "12.10.2026", "19.10.2026"]})
    pf = importer.read_file("h.xlsx", _xlsx(df))
    table = importer.table_from_source(pf.sources[0])
    c = importer.normalize(table, importer.auto_mapping(list(table.columns), table))
    stats = importer.apply_import(c, db, today=date(2026, 11, 2))
    assert stats["created"] == 1 and stats["assessments"] == 3
    rid = db.find_risk("R-7")["id"]
    assert db.risk(rid)["created_on"] == "2026-10-05"
    assert list(db.assessments(rid)["probability"]) == [2, 3, 4]


def _docx(build) -> bytes:
    d = docx.Document()
    build(d)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_import_docx_table():
    def build(d):
        d.add_paragraph("Перечень рисков")
        t = d.add_table(rows=1, cols=3)
        for c, h in zip(t.rows[0].cells, ["Риск", "Описание", "Влияние"]):
            c.text = h
        for r in [("Срыв сроков", "Подрядчик не успеет", "высокое"), ("Утечка", "Данные уйдут", "5")]:
            for c, v in zip(t.add_row().cells, r):
                c.text = v
    pf = importer.read_file("t.docx", _docx(build))
    assert pf.sources[0].kind == "table"
    table = importer.table_from_source(pf.sources[0])
    c = importer.normalize(table, importer.auto_mapping(list(table.columns), table))
    assert list(c["title"]) == ["Срыв сроков", "Утечка"] and list(c["impact"]) == [4, 5]


def test_import_docx_headings_with_inline_fields():
    def build(d):
        d.add_heading("Риски проекта", 1)
        d.add_paragraph("Вступление, не относящееся к рискам.")
        d.add_heading("Задержка интеграции", 2)
        d.add_paragraph("API не документирован.")
        d.add_paragraph("Подрядчик отвечает медленно.")
        d.add_paragraph("Вероятность: высокая")
        d.add_paragraph("Влияние — 4")
        d.add_paragraph("Владелец: ИТ-архитектор")
        d.add_heading("Сопротивление изменениям", 2)
        d.add_paragraph("Пользователи привыкли к старым процессам.")
    pf = importer.read_file("h.docx", _docx(build))
    src = next(s for s in pf.sources if s.kind == "text")
    c = importer.normalize(src.raw, importer.auto_mapping(list(src.raw.columns), src.raw))
    assert list(c["title"]) == ["Задержка интеграции", "Сопротивление изменениям"]
    first = c.iloc[0]
    assert first["description"] == "API не документирован.\nПодрядчик отвечает медленно."
    assert first["probability"] == 4 and first["impact"] == 4 and first["owner"] == "ИТ-архитектор"
    assert pd.isna(c.iloc[1]["probability"])
    assert "Риски проекта" in pf.text


def test_import_docx_numbered_and_prefixed_text():
    def build(d):
        d.add_paragraph("Риск 1. Нехватка бюджета")
        d.add_paragraph("Бюджет на доработки исчерпан на 70 %.")
        d.add_paragraph("Риск 2. Болезнь ключевого сотрудника")
        d.add_paragraph("Нет замены.")
    pf = importer.read_file("n.docx", _docx(build))
    src = next(s for s in pf.sources if s.kind == "text")
    assert list(src.raw["title"]) == ["Нехватка бюджета", "Болезнь ключевого сотрудника"]
    assert list(src.raw["description"]) == ["Бюджет на доработки исчерпан на 70 %.", "Нет замены."]

    def build_list(d):
        d.add_paragraph("Срыв поставки", style="List Number")
        d.add_paragraph("Поставщик может сорвать срок.")
        d.add_paragraph("Ошибки миграции", style="List Number")
    src = next(s for s in importer.read_file("l.docx", _docx(build_list)).sources if s.kind == "text")
    assert list(src.raw["title"]) == ["Срыв поставки", "Ошибки миграции"]


def test_templates_roundtrip(db):
    for name, data in (("t.xlsx", export.template_xlsx()), ("t.docx", export.template_docx())):
        pf = importer.read_file(name, data)
        src = pf.sources[0]
        table = importer.table_from_source(src)
        c = importer.normalize(table, importer.auto_mapping(list(table.columns), table))
        assert len(c) >= 2 and c["title"].str.len().min() > 3
    docx_text = next(s for s in importer.read_file("t.docx", export.template_docx()).sources if s.kind == "text")
    assert len(docx_text.raw) == 2


def test_export_excel(db):
    seed_demo(db, today=date(2026, 11, 2))
    reg = rules.register_view(db.risks(), db.assessments(), date(2026, 11, 2))
    data = export.register_to_excel(reg, db.assessments(), pd.DataFrame({"a": [1]}), db.levels())
    xl = pd.read_excel(io.BytesIO(data), sheet_name=None)
    assert set(xl) == {"Реестр", "История оценок", "Динамика по неделям", "Тепловая карта"}
    assert len(xl["Реестр"]) == 20


def test_level_to_pi():
    assert rules.level_to_pi("Высокий") == (4, 4)
    assert rules.level_to_pi("критический") == (5, 5) and rules.level_to_pi("Очень высокий") == (5, 5)
    assert rules.level_to_pi("средний") == (3, 3) and rules.level_to_pi("Низкий") == (2, 2)
    assert rules.level_to_pi("16") == (4, 4) and rules.level_to_pi(12) == (3, 4)
    assert rules.level_to_pi("7")[0] * rules.level_to_pi("7")[1] in (6, 8)
    assert rules.level_to_pi("?") is None and rules.level_to_pi(30) is None and rules.level_to_pi(None) is None


@pytest.mark.parametrize("header,field", [
    ("Описание проблемы и потенциальные последствия", "description"),
    ("Наименование риска / Спорного момента", "title"),
    ("Рекомендуемые мероприятия по нейтрализации риска", "response_plan"),
    ("№ п/п", "code"), ("Категория риска", "category"), ("Уровень риска", "level"),
    ("Ограничение / Требование договора (SLA, штрафы, условия)", None),
])
def test_match_field_priorities(header, field):
    assert importer.match_field(header) == field


def test_import_excel_register_with_sections_and_level(db):
    """Структура как у реального реестра: заголовок документа, шапка на 3-й строке, строки-разделы, уровень вместо В/Вл."""
    rows = [
        ["РЕЕСТР ПОДВОДНЫХ КАМНЕЙ И РИСКОВ ДЛЯ ИСПОЛНИТЕЛЯ", None, None, None, None, None, None],
        ["По закупке № 0000 (Заказчик, портал)", "Ан", None, None, None, None, None],
        ["№ п/п", "Категория риска", "Наименование риска / Спорного момента", "Описание проблемы и потенциальные последствия",
         "Ограничение / Требование договора (SLA, штрафы, условия)", "Уровень риска", "Рекомендуемые мероприятия по нейтрализации риска"],
        ["1. Проектирование и интеграция", None, None, None, None, None, None],
        [1, "Проектирование", "Затягивание согласований", "Сдвиг сроков разработки", "Раздел 5 ТЗ", "Высокий", "Фиксировать даты"],
        [2, None, "Сложность интеграций", "Много смежных систем", None, "Критический", "Совместное проектирование"],
        ["2. Финансы", None, None, None, None, None, None],
        [3, None, "Рамочный договор", "Нет гарантии выемки", "Раздел 3", "средний", ""],
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False, header=False)
    pf = importer.read_file("r.xlsx", buf.getvalue())
    src = pf.sources[0]
    assert src.header_row == 2
    table = importer.table_from_source(src)
    mp = importer.auto_mapping(list(table.columns), table)
    assert mp["level"] == "Уровень риска" and mp["probability"] is None
    assert mp["extra"] == ["Ограничение / Требование договора (SLA, штрафы, условия)"]
    c = importer.normalize(table, mp)
    assert list(c["title"]) == ["Затягивание согласований", "Сложность интеграций", "Рамочный договор"]
    assert list(c["code"]) == ["", "", ""]                                  # № п/п — не код
    assert list(c["category"]) == ["Проектирование", "Проектирование и интеграция", "Финансы"]  # пустая — из раздела
    assert list(c["probability"]) == [4, 5, 3] and list(c["impact"]) == [4, 5, 3]
    assert c["level_based"].all() and "условно" in c["issues"].iloc[0]
    assert c["description"].iloc[0] == "Сдвиг сроков разработки\n\nОграничение / Требование договора (SLA, штрафы, условия): Раздел 5 ТЗ"
    importer.apply_import(c, db, file_name="r.xlsx")
    assert db.count() == 3 and "R-003" in set(db.risks()["code"])
    assert "условно по уровню" in db.assessments().iloc[0]["comment"]


def test_import_docx_analysis_note_risk_sections():
    """Аналитическая записка: H1 — разделы, риски — пункты списка «Название: описание» в разделах про риски."""
    def build(d):
        d.add_paragraph("Аналитический разбор договора").runs[0].bold = True
        d.add_heading("1. Комплексный анализ договора", 1)
        d.add_paragraph("Договор регулирует выполнение работ.")
        d.add_paragraph("Предмет: модификация портала.", style="List Bullet")
        d.add_heading("2. Ключевые риски Исполнителя", 1)
        d.add_paragraph("Нет гарантии выемки бюджета: заказчик не обязан выбрать весь объём (п. 3.5).", style="List Bullet")
        d.add_paragraph("Очное присутствие: все работы — на объектах заказчика.", style="List Bullet")
        d.add_heading("3. Реестр штрафов", 1)
        d.add_paragraph("Таблица ниже.")
        d.add_heading("4. Правовые и технические нестыковки договора", 1)
        d.add_paragraph("Коллизия сроков — заявки 24 месяца, работы до 36 месяцев.", style="List Bullet")
        d.add_heading("5. Состав команды", 1)
        d.add_paragraph("Руководитель проекта: стаж от 6 лет.", style="List Bullet")
        t = d.add_table(rows=2, cols=3)
        for c, v in zip(t.rows[0].cells, ["Основание", "Размер штрафа", "Пункт"]):
            c.text = v
        for c, v in zip(t.rows[1].cells, ["Нарушение", "10 %", "9.2"]):
            c.text = v
    pf = importer.read_file("a.docx", _docx(build))
    best = pf.sources[0]
    assert best.name.startswith("Текст: пункты в разделах о рисках")
    assert list(best.raw["title"]) == ["Нет гарантии выемки бюджета", "Очное присутствие", "Коллизия сроков"]
    assert best.raw["description"].iloc[2] == "заявки 24 месяца, работы до 36 месяцев."
    names = [s.name for s in pf.sources]
    assert any(n.startswith("Текст: каждый пункт списка") for n in names) and "Таблица 1" in names
    table = importer.table_from_source(best)
    c = importer.normalize(table, importer.auto_mapping(list(table.columns), table))
    assert len(c) == 3 and c["probability"].isna().all()


def test_unsupported_file():
    with pytest.raises(ValueError):
        importer.read_file("x.pdf", b"")
