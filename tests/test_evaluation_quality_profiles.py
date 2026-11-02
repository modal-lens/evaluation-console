import json
from types import SimpleNamespace

import pytest

from tools.text_evaluation.predictors import ProductionPredictor
from tools.text_evaluation.schemas import PredictionOptions, InputDocument


def test_domain_template_covers_equipment_and_technology_profile():
    from tools.text_evaluation.templates import template
    data = template('domain-entity-extraction')
    allowed = set(data['options']['entity_types'] + data['options']['domain_entity_types'])
    assert {'人物', '组织', '装备', '技术'} <= allowed
    assert {'装备', '技术'} <= set(data['options']['domain_entity_types'])
    assert set(data['options']['entity_types']).isdisjoint(data['options']['domain_entity_types'])


@pytest.mark.parametrize(('raw_value', 'expected'), [('2012年', '2012'), ('2012', '2012'), ('2012年至2014年', '2012年至2014年')])
def test_year_attribute_uses_unambiguous_four_digit_format(monkeypatch, raw_value, expected):
    from tools import extraction_model as entity_llm_api
    from tools import entity_extraction
    from tools.text_evaluation.source_extraction import extract_entities_for_evaluation
    raw = {'entities': [{'name': '星河研究院', 'type': '组织', 'attributes': {'成立年份': raw_value}}], 'relationships': [], 'resolutions': []}
    class Client:
        def __init__(self, **kwargs):
            response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(raw)))])
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
        def close(self):
            pass
    monkeypatch.setattr(entity_llm_api, 'OpenAI', Client)
    monkeypatch.setattr(entity_extraction, 'load_mapping_table', lambda: {})
    graph = extract_entities_for_evaluation(input_text='星河研究院成立年份为' + raw_value, model_params={
        'entityTypes': ['组织'], 'entityAttributeSchema': {'组织': {'成立年份': ''}},
    })
    assert graph['entities'][0]['attributes']['成立年份'] == expected


@pytest.mark.parametrize(('policy', 'expected'), [('source', '南京'), ('legacy', '南京日报')])
def test_fusion_respects_same_source_name_policy_as_extraction(monkeypatch, policy, expected):
    from tools import graph_ablation
    monkeypatch.setattr(graph_ablation, 'load_mapping_table', lambda path=None: {'南京': '南京日报'})
    monkeypatch.setattr(graph_ablation.GraphAblationService, '_translate_output_to_chinese', lambda self, graph: graph)
    graphs = [{'source_doc': 'd' + str(index), 'language': 'zh', 'entities': [
        {'entity_id': 'p', 'name': '南京', 'type': '地理空间', 'attributes': {}}
    ], 'relations': []} for index in (1, 2)]
    result = ProductionPredictor().fuse(graphs, PredictionOptions(name_mapping_policy=policy))
    assert result['graph']['entities'][0]['name'] == expected


@pytest.mark.parametrize(('english_city', 'city_alias', 'should_merge'), [('Nanjing', '南京', True), ('Suzhou', '苏州', False)])
def test_semantic_attribute_merge_uses_actual_predicted_entity_aliases(english_city, city_alias, should_merge):
    from copy import deepcopy
    from tools.graph_ablation import AblationStrategy
    from tools.text_evaluation.predictors import make_tracing_service
    graphs = [
        {'source_doc': 'zh', 'language': 'zh', 'entities': [
            {'entity_id': 'org', 'name': '星河研究院', 'type': '组织', 'attributes': {'所在地': '南京'}},
            {'entity_id': 'city', 'name': '南京', 'type': '地理空间', 'attributes': {}},
        ], 'relations': []},
        {'source_doc': 'en', 'language': 'en', 'entities': [
            {'entity_id': 'org', 'name': 'Star River Research Institute', 'aliases': ['星河研究院'], 'type': '组织', 'attributes': {'所在地': english_city}},
            {'entity_id': 'city', 'name': english_city, 'aliases': [city_alias], 'type': '地理空间', 'attributes': {}},
        ], 'relations': []},
    ]
    original = deepcopy(graphs)
    strategy = AblationStrategy(translate_output_to_chinese=False, entity_equivalence={'南京': '南京'})
    old = make_tracing_service()
    old.run(deepcopy(graphs), strategy)
    assert ('en', 'org', '所在地', english_city) not in old.trace['attributes']
    service = make_tracing_service(semantic_attributes=True)
    output = service.run(deepcopy(graphs), strategy)
    if should_merge:
        assert service.trace['attributes'][('en', 'org', '所在地', english_city)] == service.trace['attributes'][('zh', 'org', '所在地', '南京')]
    else:
        assert ('en', 'org', '所在地', english_city) not in service.trace['attributes']
    assert next(e for e in output['entities'] if e['type'] == '组织')['attributes']['所在地'] == '南京'
    assert graphs == original


