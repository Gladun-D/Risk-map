"""Бизнес-правила без UI: балл, уровень, нормализация оценок, недельные снимки, сигналы."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta

import pandas as pd

from core import config

PROB_PERCENT_BOUNDS = [10, 30, 50, 80]  # границы шкалы вероятности, % (см. config.PROBABILITY_SCALE)


def score(p, i) -> int | None:
    """Балл риска = вероятность × влияние (порядковая шкала 1–25)."""
    if _missing(p) or _missing(i):
        return None
    return int(p) * int(i)


def level_for(s, levels: list[dict] | None = None) -> dict | None:
    """Уровень по баллу: уровень с наибольшей нижней границей ≤ балла."""
    if _missing(s):
        return None
    levels = sorted(levels or config.DEFAULT_LEVELS, key=lambda lv: lv["min"])
    found = levels[0]
    for lv in levels:
        if s >= lv["min"]:
            found = lv
    return found


def level_name(s, levels=None) -> str:
    lv = level_for(s, levels)
    return lv["name"] if lv else "Не оценён"


def level_color(s, levels=None) -> str:
    lv = level_for(s, levels)
    return lv["color"] if lv else "#9aa0a6"


def level_rank(s, levels=None) -> int:
    """0 — не оценён, далее 1..N по возрастанию уровня."""
    lv = level_for(s, levels)
    if lv is None:
        return 0
    ordered = sorted(levels or config.DEFAULT_LEVELS, key=lambda x: x["min"])
    return [x["code"] for x in ordered].index(lv["code"]) + 1


def percent_to_scale(pct: float) -> int:
    for idx, bound in enumerate(PROB_PERCENT_BOUNDS, start=1):
        if pct < bound:
            return idx
    return 5


def parse_score(value, kind: str = "impact") -> int | None:
    """Приводит оценку из файла к шкале 1–5.

    Понимает: 1–5 (в т. ч. «4 — вероятно», «4,0»), слова («высокая», «очень низкое»),
    для вероятности — проценты («60 %», 0.6). Неоднозначное — None (оценит человек).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if _missing(value):
            return None
        v = float(value)
        if kind == "probability" and 0 < v < 1:
            return percent_to_scale(v * 100)
        if 1 <= v <= config.SCALE and abs(v - round(v)) < 1e-9:
            return int(round(v))
        if kind == "probability" and config.SCALE < v <= 100:
            return percent_to_scale(v)
        return None
    text = str(value).strip().lower().replace("ё", "е")
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if m:
        pct = float(m.group(1).replace(",", "."))
        return percent_to_scale(pct) if kind == "probability" else None
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)(?![\d.,])", text)
    if m:
        return parse_score(float(m.group(1).replace(",", ".")), kind)
    words = {k.replace("ё", "е"): v for k, v in config.WORD_SCORES.items()}
    for key in sorted(words, key=len, reverse=True):  # «очень высокая» раньше «высокая»
        if re.search(rf"(^|[^а-яa-z]){re.escape(key)}($|[^а-яa-z])", text):
            return words[key]
    return None


def level_to_pi(value) -> tuple[int, int] | None:
    """Уровень из файла («Высокий», «16») → условные В и Вл для размещения на карте.

    Слово → диагональная ячейка уровня (config.LEVEL_DEFAULT_PI); число 1–25 → ячейка с таким
    баллом, ближайшая к диагонали (для «7» — ближайший возможный балл). Это допущение: точные
    В и Вл уточняются вручную или ИИ.
    """
    if _missing(value) or isinstance(value, bool):
        return None
    text = str(value).strip().lower().replace("ё", "е")
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)(?![\d.,])", text)
    if m:
        s = float(m.group(1).replace(",", "."))
        if not (1 <= s <= config.SCALE ** 2):
            return None
        cells = [(p, i) for p in range(1, config.SCALE + 1) for i in range(1, config.SCALE + 1)]
        return min(cells, key=lambda c: (abs(c[0] * c[1] - s), abs(c[0] - c[1]), -c[1]))
    for key, code in config.LEVEL_WORDS:
        if re.search(rf"(^|[^а-яa-z]){key}", text):
            return config.LEVEL_DEFAULT_PI[code]
    return None


def to_date(d) -> date | None:
    if d is None or d is pd.NaT or d is pd.NA:
        return None
    if isinstance(d, pd.Timestamp):
        return None if pd.isna(d) else d.date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, float) and math.isnan(d):
        return None
    if d is pd.NA or d is pd.NaT:
        return None
    s = str(d).strip()
    if not s or s.lower() in ("nat", "nan", "none", "<na>"):
        return None
    for fmt, n in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10), ("%d.%m.%y", 8), ("%d/%m/%Y", 10)):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    try:
        ts = pd.to_datetime(s, dayfirst=True)
        return None if pd.isna(ts) else ts.date()
    except (ValueError, TypeError):
        return None


def week_start(d) -> date:
    d = to_date(d)
    return d - timedelta(days=d.weekday())


def week_label(ws: date) -> str:
    return f"Нед {ws.isocalendar().week} · {ws:%d.%m}"


def week_short(ws: date) -> str:
    """Короткая подпись недели для осей графиков: дата понедельника."""
    return f"{ws:%d.%m}"


def weeks_between(start, end) -> list[date]:
    """Начала ISO-недель от недели start до недели end включительно."""
    ws, we = week_start(start), week_start(end)
    out = []
    while ws <= we:
        out.append(ws)
        ws += timedelta(days=7)
    return out


SNAP_COLS = ["risk_id", "probability", "impact", "assessed_on", "score"]


