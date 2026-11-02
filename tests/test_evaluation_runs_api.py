import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apis.evaluation_runs_api import router, get_manager
from tools.text_evaluation.jobs import JobManager
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.schemas import SUPPORTED_METRICS, RunInput
from tools.text_evaluation.templates import template
from utils.exceptions import AppError


def make_client(tmp_path, runner):
    manager = JobManager(tmp_path / 'jobs.sqlite', runner=runner)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_manager] = lambda: manager

    @app.exception_handler(AppError)
    async def handle(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={'success': False, 'data': None, 'error': exc.to_error()})
    return TestClient(app), manager


def upload(client, metric, data=None):
    data = data or template(metric)
    files = [('texts', (x['filename'], x['text'].encode('utf-8'), 'text/plain')) for x in data['texts']]
    files.append(('annotations', ('annotations.json', json.dumps(data['annotations']).encode('utf-8'), 'application/json')))
    return client.post('/api/v1/evaluation-runs/' + metric, files=files, data={'options': json.dumps(data['options'])})


def wait(client, job_id):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        job = client.get('/api/v1/evaluation-runs/jobs/' + job_id).json()['data']
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            return job
        time.sleep(.01)
    raise AssertionError('job did not finish')


def test_upload_job_poll_and_download_report_use_real_scoring(tmp_path):
    from tests.test_text_evaluation_runner import Predictor
    client, manager = make_client(tmp_path, lambda request, **kwargs: run_evaluation(request, predictor=Predictor(), **kwargs))
    try:
        response = upload(client, 'relation-extraction')
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded'
        assert job['result']['metrics']['f1'] == pytest.approx(2 / 3)
        assert job['progress'] == 100
        assert job['result']['documents'][0]['text'].startswith('南京大学')
        assert job['result']['annotations']['documents'][0]['entities'][0]['entity_id'] == 'person-1'
    finally:
        manager.close()


@pytest.mark.parametrize('metric', SUPPORTED_METRICS)
def test_precomputed_upload_scores_without_any_algorithm_calls(tmp_path, monkeypatch, metric):
    from tools.text_evaluation.precomputed import parse_predictions
    def forbidden(*args, **kwargs):
        raise AssertionError('已有预测结果时禁止初始化或调用预测器')
    monkeypatch.setattr('tools.text_evaluation.runner.ProductionPredictor', forbidden)
    client, manager = make_client(tmp_path, run_evaluation)
    data = template(metric)
    try:
        files = [
            ('annotations', ('gold.json', json.dumps(data['annotations']).encode(), 'application/json')),
            ('predictions', ('predictions.json', json.dumps(data['prediction_example']).encode(), 'application/json')),
            # Uploaded predictions take precedence, even when unusable source text is present.
            ('texts', ('unused.bin', b'\x00', 'application/octet-stream')),
        ]
        response = client.post('/api/v1/evaluation-runs/' + metric, files=files, data={'options': json.dumps(data['options'])})
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded', job
        report = job['result']
        assert report['input_mode'] == 'precomputed'
        if metric != 'multilabel-classification':
            assert 'label_names' not in report['prediction_input']
        # Both the dedicated export and a complete legacy report can be re-scored.
        request = RunInput.model_validate({
            'metric': metric, 'documents': report['documents'],
            'annotations': data['annotations'], 'options': data['options'],
            'predictions': parse_predictions(report, metric),
        })
        assert run_evaluation(request)['metrics'] == report['metrics']
        legacy = {key: value for key, value in report.items() if key != 'prediction_input'}
        request.predictions = parse_predictions({'job': {'result': legacy}}, metric)
        assert run_evaluation(request)['metrics'] == report['metrics']
    finally:
        manager.close()


def test_classification_names_survive_upload_report_and_export(tmp_path):
    data = template('multilabel-classification')
    data['prediction_example']['label_names'] = {'1': '科研'}
    client, manager = make_client(tmp_path, run_evaluation)
    try:
        response = client.post('/api/v1/evaluation-runs/multilabel-classification', files=[
            ('annotations', ('gold.json', json.dumps(data['annotations']).encode(), 'application/json')),
            ('predictions', ('predictions.json', json.dumps(data['prediction_example']).encode(), 'application/json')),
        ])
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded', job
        result = job['result']
        assert result['details']['label_names'] == {'1': '科研'}
        assert result['details']['classification_samples'][0]['predicted'] == [{'id': '1', 'name': '科研'}]
        assert result['details']['classification_samples'][0]['gold'] == [{'id': '1', 'name': '科研'}]
        assert result['prediction_input']['label_names'] == {'1': '科研'}
        assert result['metrics']['micro']['f1'] == 1
    finally:
        manager.close()


