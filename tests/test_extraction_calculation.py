from collections import Counter
import json

import pytest

from tools import evaluation
from tools.evaluation_schemas import EntityEvaluationRequest
from tools.text_evaluation.calculation import explain_extraction
from tools.text_evaluation.runner import run_evaluation
from tests.test_text_evaluation_runner import Predictor, request


def test_calculation_deduplicates_with_scoring_keys_and_uses_micro_totals():
    facts = EntityEvaluationRequest(samples=[
        {'sample_id': 'one', 'predicted': [
            {'name': ' 张三 ', 'type': '人物'}, {'name': '张三', 'type': '人物'},
            {'name': '南京大学', 'type': '企业'},
        ], 'gold': [{'name': '张三', 'type': '人物'}, {'name': '南京大学', 'type': '组织'}]},
        {'sample_id': 'two', 'predicted': [], 'gold': [{'name': '张三', 'type': '人物'}]},
    ])
    result = explain_extraction(facts.samples, 'entity', {'one': 'one.txt', 'two': 'two.txt'})
    assert result['overall'] == evaluation.evaluate_entities(facts)
    assert result['overall']['tp'] == 1
    assert result['overall']['fp'] == 1
    assert result['overall']['fn'] == 2
    assert result['overall']['f1'] == pytest.approx(.4)
    first, second = result['documents']
    assert first['predicted_raw_count'] == 3
    assert first['predicted_count'] == 2
    assert first['predicted_duplicates_removed'] == 1
    assert Counter(row['status'] for row in first['items']) == {'tp': 1, 'fp': 1, 'fn': 1}
    assert first['items'][1]['predicted']['type'] == '企业'
    assert first['items'][2]['gold']['type'] == '组织'
    assert second['score']['fn'] == 1


def test_relation_calculation_aligns_names_and_types_and_counts_wrong_direction():
    class ReversedPredictor(Predictor):
        def extract(self, document, options, **kwargs):
            graph = super().extract(document, options, **kwargs)
            relation = graph['relations'][0]
            relation['head_entity_id'], relation['tail_entity_id'] = relation['tail_entity_id'], relation['head_entity_id']
            return graph
    result = run_evaluation(request('entity-relation-extraction'), predictor=ReversedPredictor())
    calculations = result['details']['extraction_calculation']
    assert calculations['entity-extraction']['overall'] == result['metrics']['entity-extraction']
    relations = calculations['relation-extraction']
    assert relations['overall'] == result['metrics']['relation-extraction']
    assert (relations['overall']['tp'], relations['overall']['fp'], relations['overall']['fn']) == (0, 1, 1)
    rows = relations['documents'][0]['items']
    assert [row['status'] for row in rows] == ['fp', 'fn']
    assert json.loads(rows[0]['predicted']['head_entity_id']) == ['entity', '南京大学', '组织']
    assert json.loads(rows[1]['gold']['head_entity_id']) == ['entity', '张三', '人物']


def test_empty_prediction_and_gold_explain_zero_denominators():
    facts = EntityEvaluationRequest(samples=[{'sample_id': 'empty', 'predicted': [], 'gold': []}])
    result = explain_extraction(facts.samples, 'entity', {'empty': 'empty.txt'})
    assert result['overall'] == evaluation.evaluate_entities(facts)
    assert result['overall']['f1'] == result['overall']['precision'] == result['overall']['recall'] == 0
    assert result['documents'][0]['items'] == []
