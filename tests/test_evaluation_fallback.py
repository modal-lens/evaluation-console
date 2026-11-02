from copy import deepcopy
import json

import pytest

from tools.text_evaluation import fallback
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.schemas import RunInput
from tools.text_evaluation.templates import template


class EmptyPredictor:
    def extract(self, document, options, **kwargs):
        return {'source_doc': document.document_id, 'language': document.language, 'entities': [], 'relations': [], 'resolutions': []}

    def classify(self, document, options, **kwargs):
        return {'tags': []}

    def resolve(self, *args, **kwargs):
        return []

    def fuse(self, *args, **kwargs):
        return {'graph': {}, 'trace': {'entities': {}, 'relations': {}, 'attributes': {}}}

    def communities(self, *args, **kwargs):
        return {'nodes': []}


def package(tmp_path, metric):
    data = template(metric)
    root = tmp_path / 'packages'
    directory = root / fallback.PACKAGES[metric]
    (directory / 'texts').mkdir(parents=True)
    for name, value in (('gold.json', data['annotations']), ('predictions.json', {'metric': metric, 'prediction_input': data['prediction_example']}), ('options.json', data['options'])):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    for doc in data['texts']:
        (directory / 'texts' / doc['filename']).write_text(doc['text'] + '\n', encoding='utf-8')
    by_filename = {doc['filename']: doc for doc in data['annotations']['documents']}
    request = RunInput.model_validate({
        'metric': metric, 'annotations': data['annotations'], 'options': data['options'],
        'documents': [{'document_id': by_filename[doc['filename']]['document_id'], 'filename': doc['filename'],
                       'language': by_filename[doc['filename']]['language'], 'text': doc['text']} for doc in data['texts']],
    })
    return root, directory, request


@pytest.mark.parametrize('metric', list(fallback.PACKAGES))
def test_all_modules_recompute_matching_predictions_and_keep_low_text_report(tmp_path, monkeypatch, metric):
    root, directory, request = package(tmp_path, metric)
    def forbidden(*args, **kwargs):
        raise AssertionError('Cached predictions must never initialize an algorithm predictor')
    monkeypatch.setattr('tools.text_evaluation.runner.ProductionPredictor', forbidden)
    report = fallback.run_evaluation_with_fallback(request, predictor=EmptyPredictor(), cache_root=root)
    assert report['prediction_source'] == 'cached_predictions'
    assert report['submitted_input_mode'] == 'text'
    assert report['input_mode'] == 'precomputed'
    assert report['fallback']['status'] == 'applied'
    assert all(value == 0 for value in report['fallback']['original_scores'].values())
    assert all(value == 1 for value in report['fallback']['cached_scores'].values())
    assert report['fallback']['original_report']['input_mode'] == 'text'
    assert report['fallback']['original_report']['predictions'] != report['predictions']
    assert report['documents'] == [doc.model_dump() for doc in request.documents]
    assert report['fallback']['predictions_file'].endswith('/predictions.json')


@pytest.mark.parametrize('mismatch', ['text', 'gold', 'missing', 'invalid'])
def test_wrong_or_unavailable_package_never_replaces_text_results(tmp_path, mismatch):
    root, directory, request = package(tmp_path, 'community-discovery')
    if mismatch == 'text':
        request.documents[0].text += '不同的原文内容。'
    elif mismatch == 'gold':
        request.annotations.documents[0].entities[0].name = '不同的人工答案'
    elif mismatch == 'missing':
        (directory / 'predictions.json').unlink()
    else:
        (directory / 'predictions.json').write_text('{', encoding='utf-8')
    report = fallback.run_evaluation_with_fallback(request, predictor=EmptyPredictor(), cache_root=root)
    assert report['input_mode'] == 'text'
    assert 'prediction_source' not in report
    assert report['fallback']['status'] == 'skipped'
    assert report['fallback']['reason']
    assert report['metrics']['accuracy'] == 0


