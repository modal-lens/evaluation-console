from copy import deepcopy

import pytest

from tools.graph_ablation import AblationStrategy
from tools.text_evaluation.predictors import make_tracing_service
from utils.exceptions import ValidationAppError


def graphs():
    return [
        {'source_doc': 'doc-1', 'language': 'zh', 'entities': [
            {'entity_id': 'e1', 'name': '同名对象', 'type': '人物', 'attributes': {'编号': 'P'}},
            {'entity_id': 'e2', 'name': '甲研究院', 'type': '组织', 'attributes': {}},
        ], 'relations': [{'head_entity_id': 'e1', 'relationCode': '任职', 'tail_entity_id': 'e2'}]},
        {'source_doc': 'doc-2', 'language': 'zh', 'entities': [
            {'entity_id': 'e1', 'name': '同名对象', 'type': '组织', 'attributes': {'编号': 'O'}},
            {'entity_id': 'e2', 'name': '乙档案馆', 'type': '组织', 'attributes': {}},
        ], 'relations': [{'head_entity_id': 'e1', 'relationCode': '合作', 'tail_entity_id': 'e2'}]},
    ]


def test_homonyms_keep_distinct_sources_and_relationship_endpoints():
    service = make_tracing_service(semantic_attributes=True)
    result = service.run(graphs(), AblationStrategy(similarity_threshold=1, translate_output_to_chinese=False))
    entities = {e['entity_id']: e for e in result['entities']}
    assert len(result['entities']) == len(entities) == 4
    assert len(set(service.trace['entities'].values())) == 4
    assert len(set(service.trace['output_ids'].values())) == 4
    assert service.trace['entities'][('doc-1', 'e1')] != service.trace['entities'][('doc-2', 'e1')]
    relations = [(entities[r['head_entity_id']]['type'], r['relationCode'], entities[r['tail_entity_id']]['name']) for r in result['relations']]
    assert set(relations) == {('人物', '任职', '甲研究院'), ('组织', '合作', '乙档案馆')}


def test_source_ids_can_repeat_across_documents_without_output_collisions():
    data = graphs()
    data[1]['entities'][0]['name'] = '乙公司'
    service = make_tracing_service()
    result = service.run(data, AblationStrategy(similarity_threshold=1, translate_output_to_chinese=False))
    assert len({e['entity_id'] for e in result['entities']}) == len(result['entities']) == 4
    assert len(result['relations']) == 2


def test_genuine_same_entity_still_merges_and_ids_are_repeatable():
    data = graphs()
    data[1]['entities'][0]['type'] = '人物'
    data[1]['entities'][0]['attributes'] = {'编号': 'P'}
    service = make_tracing_service()
    strategy = AblationStrategy(similarity_threshold=1, translate_output_to_chinese=False)
    first = service.run(deepcopy(data), strategy)
    assert service.trace['entities'][('doc-1', 'e1')] == service.trace['entities'][('doc-2', 'e1')]
    assert len(first['entities']) == 3
    second = service.run(deepcopy(data), strategy)
    assert first == second


def test_duplicate_source_id_inside_one_document_is_still_rejected():
    data = graphs()[:1]
    data[0]['entities'][1]['entity_id'] = 'e1'
    with pytest.raises(ValidationAppError, match='同一.*实体 ID.*重复'):
        make_tracing_service().run(data, AblationStrategy(translate_output_to_chinese=False))


def test_community_run_scores_homonyms_as_distinct_nodes_with_real_fusion():
    from tools.text_evaluation.predictors import ProductionPredictor
    from tools.text_evaluation.runner import run_evaluation
    from tools.text_evaluation.schemas import RunInput

    source = graphs()
    class SourcePredictor(ProductionPredictor):
        def extract(self, document, options, **kwargs):
            return deepcopy(next(graph for graph in source if graph['source_doc'] == document.document_id))

    annotated = []
    documents = []
    for index, graph in enumerate(source):
        doc_id = graph['source_doc']
        filename = doc_id + '.txt'
        documents.append({'document_id': doc_id, 'filename': filename, 'language': 'zh', 'text': '评测来源文本'})
        annotated.append({
            'document_id': doc_id, 'filename': filename, 'language': 'zh',
            'entities': [{'entity_id': e['entity_id'], 'name': e['name'], 'type': e['type'], 'community': f'group-{index}'} for e in graph['entities']],
            'relations': [{**relation, 'relation_id': str(i)} for i, relation in enumerate(graph['relations'])],
        })
    request = RunInput.model_validate({'metric': 'community-discovery', 'documents': documents,
                                      'annotations': {'documents': annotated}, 'options': {'similarity_threshold': 1}})
    report = run_evaluation(request, predictor=SourcePredictor())
    assert report['metrics']['correct'] == report['metrics']['total'] == 4
    assert report['metrics']['accuracy'] == 1
    assert len(report['details']['community_alignment']) == 4
    assert len({e['entity_id'] for e in report['details']['fused_graph']['entities']}) == 4
