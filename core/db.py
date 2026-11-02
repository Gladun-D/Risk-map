"""Хранилище SQLite: риски, журнал оценок, аудит, настройки.

Один объект RiskDB на процесс (в приложении — через st.cache_resource); все обращения
под общей блокировкой, поэтому одновременные сессии Streamlit не мешают друг другу.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from core import config
from core.rules import to_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    category TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    status TEXT DEFAULT 'Выявлен',
    strategy TEXT DEFAULT '',
    response_plan TEXT DEFAULT '',
    due_date TEXT,
    trigger TEXT DEFAULT '',
    target_p INTEGER,
    target_i INTEGER,
    source TEXT DEFAULT 'manual',
    created_on TEXT NOT NULL,
    closed_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assessment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_id INTEGER NOT NULL REFERENCES risk(id) ON DELETE CASCADE,
    assessed_on TEXT NOT NULL,
    probability INTEGER NOT NULL CHECK (probability BETWEEN 1 AND 5),
    impact INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 5),
    comment TEXT DEFAULT '',
    author TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_assessment_risk ON assessment(risk_id, assessed_on);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT,
    role TEXT,
    action TEXT NOT NULL,
    risk_id INTEGER,
    details TEXT
);
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

RISK_FIELDS = ["code", "title", "description", "category", "owner", "status", "strategy", "response_plan",
               "due_date", "trigger", "target_p", "target_i", "source", "created_on", "closed_on"]
CLOSED_STATUSES = {"Закрыт", "Реализовался"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(d) -> str | None:
    d = to_date(d)
    return d.isoformat() if d else None


class RiskDB:
    def __init__(self, path: Path | str = config.DB_PATH):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- служебное ----------
    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params=()) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql_query(sql, self._conn, params=params)

    def audit(self, action: str, actor: str = "", role: str = "", risk_id: int | None = None, details=None):
        if isinstance(details, (dict, list)):
            details = json.dumps(details, ensure_ascii=False, default=str)
        self._exec("INSERT INTO audit(ts, actor, role, action, risk_id, details) VALUES (?,?,?,?,?,?)",
                   (_now(), actor, role, action, risk_id, details))

    # ---------- настройки ----------
    def get_setting(self, key: str, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        self._exec("INSERT INTO setting(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

    def levels(self) -> list[dict]:
        return self.get_setting("levels", config.DEFAULT_LEVELS)

    # ---------- чтение ----------
    def risks(self) -> pd.DataFrame:
        df = self._query("SELECT * FROM risk ORDER BY code")
        for c in ("created_on", "closed_on"):
            df[c] = df[c].apply(to_date).astype(object)
        for c in ("target_p", "target_i"):
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        for c in ("description", "category", "owner", "strategy", "response_plan", "trigger"):
            df[c] = df[c].fillna("")
        return df

    def assessments(self, risk_id: int | None = None) -> pd.DataFrame:
        if risk_id is None:
            df = self._query("SELECT * FROM assessment ORDER BY risk_id, assessed_on, id")
        else:
            df = self._query("SELECT * FROM assessment WHERE risk_id=? ORDER BY assessed_on, id", (int(risk_id),))
        df["assessed_on"] = df["assessed_on"].apply(to_date).astype(object)
        return df

    def audit_log(self, limit: int = 500) -> pd.DataFrame:
        return self._query("SELECT a.ts, a.actor, a.role, a.action, r.code, a.details FROM audit a "
                           "LEFT JOIN risk r ON r.id = a.risk_id ORDER BY a.id DESC LIMIT ?", (limit,))

    def risk(self, risk_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM risk WHERE id=?", (int(risk_id),)).fetchone()
        return dict(row) if row else None

    def find_risk(self, code: str | None = None, title: str | None = None) -> dict | None:
        with self._lock:
            row = None
            if code:
                row = self._conn.execute("SELECT * FROM risk WHERE lower(code)=lower(?)", (code.strip(),)).fetchone()
            if row is None and title:
                row = self._conn.execute("SELECT * FROM risk WHERE lower(trim(title))=lower(trim(?))", (title,)).fetchone()
        return dict(row) if row else None

    def next_code(self) -> str:
        with self._lock:
            rows = self._conn.execute("SELECT code FROM risk WHERE code LIKE 'R-%'").fetchall()
        nums = [int(r["code"][2:]) for r in rows if r["code"][2:].isdigit()]
        return f"R-{(max(nums) + 1 if nums else 1):03d}"

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM risk").fetchone()[0]

    # ---------- запись ----------
    def create_risk(self, data: dict, actor: str = "", role: str = "") -> int:
        today = date.today()
        row = {k: data.get(k) for k in RISK_FIELDS}
        row["code"] = (row["code"] or "").strip() or self.next_code()
        row["title"] = (row["title"] or "").strip() or "Без названия"
        row["status"] = row["status"] or "Выявлен"
        row["source"] = row["source"] or "manual"
        row["created_on"] = _iso(row["created_on"]) or today.isoformat()
        row["due_date"] = _iso(row["due_date"])
        row["closed_on"] = _iso(row["closed_on"])
        if row["status"] in CLOSED_STATUSES and not row["closed_on"]:
            row["closed_on"] = today.isoformat()
        for k in ("description", "category", "owner", "strategy", "response_plan", "trigger"):
            row[k] = (row[k] or "").strip() if isinstance(row[k], str) or row[k] is None else str(row[k])
        now = _now()
        cols = RISK_FIELDS + ["created_at", "updated_at"]
        vals = [row[k] for k in RISK_FIELDS] + [now, now]
        cur = self._exec(f"INSERT INTO risk({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        rid = cur.lastrowid
        self.audit("Создан риск", actor, role, rid, {"code": row["code"], "title": row["title"], "source": row["source"]})
        return rid

    def update_risk(self, risk_id: int, changes: dict, actor: str = "", role: str = "") -> dict:
        old = self.risk(risk_id)
        if old is None:
            raise KeyError(risk_id)
        diff = {}
        for k, v in changes.items():
            if k not in RISK_FIELDS or k == "created_on":
                continue
            if k in ("due_date", "closed_on"):
                v = _iso(v)
            elif k in ("target_p", "target_i"):
                v = None if v is None or pd.isna(v) else int(v)
            elif v is None:
                v = ""
            if (old.get(k) or None) != (v or None):
                diff[k] = {"было": old.get(k), "стало": v}
        if "status" in diff:
            new_status = diff["status"]["стало"]
            if new_status in CLOSED_STATUSES and not old.get("closed_on"):
                diff["closed_on"] = {"было": None, "стало": date.today().isoformat()}
            elif new_status not in CLOSED_STATUSES and old.get("closed_on"):
                diff["closed_on"] = {"было": old["closed_on"], "стало": None}
        if not diff:
            return {}
        sets = ", ".join(f"{k}=?" for k in diff) + ", updated_at=?"
        self._exec(f"UPDATE risk SET {sets} WHERE id=?", [d["стало"] for d in diff.values()] + [_now(), int(risk_id)])
        self.audit("Изменён риск", actor, role, risk_id, diff)
        return diff

    def add_assessment(self, risk_id: int, probability: int, impact: int, assessed_on=None, comment: str = "",
                       author: str = "", source: str = "manual", role: str = "", log: bool = True) -> int:
        p, i = int(probability), int(impact)
        if not (1 <= p <= config.SCALE and 1 <= i <= config.SCALE):
            raise ValueError("Оценка должна быть от 1 до 5")
        on = _iso(assessed_on) or date.today().isoformat()
        cur = self._exec("INSERT INTO assessment(risk_id, assessed_on, probability, impact, comment, author, source, created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (int(risk_id), on, p, i, comment or "", author or "", source, _now()))
        with self._lock:
            status = self._conn.execute("SELECT status FROM risk WHERE id=?", (int(risk_id),)).fetchone()["status"]
        if status == "Выявлен":
            self._exec("UPDATE risk SET status='Оценён', updated_at=? WHERE id=?", (_now(), int(risk_id)))
        if log:
            self.audit("Оценка риска", author, role, risk_id, {"дата": on, "В": p, "Вл": i, "балл": p * i, "источник": source})
        return cur.lastrowid

    def delete_risk(self, risk_id: int, actor: str = "", role: str = ""):
        old = self.risk(risk_id)
        self._exec("DELETE FROM risk WHERE id=?", (int(risk_id),))
        self.audit("Удалён риск", actor, role, None, {"code": old and old["code"], "title": old and old["title"]})

    def clear_all(self, actor: str = "", role: str = ""):
        with self._lock:
            self._conn.execute("DELETE FROM assessment")
            self._conn.execute("DELETE FROM risk")
            self._conn.commit()
        self.set_setting("demo", False)
        self.audit("Реестр очищен", actor, role)
