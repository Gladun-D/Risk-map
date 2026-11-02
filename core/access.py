"""Роли прототипа. Выбор роли — демонстрационный и защитой не является."""
from __future__ import annotations

ROLES = {
    "Риск-менеджер": "ведёт реестр целиком: импорт, редактирование, настройки",
    "Владелец риска": "оценивает и обновляет меры по своим рискам",
    "Наблюдатель": "только просмотр и выгрузка",
}


def can_edit(role: str, risk_owner: str | None = None, me: str | None = None) -> bool:
    if role == "Риск-менеджер":
        return True
    if role == "Владелец риска":
        return bool(me) and (risk_owner or "").strip() == me
    return False


def can_import(role: str) -> bool:
    return role == "Риск-менеджер"


def can_configure(role: str) -> bool:
    return role == "Риск-менеджер"