def test_precomputed_mismatch_is_rejected_before_scoring(tmp_path):
    called = []
    client, manager = make_client(tmp_path, lambda request, **kwargs: called.append(request))
    data = template('entity-relation-extraction')
    prediction = json.loads(json.dumps(data['prediction_example']))
    prediction['documents'][0]['document_id'] = 'wrong-document'
    try:
        response = client.post('/api/v1/evaluation-runs/entity-relation-extraction', files=[
            ('annotations', ('gold.json', json.dumps(data['annotations']).encode(), 'application/json')),
            ('predictions', ('predictions.json', json.dumps(prediction).encode(), 'application/json')),
        ])
        assert response.status_code == 400
        assert called == []
    finally:
        manager.close()


@pytest.mark.parametrize('target', ['annotations', 'predictions'])
def test_options_uploaded_as_evaluation_data_has_actionable_error(tmp_path, target):
    client, manager = make_client(tmp_path, lambda request, **kwargs: {})
    data = template('entity-relation-extraction')
    try:
        files = [('annotations', ('gold.json', json.dumps(data['annotations']).encode(), 'application/json'))]
        if target == 'annotations':
            files = []
        files.append((target, ('options.json', json.dumps(data['options']).encode(), 'application/json')))
        if target == 'annotations':
            files.append(('texts', ('document-1.txt', b'text', 'text/plain')))
        response = client.post('/api/v1/evaluation-runs/entity-relation-extraction', files=files)
        assert response.status_code == 400
        assert '运行配置' in response.json()['error']['message']
        assert 'options.json' in response.json()['error']['message']
    finally:
        manager.close()


