"""Журнал изменений (аудит)."""
import json

import streamlit as st

from views import common


def _pretty(details) -> str:
    if not details:
        return ""
    try:
        data = json.loads(details)
    except (TypeError, ValueError):
        return str(details)
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, dict) and "стало" in v:
                parts.append(f"{k}: {v.get('было') or '—'} → {v.get('стало') or '—'}")
            else:
                parts.append(f"{k}: {v}")
        return "; ".join(parts)
    return str(data)


def render():
    common.sidebar_filters(common.load().reg)
    common.page_header("Журнал изменений", "Кто, когда и что изменил. Последние 500 записей.")
    log = common.get_db().audit_log()
    if log.empty:
        st.write("Записей нет.")
        return
    actions = sorted(log["action"].unique())
    pick = st.pills("Действие", actions, selection_mode="multi", key="jr_actions")
    if pick:
        log = log[log["action"].isin(pick)]
    log = log.assign(details=log["details"].map(_pretty))
    st.dataframe(log, hide_index=True, width="stretch", height=600, column_config={
        "ts": st.column_config.DatetimeColumn("Время", format="DD.MM.YYYY HH:mm"), "actor": "Кто", "role": "Роль",
        "action": "Действие", "code": "Риск", "details": st.column_config.TextColumn("Подробности", width="large")})
