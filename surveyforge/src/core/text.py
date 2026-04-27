import re
from typing import Any, Iterable, List


def normalize_space(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_string_list(value: Any) -> List[str]:
    result = []
    seen = set()
    for item in ensure_list(value):
        text = normalize_space(item)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalize_space(text).lower()).strip("-")
    return slug or "paper"


def join_semicolon(items: Iterable[str]) -> str:
    return "; ".join([normalize_space(item) for item in items if normalize_space(item)])
