from copy import deepcopy

import pytest
from pydantic import ValidationError

from tools.evaluation_schemas import DomainEntityEvaluationRequest
from tools.text_evaluation.domain_comparison import baseline_options, comparison_acceptance
from tools.text_evaluation.domain_comparison import normalize_open_relations
from tools.text_evaluation.predictors import ProductionPredictor
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.schemas import PredictionOptions, PredictionBundle
from tests.test_text_evaluation_runner import request, Predictor


def test_baseline_removes_every_schema_without_changing_original_or_execution_options():
    options = PredictionOptions(entity_attributes={'人物': ['职位']}, relation_types=['任职'])
    original = options.model_dump()
    baseline = baseline_options(options)
    assert baseline.entity_types == baseline.domain_entity_types == baseline.relation_types == []
    assert baseline.entity_attributes == {}
    assert baseline.block_size == options.block_size
    assert baseline.name_mapping_policy == options.name_mapping_policy
    assert options.model_dump() == original


def test_production_open_prompt_does_not_leak_configured_lists(monkeypatch):
    import tools.text_evaluation.source_extraction as source
    seen = []
    def extract(**kwargs):
        seen.append(kwargs['model_params'])
        return {'entities': [], 'relations': [], 'resolutions': []}
    monkeypatch.setattr(source, 'extract_entities_for_evaluation', extract)
    options = PredictionOptions(entity_types=['秘密实体类别'], domain_entity_types=['秘密领域类别'],
                                entity_attributes={'秘密实体类别': ['秘密属性']}, relation_types=['秘密关系'])
    graph = ProductionPredictor().extract(request().documents[0], baseline_options(options))
    assert seen[0]['entityTypes'] == seen[0]['relationTypes'] == []
    assert seen[0]['entityAttributeSchema'] == {}
    assert '秘密' not in seen[0]['generatedPrompt']
    assert '只能来自' not in seen[0]['generatedPrompt']
    assert graph['processing']['extraction_mode'] == 'open'
    assert graph['processing']['schema_enforced'] is False


def test_runner_compares_same_gold_for_entities_and_relations_and_replays_without_algorithms():
    class Different(Predictor):
        def extract(self, document, options, *, domain=False, checkpoint=lambda: None):
            graph = super().extract(document, options, domain=domain, checkpoint=checkpoint)
            if not domain:
                assert not options.entity_types and not options.relation_types and not options.entity_attributes and not options.domain_entity_types
                graph['entities'].pop()
                graph['relations'] = []
            return graph
    req = request('domain-entity-extraction')
    result = run_evaluation(req, predictor=Different())
    metrics = result['metrics']
    assert metrics['baseline']['f1'] == pytest.approx(2/3)
    assert metrics['relations']['f1'] == 1
    assert metrics['baseline_relations']['f1'] == 0
    assert metrics['f1_delta'] == pytest.approx(1/3)
    assert metrics['relation_f1_delta'] == 1
    assert result['details']['module_calculation']['relation_groups']['引入前关系']['overall']['fn'] == 1
    saved = req.model_copy(update={'predictions': PredictionBundle.model_validate(result['prediction_input'])})
    class NoAlgorithms:
        def __getattr__(self, name):
            raise AssertionError('Unexpected algorithm call: ' + name)
    assert run_evaluation(saved, predictor=NoAlgorithms())['metrics'] == metrics


@pytest.mark.parametrize('second_name', ['任职', '仍...错误'])
def test_open_relation_retry_uses_same_text_without_list_and_retains_real_second_output(monkeypatch, second_name):
    import tools.text_evaluation.source_extraction as source
    seen = []
    def extract(**kwargs):
        seen.append(kwargs)
        code = '在...任职' if len(seen) == 1 else second_name
        return {'entities': [{'entity_id':'a','name':'张三','type':'人物','attributes':{}},
                             {'entity_id':'b','name':'南京大学','type':'组织','attributes':{}}],
                'relations':[{'head_entity_id':'a','relationCode':code,'tail_entity_id':'b'}], 'resolutions':[]}
    monkeypatch.setattr(source, 'extract_entities_for_evaluation', extract)
    graph = ProductionPredictor().extract(request().documents[0], baseline_options(PredictionOptions()))
    assert len(seen) == 2
    assert seen[0]['input_text'] == seen[1]['input_text']
    assert all(row['model_params']['entityTypes'] == row['model_params']['relationTypes'] == [] for row in seen)
    assert graph['relations'][0]['relationCode'] == second_name
    assert graph['processing']['open_relation_retries'][0]['invalid_relation_names'] == ['在...任职']


