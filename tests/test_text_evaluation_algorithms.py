from copy import deepcopy

import pytest

from tools.text_evaluation.schemas import RunInput, InputDocument, METRICS, PredictionOptions
from tools.text_evaluation.templates import template
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.predictors import make_tracing_service
from tools.text_evaluation.predictors import ProductionPredictor
from frontend.tests.fixture_predictor import FixturePredictor


def make_request(metric):
    data = template(metric)
    by_name = {doc['filename']: doc for doc in data['annotations']['documents']}
    return RunInput(metric=metric, annotations=data['annotations'], options=data['options'], documents=[InputDocument(document_id=by_name[x['filename']]['document_id'], filename=x['filename'], language=by_name[x['filename']]['language'], text=x['text']) for x in data['texts']])


@pytest.mark.parametrize('metric', METRICS)
def test_all_auto_metrics_score_generated_predictions(metric):
    request = make_request(metric)
    result = run_evaluation(request, predictor=FixturePredictor())
    assert result['metric'] == metric
    assert len(result['predictions']) == len(request.documents)
    assert result['annotations'] == request.annotations.model_dump()
    score = result['metrics']
    assert 'f1' in score or 'accuracy' in score or 'micro' in score or 'overall' in score
    if metric in ('entity-extraction', 'relation-extraction', 'cross-document/entity-fusion', 'cross-document/relation-fusion', 'cross-document/attribute-fusion'):
        assert score['f1'] == 1
    if metric in ('spatiotemporal-resolution', 'community-discovery'):
        assert score['accuracy'] == 1


def test_tracing_service_preserves_original_business_output():
    from tools.graph_ablation import GraphAblationService, AblationStrategy
    request = make_request('cross-document/attribute-fusion')
    graphs = [FixturePredictor().extract(doc, request.options) for doc in request.documents]
    strategy = AblationStrategy(translate_output_to_chinese=False)
    original = GraphAblationService().run(deepcopy(graphs), strategy)
    tracing = make_tracing_service()
    traced = tracing.run(deepcopy(graphs), strategy)
    assert traced == original
    assert tracing.trace['entities'][('doc-1', 'p-org')] == tracing.trace['entities'][('doc-2', 'p-org')]
    assert tracing.trace['relations'][('doc-1', 0)] == tracing.trace['relations'][('doc-2', 0)]
    assert tracing.trace['attributes'][('doc-1', 'p-org', '所在地', '南京')] == tracing.trace['attributes'][('doc-2', 'p-org', '所在地', '南京')]


def test_fusion_missing_objects_are_counted_and_coverage_exposed():
    class Missing(FixturePredictor):
        def extract(self, document, options, **kwargs):
            graph = super().extract(document, options, **kwargs)
            if document.document_id == 'doc-2':
                graph['entities'] = [e for e in graph['entities'] if e['entity_id'] != 'p-city']
                graph['relations'] = graph['relations'][:1]
            return graph
    result = run_evaluation(make_request('cross-document/entity-fusion'), predictor=Missing())
    assert result['metrics']['fn'] == 1
    assert result['metrics']['object_coverage']['fn'] == 1
    assert result['metrics']['f1'] == pytest.approx(.8)


def test_community_isolated_or_missing_nodes_are_not_silently_dropped():
    class NoEdges(FixturePredictor):
        def extract(self, document, options, **kwargs):
            graph = super().extract(document, options, **kwargs)
            graph['relations'] = []
            return graph
    result = run_evaluation(make_request('community-discovery'), predictor=NoEdges())
    assert result['metrics']['total'] == 3
    assert result['metrics']['accuracy'] == 0


def test_domain_runs_baseline_and_domain_configs_separately():
    result = run_evaluation(make_request('domain-entity-extraction'), predictor=FixturePredictor())
    assert result['metrics']['overall']['f1'] == 1
    assert result['metrics']['baseline']['f1'] == pytest.approx(2 / 3)
    assert result['metrics']['f1_delta'] == pytest.approx(1 / 3)


def test_adapter_covers_long_text_instead_of_original_four_chunk_limit(monkeypatch):
    import tools.text_evaluation.source_extraction
    seen = []
    def extract(*, input_text, model_params, name_mapping_policy):
        seen.append(input_text)
        name = '尾段实体' if '乙' in input_text else '首段实体'
        return {'entities': [{'entity_id': name, 'name': name, 'type': '组织', 'attributes': {}}], 'relations': [], 'resolutions': []}
    monkeypatch.setattr(tools.text_evaluation.source_extraction, 'extract_entities_for_evaluation', extract)
    text = '甲' * 600 + '乙' * 600
    document = InputDocument(document_id='long', filename='long.txt', language='zh', text=text)
    result = ProductionPredictor().extract(document, PredictionOptions(block_size=100))
    assert ''.join(seen) == text
    assert len(seen) > 4
    assert {e['name'] for e in result['entities']} == {'首段实体', '尾段实体'}


def test_classifier_adapter_covers_entire_text(monkeypatch):
    import tools.text_classification
    seen = []
    def classify(text, parameters, *, llm):
        assert callable(llm)
        seen.append(text)
        return {'tags': {'2' if '乙' in text else '1': '标签'}}
    monkeypatch.setattr(tools.text_classification, 'classify_text', classify)
    text = '甲' * 2500 + '乙' * 2500
    document = InputDocument(document_id='long', filename='long.txt', language='zh', text=text)
    result = ProductionPredictor().classify(document, PredictionOptions())
    assert ''.join(seen) == text
    assert max(map(len, seen)) <= 2048
    assert result['tags'] == ['1', '2']


def test_same_name_with_different_types_keeps_distinct_source_alignment():
    from tools.graph_ablation import AblationStrategy
    graphs = [
        {'source_doc': 'd1', 'language': 'zh', 'entities': [{'entity_id': 'a', 'name': '同名对象', 'type': '人物', 'attributes': {}}], 'relations': []},
        {'source_doc': 'd2', 'language': 'en', 'entities': [{'entity_id': 'b', 'name': '同名对象', 'type': '组织', 'attributes': {}}], 'relations': []},
    ]
    service = make_tracing_service()
    result = service.run(graphs, AblationStrategy(translate_output_to_chinese=False))
    assert len(result['entities']) == 2
    assert service.trace['entities'][('d1', 'a')] != service.trace['entities'][('d2', 'b')]
    assert len({item['entity_id'] for item in result['entities']}) == 2
