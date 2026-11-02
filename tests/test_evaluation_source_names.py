"""真实抽取/清洗链回归；仅替换模型网络返回，标注不参与预测。"""

import json
from types import SimpleNamespace
import pytest

from tools.text_evaluation.predictors import ProductionPredictor
from tools.text_evaluation.schemas import RunInput
from tools.text_evaluation.runner import run_evaluation


@pytest.mark.parametrize('policy', [None, 'legacy'])
def test_evaluation_preserves_city_names_and_relation_endpoints(monkeypatch, policy):
    from tools import extraction_model as entity_llm_api
    from tools import entity_extraction

    raw = {'entities': [
        {'name': '张明', 'type': '人物', 'attributes': {}},
        {'name': '星河研究院', 'type': '组织', 'attributes': {}},
        {'name': '南京', 'type': '地理空间', 'attributes': {}},
    ], 'relationships': [
        {'source': '张明', 'relation': '任职', 'target': '星河研究院', 'evidence': '张明在星河研究院任职'},
        {'source': '星河研究院', 'relation': '位于', 'target': '南京', 'evidence': '星河研究院位于南京'},
    ], 'resolutions': []}

    class ModelClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw, ensure_ascii=False)))])

        def close(self):
            pass

    monkeypatch.setattr(entity_llm_api, 'OpenAI', ModelClient)
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {'南京': '南京日报'})
    request = RunInput.model_validate({
        'metric': 'entity-relation-extraction',
        'options': {} if policy is None else {'name_mapping_policy': policy},
        'documents': [{'document_id': 'doc', 'filename': 'source.txt', 'language': 'zh', 'text': '张明在星河研究院任职。星河研究院位于南京。'}],
        'annotations': {'documents': [{'document_id': 'doc', 'filename': 'source.txt',
            'entities': [{'entity_id': str(i), 'name': e['name'], 'type': e['type']} for i, e in enumerate(raw['entities'])],
            'relations': [
                {'relation_id': 'r1', 'head_entity_id': '0', 'relationCode': '任职', 'tail_entity_id': '1'},
                {'relation_id': 'r2', 'head_entity_id': '1', 'relationCode': '位于', 'tail_entity_id': '2'},
            ]}]},
    })
    result = run_evaluation(request, predictor=ProductionPredictor())
    names = {e['name'] for e in result['predictions'][0]['graph']['entities']}
    if policy is None:
        assert result['metrics']['entity-extraction']['f1'] > .88
        assert result['metrics']['relation-extraction']['f1'] > .75
        assert names == {'张明', '星河研究院', '南京'}
    else:
        assert result['metrics']['entity-extraction']['f1'] == pytest.approx(2 / 3)
        assert names == {'张明', '星河研究院', '南京日报'}
    # 旧函数及全局名称表未被替换或修改。
    unchanged = entity_extraction.clean_extraction_result(raw, {}, [], None, None, None)
    assert '南京日报' in {e['name'] for e in unchanged['entities']}


def test_source_policy_still_resolves_aliases_explicitly_produced_by_model(monkeypatch):
    from tools import extraction_model as entity_llm_api
    from tools import entity_extraction
    from tools.text_evaluation.source_extraction import extract_entities_for_evaluation
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {'研究院': '其他机构'})
    class Client:
        def __init__(self, **kwargs):
            raw = {'entities': [
                {'name': '星河研究院', 'type': '组织', 'attributes': {}},
                {'name': '研究院', 'type': '组织', 'attributes': {}},
            ], 'relationships': [], 'resolutions': [{'mention': '研究院', 'canonical_entity': '星河研究院'}]}
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
        def close(self):
            pass
    monkeypatch.setattr(entity_llm_api, 'OpenAI', Client)
    graph = extract_entities_for_evaluation(input_text='星河研究院，简称研究院。', model_params={})
    assert {e['name'] for e in graph['entities']} == {'星河研究院'}


def test_source_policy_enforces_public_entity_and_relation_schema(monkeypatch):
    from tools import extraction_model as entity_llm_api
    from tools import entity_extraction
    from tools.text_evaluation.source_extraction import extract_entities_for_evaluation
    raw = {'entities': [
        {'name': '星河研究院', 'type': '组织', 'attributes': {}},
        {'name': '南京', 'type': '地理空间', 'attributes': {}},
        {'name': '质量检查', 'type': '事项', 'attributes': {}},
    ], 'relationships': [
        {'source': '星河研究院', 'relation': '位于', 'target': '南京', 'evidence': '星河研究院位于南京'},
        {'source': '质量检查', 'relation': '执行', 'target': '星河研究院', 'evidence': '质量检查由星河研究院执行'},
    ], 'resolutions': []}
    class Client:
        def __init__(self, **kwargs):
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
        def close(self):
            pass
    monkeypatch.setattr(entity_llm_api, 'OpenAI', Client)
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {})
    graph = extract_entities_for_evaluation(input_text='星河研究院位于南京。质量检查由星河研究院执行。', model_params={
        'entityTypes': ['组织', '地理空间'], 'relationTypes': ['位于'], 'entityAttributeSchema': {},
    })
    assert {e['name'] for e in graph['entities']} == {'星河研究院', '南京'}
    assert [r['relationCode'] for r in graph['relations']] == ['位于']
