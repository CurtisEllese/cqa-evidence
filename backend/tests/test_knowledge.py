"""Only synthetic facts; loading a review candidate must never activate it."""
import json
from pathlib import Path

import pytest

from backend.app import create_app
from backend.knowledge import KnowledgeConfigurationError, MAX_KNOWLEDGE_BYTES, load_knowledge_facts

FIXTURE = Path(__file__).parent / "fixtures/knowledge-facts.synthetic.json"


def test_no_configuration_and_explicit_empty_file(tmp_path):
    assert load_knowledge_facts(None) == ()
    path = tmp_path / "empty.json"
    path.write_text("[]")
    assert load_knowledge_facts(path) == ()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig"])
def test_valid_facts_preserve_all_fields_and_unicode(tmp_path, encoding):
    path = tmp_path / "facts.json"
    expected = json.loads(FIXTURE.read_text())
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding=encoding)
    assert [fact.model_dump() for fact in load_knowledge_facts(path)] == expected


@pytest.mark.parametrize("patch", [
    {"effective_date": "2026-02-30"}, {"effective_date": "today"}, {"effective_date": None},
    {"source": " "}, {"verified_by": ""}, {"product": 12}, {"jurisdiction": None},
    {"activation_status": "candidate"},
])
def test_bad_fact_rejects_entire_file_without_echoing_content(tmp_path, patch):
    path = tmp_path / "facts.json"
    fact = json.loads(FIXTURE.read_text())[0]
    fact["text"] = "PRIVATE-SYNTHETIC-CONTENT"
    path.write_text(json.dumps([fact, {**fact, **patch}]))
    with pytest.raises(KnowledgeConfigurationError) as error:
        load_knowledge_facts(path)
    assert "KNOWLEDGE_FACTS_PATH" in str(error.value)
    assert "PRIVATE-SYNTHETIC-CONTENT" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("raw", [b"not JSON", b"null", b"{}", b"[{}]", b"[1]", b"\xff"])
def test_malformed_file_is_configuration_error(tmp_path, raw):
    path = tmp_path / "facts.json"
    path.write_bytes(raw)
    with pytest.raises(KnowledgeConfigurationError):
        load_knowledge_facts(path)


def test_oversized_and_unreadable_files(tmp_path):
    with pytest.raises(KnowledgeConfigurationError):
        load_knowledge_facts(tmp_path / "missing.json")
    with pytest.raises(KnowledgeConfigurationError):
        load_knowledge_facts(tmp_path)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (MAX_KNOWLEDGE_BYTES + 1))
    with pytest.raises(KnowledgeConfigurationError, match="1 МБ"):
        load_knowledge_facts(path)


def test_unapproved_candidate_document_cannot_be_used_as_runtime_facts():
    path = Path(__file__).resolve().parents[2] / "docs/product/knowledge-candidates.json"
    with pytest.raises(KnowledgeConfigurationError):
        load_knowledge_facts(path)


def test_invalid_config_stops_startup_before_creating_database(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_FACTS_PATH", str(tmp_path / "missing-private-facts.json"))
    database = tmp_path / "not-created.sqlite"
    with pytest.raises(KnowledgeConfigurationError):
        create_app(database, tmp_path / "missing-cache.json", organizer_url="")
    assert not database.exists()