@pytest.mark.parametrize('baseline_entity,baseline_relation,configured,passed', [
    (.89,.76,.96,True), (.88,.76,.96,False), (.89,.75,.96,False),
    (.89,.76,.95,False), (.89,.76,1,False),
])
def test_user_thresholds_are_strict_without_clipping(baseline_entity, baseline_relation, configured, passed):
    scores = {key: {'f1': value, 'support': 10} for key, value in
              [('baseline',baseline_entity), ('baseline_relations',baseline_relation), ('overall',configured)]}
    assert comparison_acceptance(scores)['passed'] is passed
    assert scores['overall']['f1'] == configured


def test_missing_gold_relations_cannot_satisfy_baseline_relation_requirement():
    result = comparison_acceptance({'baseline': {'f1': .99,'support':100},
        'overall': {'f1': .99,'support':100}, 'baseline_relations': {'f1':1,'support':0}})
    assert not result['passed']
    assert result['checks']['baseline_relation']['status'] == 'not_evaluable'


def test_empty_relation_gold_does_not_claim_a_relation_improvement():
    from tools.evaluation import evaluate_domain_entities
    entity = {'sample_id': 'd', 'predicted': [], 'gold': []}
    relation = {'sample_id': 'd', 'predicted': [], 'gold': []}
    request = DomainEntityEvaluationRequest(samples=[entity], baseline_samples=[entity], domain_entity_types=['装备'],
        relation_samples=[relation], baseline_relation_samples=[relation])
    result = evaluate_domain_entities(request)
    assert 'relation_f1_delta' not in result
    assert result['acceptance']['checks']['baseline_relation']['status'] == 'not_evaluable'


def test_mismatched_relation_gold_or_documents_rejected():
    entity = {'sample_id': 'd', 'predicted': [], 'gold': []}
    relation = {'sample_id':'d', 'predicted':[], 'gold':[{'head_entity_id':'a','relationCode':'任职','tail_entity_id':'b'}]}
    data = {'samples':[entity], 'baseline_samples':[entity], 'domain_entity_types':['装备'],
            'relation_samples':[relation], 'baseline_relation_samples':[deepcopy(relation)]}
    data['baseline_relation_samples'][0]['gold'] = []
    with pytest.raises(ValidationError, match='真实标注必须相同'):
        DomainEntityEvaluationRequest.model_validate(data)
    data['baseline_relation_samples'][0]['sample_id'] = 'other'
    with pytest.raises(ValidationError, match='一一对应'):
        DomainEntityEvaluationRequest.model_validate(data)


def test_open_normalization_uses_inferred_type_and_grammar_without_retyping_entities():
    graph = {'entities': [{'entity_id':'a','name':'甲','type':'错分类型'},
                          {'entity_id':'b','name':'乙','type':'方法'}],
             'relations':[{'head_entity_id':'a','tail_entity_id':'b','relationCode':'使用方法'},
                          {'head_entity_id':'a','tail_entity_id':'b','relationCode':'在...负责'}]}
    before = deepcopy(graph['entities'])
    normalize_open_relations(graph, '甲使用方法乙，甲在乙处负责。')
    assert [r['relationCode'] for r in graph['relations']] == ['使用','负责']
    assert graph['entities'] == before
    changes = deepcopy(graph['processing']['open_relation_normalization'])
    normalize_open_relations(graph, '甲使用方法乙，甲在乙处负责。')
    assert graph['processing']['open_relation_normalization'] == changes


def test_open_normalization_does_not_invent_an_absent_predicate_or_ignore_evidence():
    graph = {'entities': [{'entity_id':'a','name':'甲','type':'人物'}, {'entity_id':'b','name':'乙','type':'方法'}],
             'relations':[{'head_entity_id':'a','tail_entity_id':'b','relationCode':'使用方法','evidence':'仅仅提及甲和乙'}]}
    normalize_open_relations(graph, '甲使用方法乙。')
    assert graph['relations'][0]['relationCode'] == '使用方法'
