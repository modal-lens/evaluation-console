"""Observe actual model requests made by each evaluation adapter."""

import json
from types import SimpleNamespace

import pytest

from tools.text_evaluation.predictors import ProductionPredictor
from tools.text_evaluation.schemas import InputDocument, PredictionOptions


@pytest.fixture
def model_requests(monkeypatch):
    from tools import llm_client
    from tools import extraction_model as entity_llm_api
    requests = []
    config = {'text_llm_url': 'http://unused/v1', 'text_llm_name': 'fake', 'temperature': .8, 'top_p': .3,
              'presence_penalty': 1, 'frequency_penalty': 1}
    class Client:
        def __init__(self, **kwargs):
            def create(**request):
                requests.append(request)
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                    {'entities': [], 'relationships': [], 'resolutions': [], 'tags': {}})))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        def close(self):
            pass
    monkeypatch.setattr(llm_client, 'OpenAI', Client)
    monkeypatch.setattr(llm_client, 'get_section', lambda name: dict(config))
    monkeypatch.setattr(entity_llm_api, 'OpenAI', Client)
    monkeypatch.setattr(entity_llm_api, 'get_section', lambda name: dict(config))
    return requests


def assert_greedy(request):
    assert request['temperature'] == 0
    assert request['top_p'] == 1
    assert request['frequency_penalty'] == request['presence_penalty'] == 0
    assert request.get('seed', request.get('extra_body', {}).get('seed')) == 42


def test_extraction_overrides_random_sampling_only_inside_evaluation(model_requests, monkeypatch):
    from tools import entity_extraction
    from tools.text_evaluation.source_extraction import extract_entities_for_evaluation
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {})
    extract_entities_for_evaluation(input_text='没有命名对象的背景说明。', model_params={})
    assert_greedy(model_requests[0])


def test_classification_uses_stable_client(model_requests, monkeypatch):
    from tools import text_classification
    def classify(text, parameters, **kwargs):
        kwargs['llm']('classify', parameters, system_prompt='system')
        return {'tags': {'7': '政策'}, 'labels': {'7': '政策'}}
    monkeypatch.setattr(text_classification, 'classify_text', classify)
    ProductionPredictor().classify(InputDocument(document_id='d', filename='d.txt', language='zh', text='政策解读。'), PredictionOptions())
    assert_greedy(model_requests[0])


def test_time_space_uses_stable_client(model_requests):
    ProductionPredictor().resolve(InputDocument(document_id='d', filename='d.txt', language='zh', text='没有时空对象的说明。'), {'entities': []}, PredictionOptions())
    assert_greedy(model_requests[0])


def test_regular_llm_client_keeps_business_sampling(model_requests):
    from tools.llm_client import call_llm
    call_llm('regular business')
    assert model_requests[0]['temperature'] == .8
    assert model_requests[0]['top_p'] == .3
    assert 'seed' not in model_requests[0]


def test_legacy_evaluation_and_regular_extraction_have_separate_sampling(model_requests, monkeypatch):
    from tools import entity_extraction
    from tools import extraction_model as entity_llm_api
    from tools.text_evaluation.source_extraction import extract_entities_for_evaluation
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {})
    extract_entities_for_evaluation(input_text='普通背景。', model_params={}, name_mapping_policy='legacy')
    assert_greedy(model_requests[0])
    entity_llm_api.extract_entities_with_model(input_text='普通业务背景。', model_params={})
    assert model_requests[1]['temperature'] == .8
    assert model_requests[1]['top_p'] == .3
    assert 'seed' not in model_requests[1].get('extra_body', {})


@pytest.mark.parametrize('options', [PredictionOptions(use_llm_resolution=True),
                                    PredictionOptions(name_mapping_policy='legacy', use_llm_resolution=False)])
def test_fusion_uses_stable_client_and_one_resolution_worker(model_requests, monkeypatch, options):
    from tools.text_evaluation import predictors
    class Service:
        trace = {}
        def run(self, graphs, strategy):
            assert strategy.llm_resolution_workers == 1
            self.llm_resolver.llm_client.chat_json('resolve')
            return {'entities': [], 'relations': []}
    monkeypatch.setattr(predictors, 'make_tracing_service', lambda **kwargs: Service())
    ProductionPredictor().fuse([], options)
    assert_greedy(model_requests[0])
