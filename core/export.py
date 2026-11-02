"""Выгрузка реестра в Excel и шаблоны Excel/Word для заполнения."""
from __future__ import annotations

import io

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core import config
from core.rules import level_color, level_name

REGISTER_COLUMNS = {
    "code": "Код", "title": "Риск", "description": "Описание", "category": "Категория", "owner": "Владелец",
    "status": "Статус", "p": "Вероятность", "i": "Влияние", "score": "Балл", "level": "Уровень", "trend": "Тренд",
    "strategy": "Стратегия", "response_plan": "Меры", "due_date": "Срок мер", "trigger": "Триггер",
    "target_score": "Целевой балл", "assessed_on": "Дата оценки",
}


def register_to_excel(reg: pd.DataFrame, history: pd.DataFrame, weekly: pd.DataFrame, levels: list[dict]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        r = reg[list(REGISTER_COLUMNS)].rename(columns=REGISTER_COLUMNS)
        r.to_excel(xw, sheet_name="Реестр", index=False)
        history.to_excel(xw, sheet_name="История оценок", index=False)
        weekly.to_excel(xw, sheet_name="Динамика по неделям", index=False)
        _matrix(reg, levels).to_excel(xw, sheet_name="Тепловая карта")
        ws = xw.sheets["Реестр"]
        _style_header(ws)
        widths = {"Риск": 40, "Описание": 60, "Меры": 45, "Триггер": 30}
        for idx, col in enumerate(r.columns, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = widths.get(col, 14)
        score_col = list(r.columns).index("Балл") + 1
        for row in range(2, len(r) + 2):
            v = ws.cell(row=row, column=score_col).value
            if v not in (None, ""):
                ws.cell(row=row, column=score_col).fill = PatternFill("solid", fgColor=level_color(v, levels).lstrip("#"))
            for c in (2, 3, 13):
                ws.cell(row=row, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        for name in ("История оценок", "Динамика по неделям", "Тепловая карта"):
            _style_header(xw.sheets[name])
        hm = xw.sheets["Тепловая карта"]
        for p in range(1, config.SCALE + 1):
            for i in range(1, config.SCALE + 1):
                cell = hm.cell(row=config.SCALE - p + 2, column=i + 1)
                cell.fill = PatternFill("solid", fgColor=level_color(p * i, levels).lstrip("#"))
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            hm.row_dimensions[config.SCALE - p + 2].height = 48
        for i in range(2, config.SCALE + 2):
            hm.column_dimensions[get_column_letter(i)].width = 20
        hm.column_dimensions["A"].width = 18
    return buf.getvalue()


def _matrix(reg: pd.DataFrame, levels) -> pd.DataFrame:
    """Ячейки «В × Вл» с кодами рисков; строки — вероятность сверху вниз 5→1."""
    open_ = reg[reg["is_open"] & reg["score"].notna()]
    rows = {}
    for p in range(config.SCALE, 0, -1):
        row = {}
        for i in range(1, config.SCALE + 1):
            codes = open_[(open_["p"] == p) & (open_["i"] == i)]["code"].tolist()
            row[f"Влияние {i}"] = f"{level_name(p * i, levels)}: " + (", ".join(codes) if codes else "—")
        rows[f"Вероятность {p}"] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def _style_header(ws):
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EAED")
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"


TEMPLATE_ROWS = [
    ["R-001", "Задержка поставки оборудования", "Поставщик может сдвинуть сроки поставки на 6–8 недель", "Подрядчики",
     "Руководитель закупок", 3, 4, "Снизить", "Резервный поставщик", "31.12.2026"],
    ["R-002", "Нехватка ключевых специалистов", "Ключевые пользователи не выделят время на тестирование", "Персонал",
     "Руководитель проекта", "высокая", "среднее", "Снизить", "Согласовать график с подразделениями", ""],
    ["R-003", "Рост стоимости лицензий", "Вендор пересматривает цены со следующего квартала", "Бюджет", "", "", "", "", "", ""],
]
TEMPLATE_HEADER = ["Код", "Риск", "Описание", "Категория", "Владелец", "Вероятность (1–5)", "Влияние (1–5)",
                   "Стратегия", "Меры", "Срок мер"]


def template_xlsx() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(TEMPLATE_ROWS, columns=TEMPLATE_HEADER).to_excel(xw, sheet_name="Риски", index=False)
        ws = xw.sheets["Риски"]
        _style_header(ws)
        for idx, w in enumerate([8, 36, 50, 14, 22, 14, 14, 12, 36, 12], start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w
        help_rows = [["Обязательно", "колонка «Риск» (название). Остальные колонки — по желанию."],
                     ["Вероятность", "число 1–5, слово (низкая … очень высокая) или процент (60 %)."],
                     ["Влияние", "число 1–5 или слово (незначительное … катастрофическое)."],
                     ["История", "добавьте колонку «Дата оценки» и несколько строк одного риска с разными датами."],
                     ["Без оценок", "риски попадут в «Не оценённые»; оценить можно в приложении или через ИИ-подсказку."]]
        pd.DataFrame(help_rows, columns=["Поле", "Как заполнять"]).to_excel(xw, sheet_name="Подсказка", index=False)
        xw.sheets["Подсказка"].column_dimensions["A"].width = 16
        xw.sheets["Подсказка"].column_dimensions["B"].width = 100
    return buf.getvalue()


def template_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Риски проекта", level=1)
    d.add_paragraph("Вариант 1 — таблица (как в Excel). Вариант 2 — текстом: заголовок 2-го уровня = риск, "
                    "под ним абзацы описания и строки «Вероятность: …», «Влияние: …», «Владелец: …».")
    t = d.add_table(rows=1, cols=6)
    t.style = "Table Grid"
    for c, h in zip(t.rows[0].cells, ["Риск", "Описание", "Владелец", "Вероятность", "Влияние", "Меры"]):
        c.text = h
    for r in TEMPLATE_ROWS[:2]:
        cells = t.add_row().cells
        for c, v in zip(cells, [r[1], r[2], r[4], r[5], r[6], r[8]]):
            c.text = str(v)
    d.add_heading("Задержка интеграции со складской системой", level=2)
    d.add_paragraph("API складской системы не документирован, подрядчик отвечает с задержкой — интеграция может сдвинуться на месяц.")
    d.add_paragraph("Вероятность: высокая")
    d.add_paragraph("Влияние: 4")
    d.add_paragraph("Владелец: ИТ-архитектор")
    d.add_heading("Сопротивление изменениям", level=2)
    d.add_paragraph("Пользователи привыкли к текущим процессам и могут затягивать переход на новую систему.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