def test_disabled_or_high_score_does_not_read_cached_files(tmp_path, monkeypatch):
    root, _, request = package(tmp_path, 'community-discovery')
    def forbidden(*args, **kwargs):
        raise AssertionError('Fallback must not read package files')
    monkeypatch.setattr(fallback, 'read_limited', forbidden)
    request.allow_cached_fallback = False
    report = fallback.run_evaluation_with_fallback(request, predictor=EmptyPredictor(), cache_root=root)
    assert 'fallback' not in report
    request.allow_cached_fallback = True
    original = {'metrics': {'accuracy': .98, 'correct': 98, 'total': 100}}
    monkeypatch.setattr(fallback, 'run_evaluation', lambda *args, **kwargs: original)
    assert fallback.run_evaluation_with_fallback(request, cache_root=root) is original


def test_direct_prediction_mode_never_falls_back(tmp_path, monkeypatch):
    root, _, request = package(tmp_path, 'community-discovery')
    from tools.text_evaluation.schemas import PredictionBundle
    request.predictions = PredictionBundle(documents=deepcopy(request.annotations.documents))
    request.predictions.documents[0].entities[0].community = None
    def forbidden(*args, **kwargs):
        raise AssertionError('Explicit prediction upload must be scored as-is')
    monkeypatch.setattr(fallback, 'read_limited', forbidden)
    report = fallback.run_evaluation_with_fallback(request, cache_root=root)
    assert report['metrics']['accuracy'] < .90
    assert 'fallback' not in report


def test_cached_options_differences_are_visible_and_cancel_is_not_swallowed(tmp_path):
    root, _, request = package(tmp_path, 'community-discovery')
    request.options.block_size = 1234
    report = fallback.run_evaluation_with_fallback(request, predictor=EmptyPredictor(), cache_root=root)
    assert report['fallback']['options_changes']['block_size'] == {'text': 1234, 'cached': 1000}
    assert report['fallback']['original_report']['options']['block_size'] == 1234
    assert report['options']['block_size'] == 1000
    cancelled = False
    class Cancelled(Exception):
        pass
    def progress(stage, done, total):
        nonlocal cancelled
        if stage == '评测完成':
            cancelled = True
    def checkpoint():
        if cancelled:
            raise Cancelled()
    with pytest.raises(Cancelled):
        fallback.run_evaluation_with_fallback(request, predictor=EmptyPredictor(), cache_root=root,
                                               progress=progress, checkpoint=checkpoint)


def test_exact_threshold_is_low_and_document_order_does_not_matter(tmp_path, monkeypatch):
    root, _, request = package(tmp_path, 'cross-document/fusion')
    request.documents.reverse()
    request.annotations.documents.reverse()
    actual = run_evaluation
    def controlled(req, **kwargs):
        if req.predictions is None:
            return {'metrics': {name: {'f1': .90, 'precision': .90, 'recall': .90} for name in ('cross-document/entity-fusion', 'cross-document/relation-fusion', 'cross-document/attribute-fusion')}}
        return actual(req, **kwargs)
    monkeypatch.setattr(fallback, 'run_evaluation', controlled)
    report = fallback.run_evaluation_with_fallback(request, cache_root=root)
    assert report['fallback']['status'] == 'applied'
    assert all(score == .90 for score in report['fallback']['original_scores'].values())


@pytest.mark.parametrize('enabled', [True, False])
def test_default_job_manager_honors_api_fallback_switch(tmp_path, monkeypatch, enabled):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apis.evaluation_runs_api import router, get_manager
    from tools.text_evaluation.jobs import JobManager
    from tests.test_evaluation_runs_api import wait

    root, _, request = package(tmp_path, 'community-discovery')
    monkeypatch.setenv('NANJING_EVALUATION_PREDICTIONS', str(root))
    monkeypatch.setattr('tools.text_evaluation.runner.ProductionPredictor', EmptyPredictor)
    manager = JobManager(tmp_path / 'default-jobs.sqlite')
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_manager] = lambda: manager
    client = TestClient(app)
    try:
        files = [('texts', (doc.filename, doc.text.encode('utf-8'), 'text/plain')) for doc in request.documents]
        files.append(('annotations', ('gold.json', request.annotations.model_dump_json().encode('utf-8'), 'application/json')))
        response = client.post('/api/v1/evaluation-runs/community-discovery', files=files,
                               data={'options': request.options.model_dump_json(), 'allow_cached_fallback': str(enabled).lower()})
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded', job
        if enabled:
            assert job['result']['prediction_source'] == 'cached_predictions'
            assert job['result']['metrics']['accuracy'] == 1
            assert job['result']['fallback']['original_report']['metrics']['accuracy'] == 0
        else:
            assert job['result']['metrics']['accuracy'] == 0
            assert 'fallback' not in job['result']
    finally:
        manager.close()


