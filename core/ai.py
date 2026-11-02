"""ИИ-подсказки через Claude API: предложить оценку В/Вл и извлечь риски из текста.

Результат — только предложение: в реестр попадает после подтверждения человеком.
Ключ передаётся из приложения (переменная окружения ANTHROPIC_API_KEY или st.secrets).
"""
from __future__ import annotations

import json

from core import config

SYSTEM = (
    "Ты — эксперт по управлению рисками проектов (ГОСТ Р ИСО 31000-2019, PMBOK). "
    "Оцениваешь риски по шкалам 1–5. Отвечай по-русски, кратко и по существу. "
    "Если описания мало для уверенной оценки — ставь средние значения и прямо пиши об этом в обосновании.\n\n"
    "Шкала вероятности:\n{p}\n\nШкала влияния:\n{i}\n\nКатегории: {cats}."
)

ASSESS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "probability": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "impact": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "category": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["idx", "probability", "impact", "category", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "owner": {"type": "string"},
                    "probability": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
                    "impact": {"type": "integer", "enum": [0, 1, 2, 3, 4, 5]},
                },
                "required": ["title", "description", "owner", "probability", "impact"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["risks"],
    "additionalProperties": False,
}


class AIError(RuntimeError):
    pass


def _system(scales: dict | None = None) -> str:
    ps = (scales or {}).get("probability") or config.PROBABILITY_SCALE
    is_ = (scales or {}).get("impact") or config.IMPACT_SCALE
    fmt = lambda sc: "\n".join(f"{k} — {v[0]}: {v[1]}" for k, v in sorted(sc.items(), key=lambda x: int(x[0])))  # noqa: E731
    return SYSTEM.format(p=fmt(ps), i=fmt(is_), cats=", ".join(config.CATEGORIES))


def _call(api_key: str, system: str, user: str, schema: dict) -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=120)
    try:
        # server-side fallback: при отказе классификатора запрос автоматически повторяется на другой модели
        resp = client.beta.messages.create(
            model=config.AI_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError as e:
        raise AIError("Ключ ANTHROPIC_API_KEY не принят API.") from e
    except anthropic.RateLimitError as e:
        raise AIError("Превышен лимит запросов к Claude API, повторите через минуту.") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"Claude API вернул ошибку {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise AIError("Нет связи с Claude API.") from e
    if resp.stop_reason == "refusal":
        raise AIError("Модель отказалась обработать запрос.")
    if resp.stop_reason == "max_tokens":
        raise AIError("Ответ не поместился — уменьшите число рисков в одном запросе.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError("Не удалось разобрать ответ модели.") from e


def suggest_assessments(api_key: str, items: list[dict], scales: dict | None = None) -> list[dict]:
    """items: [{idx, title, description}] → [{idx, probability, impact, category, rationale}]."""
    if not items:
        return []
    payload = [{"idx": int(it["idx"]), "title": it.get("title", ""), "description": (it.get("description") or "")[:2000]}
               for it in items]
    user = ("Оцени вероятность и влияние каждого риска, предложи категорию и дай обоснование в 1–2 предложениях. "
            "Верни элемент для каждого idx.\n\n" + json.dumps(payload, ensure_ascii=False))
    data = _call(api_key, _system(scales), user, ASSESS_SCHEMA)
    valid = {p["idx"] for p in payload}
    return [x for x in data.get("items", []) if x.get("idx") in valid]


def extract_risks(api_key: str, text: str, scales: dict | None = None) -> list[dict]:
    """Риски из неструктурированного текста. probability/impact = 0, если в тексте оценки нет."""
    if not text.strip():
        return []
    user = ("Ниже текст документа. Выдели из него отдельные риски: короткое название (до 90 символов), описание "
            "(причина → событие → последствие, словами документа), владелец, если указан (иначе пустая строка), "
            "вероятность и влияние 1–5, только если они явно указаны в тексте (иначе 0). Ничего не выдумывай.\n\n"
            f"<document>\n{text[:150000]}\n</document>")
    return _call(api_key, _system(scales), user, EXTRACT_SCHEMA).get("risks", [])
