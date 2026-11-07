"""History and provenance survive subsequent attempts and server restarts."""
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.domain import score_evaluation
from backend.evaluator import AdapterFailure
from backend.tests.test_domain import evaluation, appeal
from backend.tests.test_evaluation_api import model_output
from backend.tests.test_api import record


def test_history_keeps_success_when_later_attempt_fails_and_metadata_is_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv('EVALUATOR_ADAPTER', '')
    monkeypatch.delenv('KNOWLEDGE_FACTS_PATH', raising=False)
    config = dict(provider='test-only', model='synthetic-v1', promptVersion='prompt-v1')
    calls = []

    async def adapter(request):
        calls.append(request)
        if len(calls) > 1:
            raise AdapterFailure(retryable=False)
        return model_output(request)

    adapter.describe = lambda: dict(config)
    paths = tmp_path / 'history.sqlite', tmp_path / 'no-cache'
    app = create_app(*paths, organizer_url='', evaluator=adapter)
    item = record('history-test')
    item['transcript'][1]['text'] = 'Здравствуйте! Синтетический ответ.'
    app.state.repo.import_data([item])
    with TestClient(app) as client:
        def start():
            response = client.post('/api/evaluations', json=dict(schema_version='2.0', conversationId='history-test'))
            assert response.status_code == 202
            return response.json()['id']
        first = start()
        config.update(model='synthetic-v2', promptVersion='prompt-v2')
        second = start()
        history = client.get('/api/conversations/history-test/evaluations').json()
        assert len(history) == 2
        by_id = {row['id']: row for row in history}
        assert by_id[first]['status'] == 'completed'
        assert by_id[first]['score']['total'] == '85.38'
        assert by_id[first]['provenance']['model'] == 'synthetic-v1'
        assert by_id[first]['provenance']['promptVersion'] == 'prompt-v1'
        assert by_id[second]['status'] == 'failed' and by_id[second]['score'] is None
        assert by_id[second]['provenance']['model'] == 'synthetic-v2'
        assert client.get('/api/conversations/missing/evaluations').status_code == 404
        assert client.get(f'/api/evaluations/{first}/export').json() == by_id[first]
    with TestClient(create_app(*paths, organizer_url='')) as restarted:
        assert restarted.get('/api/conversations/history-test/evaluations').json() == history


def test_contributions_use_applicable_weights_and_recompute_after_appeal():
    original = score_evaluation(evaluation(), wire_decimals=True)
    assert original['total'] == '85.38'
    assert 'discovery' not in original['breakdown']
    assert 'gross-errors' not in original['breakdown']
    assert original['breakdown']['consultation'] == dict(weightedPoints='15.00', normalizedWeight='30.77', contribution='23.08')
    assert original['breakdown']['dialogue'] == dict(weightedPoints='10.50', normalizedWeight='23.08', contribution='16.15')
    final = score_evaluation(evaluation(), [appeal(newValue=None)], wire_decimals=True)
    assert final['total'] == '90.00'
    assert 'consultation' not in final['breakdown']
    assert final['breakdown']['dialogue']['normalizedWeight'] == '33.33'


def test_builtin_provider_metadata_uses_selected_prompt(monkeypatch):
    from evaluator.provider import describe
    from evaluator.openai_v2 import PROMPT_VERSION as openai_prompt
    monkeypatch.setenv('LLM_MODEL', 'test-model')
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    assert describe() == dict(provider='openai', model='test-model', promptVersion=openai_prompt)
    monkeypatch.setenv('LLM_PROVIDER', 'gemini')
    assert describe()['promptVersion'] == 'unknown'  # No fallback provider is enabled.