def test_raw_business_json_is_scored_against_separately_uploaded_gold(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('不允许重新预测')
    monkeypatch.setattr('tools.text_evaluation.runner.ProductionPredictor', forbidden)
    client, manager = make_client(tmp_path, run_evaluation)
    data = template('entity-relation-extraction')
    prediction = {'success': True, 'data': {'entities': [
        {'entity_id': 'pred-person', 'name': '张三', 'type': '人物', 'attributes': {}, 'description': '业务字段'},
        {'entity_id': 'pred-org', 'name': '南京大学', 'type': '组织', 'attributes': {}},
    ], 'relations': [{'head_entity_id': 'pred-person', 'relationCode': '任职', 'tail_entity_id': 'pred-org'}],
       'resolutions': [{'entity_id': 'pred-org', 'standard_name': '南京大学', 'aliases': ['南大']}]}}
    try:
        response = client.post('/api/v1/evaluation-runs/entity-relation-extraction', files=[
            ('annotations', ('gold.json', json.dumps(data['annotations']).encode(), 'application/json')),
            ('predictions', ('predictions.json', json.dumps(prediction).encode(), 'application/json')),
        ])
        assert response.status_code == 202, response.text
        report = wait(client, response.json()['data']['job_id'])['result']
        assert report['metrics']['entity-extraction']['f1'] == pytest.approx(.8)
        assert report['metrics']['relation-extraction']['f1'] == pytest.approx(2 / 3)
        assert len(report['prediction_input']['documents'][0]['entities']) == 2
        request = RunInput.model_validate({
            'metric': 'entity-relation-extraction', 'documents': report['documents'],
            'annotations': data['annotations'], 'predictions': report['prediction_input'],
        })
        request.annotations.documents[0].entities[0].name = '李四'
        changed = run_evaluation(request)
        assert changed['metrics']['entity-extraction']['f1'] == pytest.approx(.4)
        assert changed['metrics']['relation-extraction']['f1'] == 0
    finally:
        manager.close()


@pytest.mark.parametrize('metric', SUPPORTED_METRICS)
def test_every_downloadable_template_validates_and_has_registered_upload_route(tmp_path, metric):
    client, manager = make_client(tmp_path, lambda request, **kwargs: {'metric': request.metric})
    try:
        data = client.get('/api/v1/evaluation-runs/templates/' + metric).json()['data']
        response = upload(client, metric, data)
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded'
        assert job['result']['metric'] == metric
    finally:
        manager.close()


def test_cross_language_template_enables_llm_resolution():
    assert template('cross-language/fusion')['options']['use_llm_resolution'] is True


@pytest.mark.parametrize('metric', ['cross-document/fusion', 'cross-language/fusion'])
def test_fusion_template_attributes_match_annotated_sample(metric):
    data = template(metric)
    assert data['options']['entity_attributes'] == {'组织': ['所在地', '成立年份']}
    assert all('成立于1902年' in item['text'] or 'founded in 1902' in item['text'] for item in data['texts'])
    assert all(
        {attribute['key'] for attribute in document['attributes']} == {'所在地', '成立年份'}
        for document in data['annotations']['documents']
    )


def test_cross_language_upload_defaults_llm_resolution_when_options_are_omitted(tmp_path):
    client, manager = make_client(tmp_path, lambda request, **kwargs: {
        'metric': request.metric,
        'use_llm_resolution': request.options.use_llm_resolution,
    })
    try:
        data = template('cross-language/fusion')
        files = [('texts', (item['filename'], item['text'].encode('utf-8'), 'text/plain')) for item in data['texts']]
        files.append(('annotations', ('annotations.json', json.dumps(data['annotations']).encode('utf-8'), 'application/json')))
        response = client.post('/api/v1/evaluation-runs/cross-language/fusion', files=files)
        assert response.status_code == 202, response.text
        job = wait(client, response.json()['data']['job_id'])
        assert job['status'] == 'succeeded'
        assert job['result']['use_llm_resolution'] is True
    finally:
        manager.close()


def test_invalid_upload_never_runs_prediction(tmp_path):
    called = []
    client, manager = make_client(tmp_path, lambda request, **kwargs: called.append(request))
    try:
        data = template('relation-extraction')
        data['annotations']['documents'][0]['filename'] = 'missing.txt'
        assert upload(client, 'relation-extraction', data).status_code == 400
        data = template('cross-language/entity-fusion')
        for doc in data['annotations']['documents']:
            doc['language'] = 'zh'
        assert upload(client, 'cross-language/entity-fusion', data).status_code == 400
        assert called == []
    finally:
        manager.close()


def test_cancelled_task_cannot_be_overwritten_by_late_completion(tmp_path):
    started, release = Event(), Event()
    def slow(request, **kwargs):
        started.set()
        release.wait(3)
        return {'metrics': {'f1': 1}}
    client, manager = make_client(tmp_path, slow)
    try:
        response = upload(client, 'entity-extraction')
        job_id = response.json()['data']['job_id']
        assert started.wait(1)
        assert client.post('/api/v1/evaluation-runs/jobs/' + job_id + '/cancel').json()['data']['status'] == 'cancelled'
        release.set()
        manager.close()
        job = manager.get(job_id)
        assert job['status'] == 'cancelled'
        assert job['result'] is None
        assert client.post('/api/v1/evaluation-runs/jobs/' + job_id + '/delete').status_code == 200
        assert client.get('/api/v1/evaluation-runs/jobs/' + job_id).status_code == 404
    finally:
        release.set()
        manager.close()


def test_completed_results_survive_manager_restart(tmp_path):
    client, manager = make_client(tmp_path, lambda request, **kwargs: {'metric': request.metric, 'metrics': {'f1': .5}})
    response = upload(client, 'entity-extraction')
    job_id = response.json()['data']['job_id']
    original = wait(client, job_id)
    manager.close()
    reopened = JobManager(tmp_path / 'jobs.sqlite')
    try:
        assert reopened.get(job_id) == original
    finally:
        reopened.close()


def test_concurrent_submission_and_polling_preserve_database_records(tmp_path):
    from tests.test_text_evaluation_algorithms import make_request
    manager = JobManager(tmp_path / 'concurrent.sqlite', runner=lambda request, **kwargs: {'metric': request.metric}, workers=2, capacity=32)
    try:
        request = make_request('entity-extraction')
        with ThreadPoolExecutor(max_workers=12) as clients:
            jobs = list(clients.map(lambda _: manager.submit(request), range(24)))
            for _ in range(5):
                records = list(clients.map(lambda job: manager.get(job['job_id']), jobs))
                assert all(record['status'] in ('queued', 'running', 'succeeded') for record in records)
        manager.close()
        assert all(manager.get(job['job_id'])['status'] == 'succeeded' for job in jobs)
    finally:
        manager.close()