def test_explicit_resolution_wins_over_conflicting_model_date_and_city(monkeypatch):
    from tools import llm_client
    response = {'resolutions': [
        {'mention': '2026年10月21日', 'dimension': 'time', 'granularity': 'year', 'value': '2025'},
        {'mention': '2026年10月21日', 'dimension': 'time', 'granularity': 'year', 'value': '2026'},
        {'mention': '南京市', 'dimension': 'space', 'granularity': 'city', 'value': '南京'},
        {'mention': '南京市', 'dimension': 'space', 'granularity': 'city', 'value': '南京市'},
    ]}
    monkeypatch.setattr(llm_client, 'call_llm', lambda *args, **kwargs: response)
    document = InputDocument(document_id='d', filename='d.txt', language='zh', text='2026年10月21日，在中国江苏省南京市鼓楼区开展工作，昨天也开展工作。')
    graph = {'entities': [{'name': '中国江苏省南京市鼓楼区', 'type': '地理空间'}]}
    rows = ProductionPredictor().resolve(document, graph, PredictionOptions(reference_date='2026-10-21'))
    values = {(r['mention'], r['dimension'], r['granularity']): r['value'] for r in rows}
    assert values[('2026年10月21日', 'time', 'year')] == '2026'
    assert values[('2026年10月21日', 'time', 'day')] == '2026-10-21'
    assert values[('昨天', 'time', 'day')] == '2026-10-20'
    for mention, level in [('中国', 'country'), ('江苏省', 'province'), ('南京市', 'city'), ('鼓楼区', 'district')]:
        assert values[(mention, 'space', level)] == mention


def test_unknown_conflicting_resolution_is_reported_as_unassigned_not_guessed(monkeypatch):
    from tools import llm_client
    monkeypatch.setattr(llm_client, 'call_llm', lambda *args, **kwargs: {'resolutions': [
        {'mention': '某地', 'dimension': 'space', 'granularity': 'city', 'value': '甲市'},
        {'mention': '某地', 'dimension': 'space', 'granularity': 'city', 'value': '乙市'},
    ]})
    graph = {'entities': []}
    document = InputDocument(document_id='d', filename='d.txt', language='zh', text='在某地开展调研。')
    assert ProductionPredictor().resolve(document, graph, PredictionOptions()) == []
    assert graph['processing']['resolution_warnings'][0]['mention'] == '某地'


def test_invalid_llm_resolution_value_is_skipped_without_failing_valid_items(monkeypatch):
    from tools import llm_client
    monkeypatch.setattr(llm_client, 'call_llm', lambda *args, **kwargs: {'resolutions': [
        {'mention': '目标日', 'dimension': 'time', 'granularity': 'day', 'value': None},
        {'mention': '南京市', 'dimension': 'space', 'granularity': 'city', 'value': '南京市'},
    ]})
    graph = {'entities': []}
    document = InputDocument(document_id='d', filename='d.txt', language='zh', text='记录将日期简称为目标日，地点文字为南京市。')

    result = ProductionPredictor().resolve(document, graph, PredictionOptions())

    assert [(row['mention'], row['value']) for row in result] == [('南京市', '南京市')]
    warning = graph['processing']['resolution_warnings'][0]
    assert warning['mention'] == '目标日'
    assert warning['reason'] == '模型输出字段或标准值格式无效，已忽略'
