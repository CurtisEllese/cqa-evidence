"""The import limit applies to the UTF-8 source, not its JSON transport envelope."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import MAX_IMPORT_BYTES
from backend.tests.test_api import record


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('EVALUATOR_ADAPTER', '')
    app = create_app(tmp_path / 'import.sqlite', tmp_path / 'missing.json', organizer_url='')
    with TestClient(app) as client:
        yield client


def source_at_limit(character=' '):
    item = record('at-limit')
    item['padding'] = ''
    prefix = json.dumps([item], ensure_ascii=False)
    remaining = MAX_IMPORT_BYTES - len(prefix.encode('utf-8'))
    width = len(character.encode('utf-8'))
    item['padding'] = character * (remaining // width) + ' ' * (remaining % width)
    source = json.dumps([item], ensure_ascii=False)
    assert len(source.encode('utf-8')) == MAX_IMPORT_BYTES
    return source


@pytest.mark.parametrize('character,ascii_transport', [(' ', False), ('я', False), ('я', True)])
def test_exact_utf8_limit_imports_and_one_extra_byte_preserves_state(client, character, ascii_transport):
    source = source_at_limit(character)
    body = json.dumps({'schema_version': '2.0', 'text': source}, ensure_ascii=ascii_transport).encode('utf-8')
    assert len(body) > MAX_IMPORT_BYTES
    response = client.post('/api/import', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 200, response.text
    assert response.json()['imported'] == 1
    before = client.get('/api/state').json()
    response = client.post('/api/import', json={'schema_version': '2.0', 'text': source + ' '})
    assert response.status_code == 413
    assert response.json()['code'] == 'REQUEST_TOO_LARGE'
    assert client.get('/api/state').json() == before


def test_maximum_json_escape_expansion_is_allowed(client):
    source = json.dumps([record('escaped')])
    source += '\n' * (MAX_IMPORT_BYTES - len(source.encode('utf-8')))
    body = json.dumps({'schema_version': '2.0', 'text': source}).replace('\\n', '\\u000a').encode()
    response = client.post('/api/import', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize('streamed', [False, True])
def test_transport_still_has_a_bounded_limit(client, streamed):
    from backend.app import MAX_IMPORT_BODY
    before = client.get('/api/state').json()
    body = b' ' * (MAX_IMPORT_BODY + 1)
    content = (body[i:i + 65536] for i in range(0, len(body), 65536)) if streamed else body
    response = client.post('/api/import', content=content, headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert client.get('/api/state').json() == before


def test_other_routes_keep_original_body_limit(client):
    response = client.post('/api/migrate', content=b' ' * (MAX_IMPORT_BYTES + 1),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
