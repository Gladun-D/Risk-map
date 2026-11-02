"""Отрисовка каждой страницы под каждой ролью и на пустом реестре (Streamlit AppTest)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import config
from views import common

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["home", "heatmap_page", "register", "card", "dynamics", "owners", "import_page", "journal", "methodology"]
ROLES = ["Риск-менеджер", "Владелец риска", "Наблюдатель"]


def harness(page: str, role: str, me: str = ""):
    import importlib

    import streamlit as st

    st.session_state["role"] = role
    st.session_state["me"] = me
    importlib.import_module(f"views.{page}").render()


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.sqlite")
    common.get_db.clear()
    yield
    common.get_db.clear()


def _run(page, role, me=""):
    at = AppTest.from_function(harness, args=(page, role, me), default_timeout=60)
    at.run()
    assert not at.exception, f"{page}/{role}: {at.exception}"
    return at


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("role", ROLES)
def test_page_renders(page, role):
    me = "Руководитель проекта" if role == "Владелец риска" else ""
    at = _run(page, role, me)
    assert at.title, f"{page}: нет заголовка"


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_on_empty_register(page):
    common.get_db().clear_all()
    _run(page, "Риск-менеджер")


def test_full_app_and_filters():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Обзор рисков"
    at.sidebar.multiselect(key="f_level").set_value(["Критический"]).run()
    assert not at.exception
    at.sidebar.selectbox(key="role").set_value("Владелец риска").run()
    assert not at.exception
    assert at.sidebar.selectbox(key="me").value


def test_reassess_from_card():
    db = common.get_db()
    rid = int(db.risks().iloc[0]["id"])
    before = len(db.assessments(rid))
    at = AppTest.from_function(harness, args=("card", "Риск-менеджер"), default_timeout=60)
    at.session_state["card_risk_id"] = rid
    at.run()
    at.radio[0].set_value(5)
    at.radio[1].set_value(5)
    at.button(key="FormSubmitter:assess_%d-Сохранить оценку" % rid).click().run()
    assert not at.exception
    assert len(db.assessments(rid)) == before + 1
    assert db.assessments(rid).iloc[-1]["probability"] == 5


def test_heatmap_compare_modes():
    at = _run("heatmap_page", "Наблюдатель")
    at.segmented_control(key="hm_mode").set_value("другая неделя").run()
    assert not at.exception
    at.segmented_control(key="hm_mode").set_value("нет").run()
    assert not at.exception


def test_levels_settings_saved():
    at = _run("methodology", "Риск-менеджер")
    at.number_input(key="lv_critical").set_value(16)
    at.button(key="FormSubmitter:levels-Сохранить пороги").click().run()
    assert not at.exception
    assert [lv["min"] for lv in common.get_db().levels()] == [1, 5, 10, 16]


def import_harness(path: str, clicks: int = 0):
    from pathlib import Path

    import streamlit as st

    from views import import_page

    class Uploaded:  # AppTest не поддерживает file_uploader — подставляем файл
        name = Path(path).name

        def getvalue(self):
            return Path(path).read_bytes()

    original = st.file_uploader
    st.file_uploader = lambda *a, **k: Uploaded()
    try:
        st.session_state["role"] = "Риск-менеджер"
        import_page.render()
    finally:
        st.file_uploader = original


def _register_xlsx(path):
    import pandas as pd

    rows = [
        ["РЕЕСТР ПОДВОДНЫХ КАМНЕЙ И РИСКОВ", None, None, None, None, None, None],
        ["№ п/п", "Категория риска", "Наименование риска / Спорного момента", "Описание проблемы и потенциальные последствия",
         "Ограничение / Требование договора", "Уровень риска", "Рекомендуемые мероприятия по нейтрализации риска"],
        ["1. Проектирование", None, None, None, None, None, None],
        [1, "Проектирование", "Затягивание согласований", "Сдвиг сроков", "Раздел 5 ТЗ", "Высокий", "Фиксировать даты"],
        [2, "Интеграция", "Сложность интеграций", "Много систем", None, "Критический", "Совместное проектирование"],
        [3, "Штрафы", "Высокие штрафы", "Одностороннее удержание", "10 %", "Средний", "Контроль регламентов"],
    ]
    pd.DataFrame(rows).to_excel(path, index=False, header=False)


def test_import_excel_end_to_end(tmp_path):
    f = tmp_path / "реестр.xlsx"
    _register_xlsx(f)
    at = AppTest.from_function(import_harness, args=(str(f),), default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Лист" in o for o in at.radio[0].options)
    assert at.checkbox(key="imp_clear_demo").value is True
    btn = next(b for b in at.button if b.label.startswith("Загрузить в реестр: 3"))
    btn.click().run()
    assert not at.exception
    db = common.get_db()
    assert db.count() == 3 and not db.get_setting("demo")       # демо удалено, загружены 3 риска
    assert any("создано 3" in s.value for s in at.success)
    reg = db.risks().set_index("title")
    assert reg.at["Высокие штрафы", "category"] == "Штрафы"
    ass = db.assessments()
    assert sorted(ass["probability"] * ass["impact"]) == [9, 16, 25]  # условно по уровню


def test_import_word_note_end_to_end(tmp_path):
    import docx

    d = docx.Document()
    d.add_heading("1. Анализ договора", 1)
    d.add_paragraph("Предмет: работы.", style="List Bullet")
    d.add_heading("2. Ключевые риски Исполнителя", 1)
    d.add_paragraph("Нет гарантии выемки бюджета: заказчик не обязан выбрать весь объём.", style="List Bullet")
    d.add_paragraph("Очное присутствие: все работы на объектах заказчика.", style="List Bullet")
    f = tmp_path / "записка.docx"
    d.save(f)
    at = AppTest.from_function(import_harness, args=(str(f),), default_timeout=60)
    at.run()
    assert not at.exception
    assert at.radio[0].value == 0 and at.radio[0].options[0].startswith("Текст: пункты в разделах о рисках")
    at.checkbox(key="imp_clear_demo").uncheck().run()
    next(b for b in at.button if b.label.startswith("Загрузить в реестр: 2")).click().run()
    assert not at.exception
    db = common.get_db()
    assert db.count() == 22                                      # 20 демо + 2 новых, без оценки
    assert db.find_risk(title="Очное присутствие")["description"] == "все работы на объектах заказчика."


def test_week_axes_are_categories():
    """Подписи недель «17.08» Plotly принимает за числа — оси недель должны быть категориальными."""
    import json

    for page, n in (("dynamics", 3), ("card", 1)):
        at = _run(page, "Риск-менеджер")
        specs = [json.loads(el.proto.spec) for el in at.get("plotly_chart")]
        week_axes = [s["layout"]["xaxis"] for s in specs if s["layout"].get("xaxis", {}).get("type") == "category"]
        assert len(week_axes) == n, page
