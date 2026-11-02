"""Импорт рисков из Excel (.xlsx) и Word (.docx).

Конвейер: файл → «источники» (листы Excel, таблицы Word, варианты разбора текста Word) →
поиск строки заголовка → сопоставление колонок по синонимам → нормализация значений →
план импорта (создать / обновить) → запись в БД.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from core import config
from core.rules import level_to_pi, parse_score, to_date

FIELDS = list(config.FIELD_LABELS)
EXTRA = "extra"  # ключ сопоставления: колонки, которые дописываются в описание


@dataclass
class Source:
    name: str             # «Лист «Риски»», «Таблица 2», «Текст: разделы о рисках»
    kind: str             # sheet | table | text
    raw: pd.DataFrame     # для sheet/table — сырые ячейки без заголовка; для text — готовые поля
    header_row: int = 0
    score: float = 0      # насколько похоже на реестр рисков (для порядка в списке)
    hint: str = ""        # пояснение, как разобран источник


@dataclass
class ParsedFile:
    name: str
    sources: list[Source] = field(default_factory=list)
    text: str = ""        # весь текст документа — для извлечения рисков ИИ


# ---------------------------------------------------------------- чтение файлов
def read_file(name: str, data: bytes) -> ParsedFile:
    low = name.lower()
    if low.endswith((".xlsx", ".xlsm")):
        return _read_excel(name, data)
    if low.endswith(".docx"):
        return _read_docx(name, data)
    if low.endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(data), header=None, dtype=object, sep=None, engine="python")
        return _finish(ParsedFile(name, [Source("CSV", "sheet", _clean(raw))]))
    raise ValueError("Поддерживаются файлы .xlsx, .docx и .csv")


def _read_excel(name: str, data: bytes) -> ParsedFile:
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object, engine="openpyxl")
    pf = ParsedFile(name)
    for sheet, raw in sheets.items():
        raw = _clean(raw)
        if not raw.empty:
            pf.sources.append(Source(f"Лист «{sheet}»", "sheet", raw))
    return _finish(pf)


def _read_docx(name: str, data: bytes) -> ParsedFile:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    pf = ParsedFile(name)
    for n, table in enumerate(document.tables, start=1):
        rows = []
        for row in table.rows:
            cells, prev = [], None
            for cell in row.cells:
                # объединённые по горизонтали ячейки python-docx отдаёт повторно — берём один раз
                cells.append("" if cell._tc is prev else cell.text.strip())
                prev = cell._tc
            rows.append(cells)
        raw = _clean(pd.DataFrame(rows, dtype=object))
        if not raw.empty and len(raw) >= 2:
            pf.sources.append(Source(f"Таблица {n}", "table", raw))
    paragraphs = [_para_info(p) for p in document.paragraphs]
    pf.text = "\n".join(p["text"] for p in paragraphs if p["text"])
    pf.sources.extend(text_sources(paragraphs))
    return _finish(pf)


def _finish(pf: ParsedFile) -> ParsedFile:
    for s in pf.sources:
        if s.kind != "text":
            s.header_row, s.score = detect_header(s.raw)
            s.hint = f"заголовок таблицы — строка {s.header_row + 1}"
    pf.sources.sort(key=lambda s: s.score, reverse=True)
    return pf


def _clean(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.map(lambda v: None if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and pd.isna(v)) else v)
    raw = raw.dropna(how="all").dropna(axis=1, how="all")
    return raw.reset_index(drop=True).T.reset_index(drop=True).T


# ---------------------------------------------------------------- заголовки и колонки
def _norm(s) -> str:
    s = str(s or "").lower().replace("ё", "е").replace("\n", " ")
    s = re.sub(r"\(.*?\)", " ", s)        # «Вероятность (1–5)» → «вероятность»
    s = re.sub(r"[^\w№# ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_field(header) -> str | None:
    """Поле реестра по заголовку колонки: точный синоним → заголовок начинается с синонима → содержит синоним.

    Порядок важен: «Описание проблемы и последствия» — это описание, а не влияние.
    """
    h = _norm(header)
    if not h:
        return None
    syn = [(fld, _norm(s)) for fld, ss in config.COLUMN_SYNONYMS.items() for s in ss]
    for fld, sn in syn:
        if h == sn:
            return fld
    for test in (lambda sn: h.startswith(sn + " ") or h.startswith(sn),
                 lambda sn: f" {sn}" in f" {h}"):
        cands = [(len(sn), fld) for fld, sn in syn if len(sn) >= 4 and test(sn)]
        if cands:
            return max(cands)[1]
    return None


def detect_header(raw: pd.DataFrame, max_rows: int = 15) -> tuple[int, float]:
    """Строка заголовка = строка с наибольшим числом распознанных полей среди первых max_rows."""
    best_row, best_score = 0, 0
    for r in range(min(max_rows, len(raw))):
        fields = {match_field(v) for v in raw.iloc[r].tolist() if v is not None}
        fields.discard(None)
        sc = len(fields) + (2 if "title" in fields else 0)
        if sc > best_score:
            best_row, best_score = r, sc
    return best_row, best_score


def table_from_source(src: Source) -> pd.DataFrame:
    """Таблица с заголовками из источника (для text — как есть)."""
    if src.kind == "text":
        return src.raw.copy()
    header = [str(v).strip() if v is not None else "" for v in src.raw.iloc[src.header_row].tolist()]
    seen: dict[str, int] = {}
    cols = []
    for idx, h in enumerate(header):
        h = h or f"Колонка {idx + 1}"
        seen[h] = seen.get(h, 0) + 1
        cols.append(h if seen[h] == 1 else f"{h} ({seen[h]})")
    body = src.raw.iloc[src.header_row + 1:].copy()
    body.columns = cols
    return body.reset_index(drop=True)


def auto_mapping(columns: list[str], table: pd.DataFrame | None = None) -> dict:
    """{поле: колонка | None, …, "extra": [колонки, которые допишутся в описание]}."""
    mapping: dict = {f: None for f in FIELDS}
    for col in columns:
        fld = col if col in mapping else match_field(col)  # у текстовых источников колонки уже названы полями
        if fld and mapping[fld] is None:
            mapping[fld] = col
    if table is not None:
        # «Последствия» бывают текстом, а не баллом: такую колонку не считаем оценкой
        for fld in ("probability", "impact"):
            col = mapping[fld]
            if col is None:
                continue
            vals = [v for v in table[col].tolist() if _text(v)]
            ok = sum(parse_score(v, fld) is not None for v in vals)
            if vals and ok / len(vals) < 0.5:
                mapping[fld] = None
                if mapping["description"] is None:
                    mapping["description"] = col
        col = mapping["level"]
        if col is not None:
            vals = [v for v in table[col].tolist() if _text(v)]
            if vals and sum(level_to_pi(v) is not None for v in vals) / len(vals) < 0.5:
                mapping["level"] = None
    # без колонки «название» берём первую колонку, не занятую другими полями
    if mapping["title"] is None:
        used = {c for f, c in mapping.items() if c and f != EXTRA}
        free = [c for c in columns if c not in used]
        if free:
            mapping["title"] = free[0]
    used = {c for f, c in mapping.items() if c and f != EXTRA}
    mapping[EXTRA] = [c for c in columns if c not in used and not c.startswith("Колонка ")
                      and (table is None or any(_text(v) for v in table[c].tolist()))]
    return mapping


# ---------------------------------------------------------------- текст Word
INLINE_RE = re.compile(r"^\s*(?P<key>[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z \-]{1,30}?)\s*[:—–-]\s*(?P<val>.+)$")
BULLET_RE = re.compile(r"^\s*[•·▪●○◦\-–—*]\s*")
TITLE_PREFIX_RE = re.compile(r"^\s*(?:риск\s*(?:№\s*)?\d+[.:)]?|R-?\d+[.:)]?|\d{1,3}(?:\.\d+)*[.)])\s*", re.IGNORECASE)


def _para_info(p) -> dict:
    style = ((p.style.name if p.style is not None else "") or "").lower()
    ppr = p._p.pPr
    numbered = ppr is not None and ppr.numPr is not None
    m = re.match(r"(?:heading|заголовок)\s*(\d)", style)
    runs = [r for r in p.runs if r.text.strip()]
    bold = bool(runs) and all(r.bold for r in runs)
    text = p.text.strip()
    is_list = numbered or style.startswith(("list", "список", "маркированный", "нумерованный")) or bool(BULLET_RE.match(text))
    return {"text": text, "heading": int(m.group(1)) if m else 0, "list": is_list, "bold": bold}


def _clean_title(t: str) -> str:
    return TITLE_PREFIX_RE.sub("", BULLET_RE.sub("", t)).strip(" .:—–-")


def _split_title(text: str) -> tuple[str, str]:
    """«Название: описание» / «Название — описание» в одном абзаце."""
    t = TITLE_PREFIX_RE.sub("", BULLET_RE.sub("", text)).strip()
    m = re.match(r"^(.{3,120}?)(?::\s+|\s+[—–]\s+)(.+)$", t, re.S)
    if m:
        return m.group(1).strip(" .:—–-"), m.group(2).strip()
    return t.strip(" .:—–-"), ""


def _section_is_risky(heading: str) -> bool:
    h = heading.lower().replace("ё", "е")
    return any(w in h for w in config.RISK_SECTION_WORDS)


def _items_risk_sections(paras) -> list[dict]:
    """Пункты списка внутри разделов, в заголовке которых есть «риск», «нестыковки», «подводные камни»…"""
    items, active, level = [], False, 0
    for p in paras:
        if p["heading"]:
            if p["heading"] <= level or not active:
                active, level = _section_is_risky(p["text"]), p["heading"]
            continue
        if active and p["list"]:
            title, desc = _split_title(p["text"])
            items.append({"title": title, "body": [desc] if desc else []})
        elif active and items and not p["list"]:
            items[-1]["body"].append(p["text"])
    return items


def _items_by(paras, is_title) -> list[dict]:
    items, cur = [], None
    for p in paras:
        if is_title(p):
            cur = {"title": p["text"], "body": []}
            items.append(cur)
        elif cur is not None:
            cur["body"].append(p["text"])
    return items


def _items_list(paras) -> list[dict]:
    items = []
    for p in paras:
        if p["list"]:
            title, desc = _split_title(p["text"])
            items.append({"title": title, "body": [desc] if desc else []})
        elif items and not p["heading"]:
            items[-1]["body"].append(p["text"])
    return items


def _heading_rule(paras):
    heads = [p["heading"] for p in paras if p["heading"]]
    if not heads:
        return None
    levels = sorted(set(heads))
    # единственный заголовок верхнего уровня — это название документа, риски — уровнем ниже
    lvl = levels[1] if len(levels) > 1 and heads.count(levels[0]) == 1 else levels[0]
    return lambda p, lvl=lvl: p["heading"] == lvl


def text_sources(paras: list[dict]) -> list[Source]:
    """Варианты разбора текста Word; пользователь выбирает подходящий, лучший — первым."""
    paras = [p for p in paras if p["text"]]
    if not paras:
        return []
    out = []
    variants = [
        ("Текст: пункты в разделах о рисках", 3.5, lambda: _items_risk_sections(paras),
         "пункты списка «Название: описание» в разделах, где в заголовке есть «риск», «нестыковки», «подводные камни» и т. п."),
        ("Текст: каждый заголовок — риск", 1.6, lambda: _items_by(paras, _heading_rule(paras)) if _heading_rule(paras) else [],
         "заголовок = риск, абзацы под ним = описание, строки «Вероятность: …», «Владелец: …» = поля"),
        ("Текст: «Риск N.» / нумерация", 1.5, lambda: _items_by(paras, lambda p: bool(TITLE_PREFIX_RE.match(p["text"])) and not p["heading"]),
         "абзац, начинающийся с «Риск 1.», «R-01», «1.», = риск"),
        ("Текст: каждый пункт списка — риск", 1.4, lambda: _items_list(paras),
         "каждый пункт маркированного или нумерованного списка = риск"),
        ("Текст: жирная строка — риск", 1.3, lambda: _items_by(paras, lambda p: p["bold"] and len(p["text"]) < 160 and not p["heading"]),
         "короткий жирный абзац = риск, следующие абзацы = описание"),
    ]
    seen = set()
    for name, score, build, hint in variants:
        items = build()
        if len(items) < 2:
            continue
        df = _items_to_df(items)
        key = tuple(df["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(Source(f"{name} ({len(df)})", "text", df, score=score, hint=hint))
    return out


def parse_text_paragraphs(paras: list[dict]) -> pd.DataFrame:
    """Лучший вариант разбора текста (для тестов и простых случаев)."""
    srcs = sorted(text_sources(paras), key=lambda s: s.score, reverse=True)
    return srcs[0].raw if srcs else pd.DataFrame()


def _items_to_df(items: list[dict]) -> pd.DataFrame:
    rows = []
    for it in items:
        row = {"code": None}
        raw_title = BULLET_RE.sub("", it["title"])
        m = re.match(r"^\s*R-?(\d+)", raw_title, re.IGNORECASE)
        if m:
            row["code"] = f"R-{int(m.group(1)):03d}"
        title = _clean_title(raw_title)
        desc = []
        for line in it["body"]:
            fm = INLINE_RE.match(line)
            fld = match_field(fm.group("key")) if fm else None
            if fld and fld not in ("title", "code", "description") and fld not in row and len(fm.group("key")) <= 25:
                row[fld] = fm.group("val").strip()
            else:
                desc.append(line)
        if not desc and len(title) > 90:  # длинный заголовок без описания — разделяем
            t, d = _split_title(title)
            if d:
                title, desc = t, [d]
        row["title"] = title
        row["description"] = "\n".join(desc)
        rows.append(row)
    df = pd.DataFrame(rows)
    return df.reindex(columns=[c for c in FIELDS if c in df.columns])


# ---------------------------------------------------------------- нормализация
STRATEGY_KEYS = [("снизи", "Снизить"), ("сниж", "Снизить"), ("миним", "Снизить"), ("избеж", "Избежать"), ("уклон", "Избежать"),
                 ("переда", "Передать"), ("страх", "Передать"), ("приня", "Принять"), ("принят", "Принять"), ("эскал", "Эскалировать")]


def _map_status(v) -> str | None:
    t = _norm(v)
    if not t:
        return None
    for s in config.STATUSES:
        if t.startswith(_norm(s)[:5]):
            return s
    if t.startswith(("открыт", "актив", "новый")):
        return "Выявлен"
    if t.startswith(("в работе", "обраб")):
        return "В обработке"
    if t.startswith(("монитор", "контрол")):
        return "На мониторинге"
    if t.startswith(("закр", "неактуал", "снят")):
        return "Закрыт"
    if t.startswith(("реализ", "наступ", "произош")):
        return "Реализовался"
    return None


def _map_strategy(v) -> str:
    t = _norm(v)
    for key, val in STRATEGY_KEYS:
        if key in t:
            return val
    return ""


def _text(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def normalize(table: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Кандидаты на импорт в полях реестра + замечания по каждой строке."""
    out = []
    header_words = {_norm(c) for c in table.columns}
    extra_cols = [c for c in mapping.get(EXTRA, []) if c in table.columns]
    section = ""
    for _, src in table.iterrows():
        get = lambda f: src[mapping[f]] if mapping.get(f) and mapping[f] in src.index else None  # noqa: E731
        row = {f: _text(get(f)) for f in FIELDS}
        filled = [_text(v) for v in src.tolist() if _text(v)]
        if not filled:
            continue
        # строка-раздел («2. Разработка и архитектура» в объединённой ячейке) — запоминаем как категорию
        if len(filled) == 1 and not row["title"]:
            if not re.fullmatch(r"\d+", filled[0]):
                section = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", filled[0])
            continue
        if _norm(row["title"]) and _norm(row["title"]) in header_words:  # повтор шапки на новой странице
            continue
        issues = []
        if re.fullmatch(r"\d+([.,]0)?", row["code"]):  # «№ п/п» — номер строки, а не код риска
            row["code"] = ""
        if not row["title"] and row["description"]:
            first = re.split(r"(?<=[.!?])\s", row["description"], maxsplit=1)[0]
            row["title"] = first[:90]
            issues.append("название взято из описания")
        if not row["title"]:
            continue
        extras = [f"{c}: {_text(src[c])}" for c in extra_cols if _text(src[c])]
        if extras:
            row["description"] = "\n\n".join([row["description"]] + extras if row["description"] else extras)
        if not row["category"] and section:
            row["category"] = section
        p_raw, i_raw = get("probability"), get("impact")
        row["probability"] = parse_score(p_raw, "probability")
        row["impact"] = parse_score(i_raw, "impact")
        if _text(p_raw) and row["probability"] is None:
            issues.append(f"вероятность «{_text(p_raw)}» не распознана")
        if _text(i_raw) and row["impact"] is None:
            issues.append(f"влияние «{_text(i_raw)}» не распознано")
        row["level_based"] = False
        if row["probability"] is None and row["impact"] is None and row["level"]:
            pi = level_to_pi(row["level"])
            if pi:
                row["probability"], row["impact"] = pi
                row["level_based"] = True
                issues.append(f"В и Вл условно восстановлены по уровню «{row['level']}» — уточните")
            else:
                issues.append(f"уровень «{row['level']}» не распознан")
        if (row["probability"] is None) != (row["impact"] is None):
            issues.append("оценка неполная — нужна и вероятность, и влияние")
        status_raw = row["status"]
        row["status"] = _map_status(status_raw) or ""
        if status_raw and not row["status"]:
            issues.append(f"статус «{status_raw}» не распознан")
        row["strategy"] = _map_strategy(row["strategy"]) if row["strategy"] else ""
        row["due_date"] = to_date(get("due_date")) if _text(get("due_date")) else None
        row["assessed_on"] = to_date(get("assessed_on")) if _text(get("assessed_on")) else None
        if _text(get("due_date")) and row["due_date"] is None:
            issues.append("срок мер не распознан")
        if row["category"]:
            row["category"] = row["category"][:1].upper() + row["category"][1:]
        row["issues"] = "; ".join(issues)
        out.append(row)
    cols = FIELDS + ["level_based", "issues"]
    df = pd.DataFrame(out, columns=cols)
    for c in ("probability", "impact"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["level_based"] = df["level_based"].fillna(False).astype(bool)
    return df


# ---------------------------------------------------------------- план и применение
def _key(code, title) -> str:
    return (code or "").strip().lower() or "t:" + _norm(title)


def plan_import(cands: pd.DataFrame, db, update_existing: bool = True) -> pd.DataFrame:
    """Добавляет колонку «действие»: создать / обновить."""
    df = cands.copy()
    actions = []
    for _, r in df.iterrows():
        found = db.find_risk(r["code"] or None, r["title"]) if update_existing else None
        actions.append("обновить" if found else "создать")
    df.insert(0, "action", actions)
    return df


def apply_import(cands: pd.DataFrame, db, actor: str = "", role: str = "", file_name: str = "",
                 update_existing: bool = True, today: date | None = None) -> dict:
    """Записывает кандидатов в реестр. Строки одного риска (по коду или названию) с разными датами — история оценок."""
    today = today or date.today()
    stats = {"created": 0, "updated": 0, "assessments": 0, "unassessed": 0}
    groups: dict[str, list[dict]] = {}
    for r in cands.to_dict("records"):
        groups.setdefault(_key(r.get("code"), r.get("title")), []).append(r)
    for rows in groups.values():
        rows.sort(key=lambda r: r.get("assessed_on") or today)
        last = rows[-1]
        fields = {k: last.get(k) for k in ("code", "title", "description", "category", "owner", "status", "strategy",
                                          "response_plan", "due_date", "trigger")}
        fields = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in fields.items()}
        existing = db.find_risk(fields.get("code") or None, fields.get("title")) if update_existing else None
        if existing:
            changes = {k: v for k, v in fields.items() if v not in (None, "") and k != "code"}
            db.update_risk(existing["id"], changes, actor, role)
            rid = existing["id"]
            stats["updated"] += 1
        else:
            if fields.get("code") and db.find_risk(fields["code"]):
                fields["code"] = None  # код занят (режим «добавлять как новые») — выдадим следующий
            first_date = rows[0].get("assessed_on")
            fields.update(source="import", created_on=first_date if first_date and first_date < today else today)
            rid = db.create_risk(fields, actor, role)
            stats["created"] += 1
        hist = db.assessments(rid)
        have = {(a.assessed_on, int(a.probability), int(a.impact)) for a in hist.itertuples()}
        added = 0
        for r in rows:
            p, i = r.get("probability"), r.get("impact")
            if p is None or i is None or pd.isna(p) or pd.isna(i):
                continue
            on = r.get("assessed_on") or today
            if (on, int(p), int(i)) in have:
                continue
            comment = r.get("comment") or ""
            if r.get("level_based"):
                comment = (comment + "; " if comment else "") + f"условно по уровню «{r.get('level')}» из файла"
            db.add_assessment(rid, int(p), int(i), on, comment=comment or f"Импорт из {file_name}".strip(),
                              author=actor, source=r.get("_source") or "import", role=role)
            have.add((on, int(p), int(i)))
            added += 1
        stats["assessments"] += added
        if added == 0 and hist.empty:
            stats["unassessed"] += 1
    db.audit("Импорт файла", actor, role, None, {"файл": file_name, **stats})
    return stats