def latest_as_of(assessments: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Последняя оценка каждого риска на дату as_of включительно."""
    if assessments.empty:
        return pd.DataFrame(columns=SNAP_COLS)
    a = assessments[assessments["assessed_on"] <= as_of]
    if a.empty:
        return pd.DataFrame(columns=SNAP_COLS)
    a = a.sort_values(["risk_id", "assessed_on", "id"]).groupby("risk_id", as_index=False).tail(1)
    a = a[SNAP_COLS[:-1]].copy()
    a["score"] = a["probability"].astype(int) * a["impact"].astype(int)
    return a.reset_index(drop=True)


def is_open_on(risks: pd.DataFrame, as_of: date) -> pd.Series:
    """Риск открыт на дату: создан не позже и не закрыт к ней."""
    created = risks["created_on"].apply(lambda d: d is None or d <= as_of)
    closed = risks["closed_on"].apply(lambda d: d is not None and d <= as_of)
    return created & ~closed


def snapshot_at(risks: pd.DataFrame, assessments: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Положение открытых на дату рисков: последняя оценка не позднее as_of."""
    snap = latest_as_of(assessments, as_of)
    open_ids = set(risks.loc[is_open_on(risks, as_of), "id"]) if not risks.empty else set()
    return snap[snap["risk_id"].isin(open_ids)].reset_index(drop=True)


def week_snapshot_date(ws: date, today: date) -> date:
    """Дата снимка недели: воскресенье, а для текущей недели — сегодня."""
    return min(ws + timedelta(days=6), today)


def weekly_snapshots(risks: pd.DataFrame, assessments: pd.DataFrame, weeks: list[date]) -> pd.DataFrame:
    """Положение открытых рисков на конец каждой недели (перенос последней оценки вперёд).

    Модель «последняя оценка до конца периода» — как стрелки тренда в CAMMS (docs/RESEARCH.md).
    """
    frames = []
    for ws in weeks:
        we = ws + timedelta(days=6)
        snap = latest_as_of(assessments, we)
        open_ids = set(risks.loc[is_open_on(risks, we), "id"]) if not risks.empty else set()
        frames.append(snap[snap["risk_id"].isin(open_ids)].assign(week=ws))
    if not frames:
        return pd.DataFrame(columns=SNAP_COLS + ["week"])
    return pd.concat(frames, ignore_index=True)


def weekly_summary(snaps: pd.DataFrame, weeks: list[date], levels=None) -> pd.DataFrame:
    """По неделям: число рисков по уровням, суммарный и средний балл."""
    names = [lv["name"] for lv in sorted(levels or config.DEFAULT_LEVELS, key=lambda x: x["min"])]
    rows = []
    for ws in weeks:
        s = snaps[snaps["week"] == ws]
        lv = [level_name(x, levels) for x in s["score"]]
        row = {"Неделя": week_label(ws), "Начало недели": ws, "Ось": week_short(ws)}
        for n in names:
            row[n] = lv.count(n)
        row["Всего"] = len(s)
        row["Суммарный балл"] = int(s["score"].sum()) if len(s) else 0
        row["Средний балл"] = round(float(s["score"].mean()), 1) if len(s) else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def trend_symbol(prev, cur) -> str:
    if _missing(cur):
        return ""
    if _missing(prev):
        return "новый"
    if cur > prev:
        return "↑"
    if cur < prev:
        return "↓"
    return "→"


def register_view(risks: pd.DataFrame, assessments: pd.DataFrame, as_of: date, levels=None) -> pd.DataFrame:
    """Реестр на дату: риск + текущая оценка + оценка неделю назад (as_of − 7 дней) + тренд."""
    cur = latest_as_of(assessments, as_of).rename(columns={"risk_id": "id", "probability": "p", "impact": "i"})
    prev_end = as_of - timedelta(days=7)
    prev = latest_as_of(assessments, prev_end)[["risk_id", "score", "probability", "impact"]].rename(
        columns={"risk_id": "id", "score": "prev_score", "probability": "prev_p", "impact": "prev_i"})
    df = risks.merge(cur, on="id", how="left").merge(prev, on="id", how="left")
    for c in ("p", "i", "score", "prev_score", "prev_p", "prev_i"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    df["level"] = [level_name(s, levels) for s in df["score"]]
    df["level_rank"] = [level_rank(s, levels) for s in df["score"]]
    df["trend"] = [trend_symbol(p, c) for p, c in zip(df["prev_score"], df["score"])]
    df["delta"] = df["score"] - df["prev_score"]
    df["target_score"] = pd.array([score(p, i) for p, i in zip(df["target_p"], df["target_i"])], dtype="Int64")
    df["is_open"] = df["status"].isin(config.OPEN_STATUSES)
    return df


def attention(reg: pd.DataFrame, today: date, levels=None) -> dict[str, pd.DataFrame]:
    """Сигналы «требует внимания» по открытым рискам."""
    r = reg[reg["is_open"]]
    crit_min = max(lv["min"] for lv in (levels or config.DEFAULT_LEVELS))
    due = r["due_date"].apply(to_date)
    last = r["assessed_on"].apply(to_date)
    return {
        "critical": r[r["score"].fillna(0) >= crit_min],
        "grown": r[r["delta"].fillna(0) > 0],
        "no_owner": r[r["owner"].fillna("").astype(str).str.strip() == ""],
        "unassessed": r[r["score"].isna()],
        "overdue": r[due.apply(lambda d: d is not None and d < today)],
        "due_soon": r[due.apply(lambda d: d is not None and today <= d <= today + timedelta(days=config.DUE_SOON_DAYS))],
        "stale": r[last.apply(lambda d: d is not None and (today - d).days > config.STALE_DAYS)],
    }


def _missing(v) -> bool:
    if v is None or v is pd.NA or v is pd.NaT:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False