@pytest.mark.parametrize('metric', list(fallback.PACKAGES))
def test_model_connection_failure_uses_only_matching_cache_without_fake_text_score(tmp_path, metric):
    import httpx
    from openai import APIConnectionError
    from utils.exceptions import LLMServiceError
    class Disconnected(EmptyPredictor):
        def extract(self, *args, **kwargs):
            try:
                raise APIConnectionError(request=httpx.Request('POST', 'http://model.invalid/v1/chat/completions'))
            except APIConnectionError as exc:
                raise LLMServiceError('private debug details must not be returned') from exc
        classify = extract
    root, _, request = package(tmp_path, metric)
    report = fallback.run_evaluation_with_fallback(request, predictor=Disconnected(), cache_root=root)
    assert report['prediction_source'] == 'cached_predictions'
    assert report['fallback']['trigger'] == 'model_connection_error'
    assert report['fallback']['original_scores'] == {}
    assert 'original_report' not in report['fallback']
    assert report['fallback']['text_error']['code'] == 'model_connection_failed'
    assert all(value == 1 for value in report['fallback']['cached_scores'].values())
    assert 'private debug details' not in json.dumps(report)


class ConnectionFailure(EmptyPredictor):
    def extract(self, *args, **kwargs):
        import httpx
        raise httpx.ConnectError('connection refused', request=httpx.Request('POST', 'http://model.invalid/v1/chat/completions'))
    classify = extract


@pytest.mark.parametrize('reason', ['disabled', 'different_text', 'missing_cache'])
def test_model_failure_without_usable_cache_is_clear_error_not_zero_score(tmp_path, reason):
    root, directory, request = package(tmp_path, 'community-discovery')
    if reason == 'disabled':
        request.allow_cached_fallback = False
    elif reason == 'different_text':
        request.documents[0].text += '其他文本'
    else:
        (directory / 'predictions.json').unlink()
    with pytest.raises(fallback.ModelConnectionError, match='模型服务连接失败') as error:
        fallback.run_evaluation_with_fallback(request, predictor=ConnectionFailure(), cache_root=root)
    assert error.value.retryable is True
    assert error.value.stage == 'model_connection'


def test_programming_or_model_format_errors_do_not_trigger_connection_fallback(tmp_path):
    root, _, request = package(tmp_path, 'community-discovery')
    class Broken(EmptyPredictor):
        def extract(self, *args, **kwargs):
            raise ValueError('invalid extraction format')
    with pytest.raises(ValueError, match='invalid extraction format'):
        fallback.run_evaluation_with_fallback(request, predictor=Broken(), cache_root=root)


@pytest.mark.parametrize('cache_available', [True, False])
def test_job_manager_reports_connection_failure_or_reuses_matching_predictions(tmp_path, monkeypatch, cache_available):
    from tools.text_evaluation.jobs import JobManager
    import time
    root, directory, request = package(tmp_path, 'community-discovery')
    if not cache_available:
        (directory / 'predictions.json').unlink()
    monkeypatch.setenv('NANJING_EVALUATION_PREDICTIONS', str(root))
    monkeypatch.setattr('tools.text_evaluation.runner.ProductionPredictor', ConnectionFailure)
    manager = JobManager(tmp_path / 'connection-jobs.sqlite')
    try:
        job = manager.submit(request)
        deadline = time.monotonic() + 3
        while job['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
            job = manager.get(job['job_id'])
        if cache_available:
            assert job['status'] == 'succeeded'
            assert job['result']['fallback']['trigger'] == 'model_connection_error'
        else:
            assert job['status'] == 'failed'
            assert job['result'] is None
            assert job['error']['code'] == 'evaluation_model_connection_failed'
            assert '模型服务连接失败' in job['error']['message']
    finally:
        manager.close()
