"""Load the expert-approved server file without exposing its contents in errors."""
from pathlib import Path
from datetime import date

from pydantic import TypeAdapter, ValidationError

from .contracts.v2 import KnowledgeFact

MAX_KNOWLEDGE_BYTES = 1024 * 1024


class KnowledgeConfigurationError(ValueError):
    """An explicitly configured, unusable file must not become an empty reference."""


def load_knowledge_facts(path: str | Path | None) -> tuple[KnowledgeFact, ...]:
    if path is None:
        return ()
    try:
        with Path(path).open("rb") as source:
            raw = source.read(MAX_KNOWLEDGE_BYTES + 1)
    except OSError:
        raise KnowledgeConfigurationError("KNOWLEDGE_FACTS_PATH: не удалось прочитать файл фактов.") from None
    if len(raw) > MAX_KNOWLEDGE_BYTES:
        raise KnowledgeConfigurationError("KNOWLEDGE_FACTS_PATH: файл превышает 1 МБ.")
    try:
        facts = TypeAdapter(list[KnowledgeFact]).validate_json(raw.decode("utf-8-sig"))
        for fact in facts:
            date.fromisoformat(fact.effective_date)
    except (ValidationError, ValueError):
        # Pydantic's original error includes the rejected input, possibly private facts.
        raise KnowledgeConfigurationError(
            "KNOWLEDGE_FACTS_PATH: ожидается JSON-массив KnowledgeFact с корректными датами действия."
        ) from None
    return tuple(facts)
