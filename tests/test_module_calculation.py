from copy import deepcopy

import pytest

from tools import evaluation
from tools.evaluation_schemas import CommunityEvaluationRequest
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.schemas import RunInput
from tools.text_evaluation.templates import template


def replay(metric, change=None):
    data = template(metric)
    gold = deepcopy(data['annotations'])
    predicted = deepcopy(data['prediction_example'])
    if change:
        change(gold, predicted)
    request = RunInput.model_validate({
        'metric': metric, 'documents': [dict(document_id=doc['document_id'], filename=doc['filename'], language=doc['language'], text='已有结果') for doc in gold['documents']],
        'annotations': gold, 'predictions': predicted, 'options': data['options'],
    })
    return run_evaluation(request)


@pytest.mark.parametrize('scope', ['cross-document', 'cross-language'])
def test_fusion_details_count_scope_pairs_and_missing_object_coverage(scope):
    def change(gold, predicted):
        doc = predicted['documents'][1]
        missing = doc['entities'].pop()['entity_id']
        doc['relations'] = [r for r in doc['relations'] if missing not in (r['head_entity_id'], r['tail_entity_id'])]
    report = replay(scope + '/fusion', change)
    calculations = report['details']['module_calculation']['groups']
    for metric, calculation in calculations.items():
        score = report['metrics'][metric]
        assert calculation['score'] == score
        counts = calculation['pair_counts']
        assert sum(counts.values()) == score['eligible_pairs']
        assert (counts['tp'], counts['fp'], counts['fn']) == (score['tp'], score['fp'], score['fn'])
        for status, pairs in calculation['pair_examples'].items():
            assert len(pairs) == min(counts[status], 50)
            for pair in pairs:
                left, right = [calculation['objects'][pair[key]] for key in ('left', 'right')]
                assert left['language' if scope == 'cross-language' else 'document_id'] != right['language' if scope == 'cross-language' else 'document_id']
                expected, actual = pair['gold_same_group'], pair['predicted_same_group']
                assert status == ('tp' if expected and actual else 'fp' if actual else 'fn' if expected else 'tn')
    entity = calculations[scope + '/entity-fusion']
    assert entity['coverage_counts'] == {'matched': 5, 'predicted': 5, 'gold': 6}
    assert entity['score']['object_coverage']['fn'] == 1


def test_fusion_pair_examples_are_bounded_but_counts_remain_complete():
    def change(gold, predicted):
        for index, doc in enumerate(gold['documents']):
            doc.update(entities=[{'entity_id': str(i), 'name': f'{index}-{i}', 'type': '组织', 'cluster': 'same'} for i in range(12)], relations=[], attributes=[])
        predicted['documents'] = deepcopy(gold['documents'])
    report = replay('cross-document/entity-fusion', change)
    group = report['details']['module_calculation']['groups']['cross-document/entity-fusion']
    assert group['pair_counts']['tp'] == group['score']['eligible_pairs'] == 144
    assert group['excluded_pairs'] == 132
    assert len(group['pair_examples']['tp']) == 50


def test_fusion_false_merges_are_fp_and_missed_merges_are_fn():
    def change(gold, predicted):
        predicted['documents'][1]['entities'][1]['cluster'] = predicted['documents'][0]['entities'][0]['cluster']
    report = replay('cross-document/entity-fusion', change)
    group = report['details']['module_calculation']['groups']['cross-document/entity-fusion']
    assert group['pair_counts'] == {'tp': 2, 'fp': 1, 'fn': 1, 'tn': 5}
    assert group['score']['f1'] == pytest.approx(2 / 3)


@pytest.mark.parametrize('with_baseline', [False, True])
def test_domain_details_use_domain_filter_and_same_baseline(with_baseline):
    def change(gold, predicted):
        if with_baseline:
            predicted['baseline_documents'] = deepcopy(gold['documents'])
            predicted['baseline_documents'][0]['entities'] = predicted['baseline_documents'][0]['entities'][:1]
            predicted['baseline_documents'][0]['relations'] = []
        predicted['documents'][0]['entities'][1]['type'] = '技术'
    report = replay('domain-entity-extraction', change)
    detail, metrics = report['details']['module_calculation'], report['metrics']
    assert detail['groups']['当前总体']['overall']['f1'] == metrics['overall']['f1'] == .5
    assert detail['groups']['领域内']['overall']['f1'] == metrics['domain_only']['f1'] == 0
    domain_entities = report['details']['domain_entities']
    typed_counts = {row['type']: row for row in domain_entities['per_type']}
    assert typed_counts['装备']['fn'] == 1
    assert typed_counts['技术']['fp'] == 1
    assert {row['status'] for row in domain_entities['entities']} == {'fp', 'fn'}
    assert all(row['predicted_types'] == ['技术'] and row['gold_types'] == ['装备'] for row in domain_entities['entities'])
    assert not any(row['new_correct_entity'] for row in domain_entities['entities'])
    if with_baseline:
        assert detail['groups']['引入前基线']['overall']['f1'] == metrics['baseline']['f1']
        assert detail['f1_delta'] == pytest.approx(.5 - 2 / 3)
    else:
        assert '引入前基线' not in detail['groups']
        assert detail['f1_delta'] is None


def test_domain_entities_mark_new_correct_domain_recognition_only_with_baseline():
    def change(gold, predicted):
        predicted['baseline_documents'] = deepcopy(gold['documents'])
        predicted['baseline_documents'][0]['entities'] = predicted['baseline_documents'][0]['entities'][:1]
        predicted['baseline_documents'][0]['relations'] = []
    report = replay('domain-entity-extraction', change)
    detail = report['details']['domain_entities']
    assert detail['has_baseline'] is True
    assert len(detail['entities']) == 1
    entity = detail['entities'][0]
    assert entity['name'] == '相控阵雷达'
    assert entity['domain_type'] == '装备'
    assert entity['status'] == 'tp'
    assert entity['baseline_recognized'] is False
    assert entity['new_correct_entity'] is True
    assert sum(row['tp'] for row in detail['per_type']) == report['metrics']['domain_only']['tp']
    no_baseline = replay('domain-entity-extraction')['details']['domain_entities']
    assert no_baseline['entities'][0]['baseline_recognized'] is None
    assert no_baseline['entities'][0]['new_correct_entity'] is False


def test_classification_explains_label_union_duplicates_micro_macro_and_subset():
    def change(gold, predicted):
        base = gold['documents'][0]
        gold['documents'] = [{**deepcopy(base), 'document_id': f'doc-{i}', 'filename': f'{i}.txt', 'tags': tags}
                             for i, tags in enumerate([['a', 'b'], ['a'], []])]
        predicted['documents'] = deepcopy(gold['documents'])
        for doc, tags in zip(predicted['documents'], [['a', 'c', 'c'], [], []]):
            doc['tags'] = tags
    report = replay('multilabel-classification', change)
    detail, metrics = report['details']['module_calculation'], report['metrics']
    assert detail['metrics'] == metrics
    assert detail['documents'][0]['tp_labels'] == ['a']
    assert detail['documents'][0]['fp_labels'] == ['c']
    assert detail['documents'][0]['fn_labels'] == ['b']
    assert detail['documents'][0]['predicted_duplicates_removed'] == 1
    assert metrics['micro']['f1'] == .4
    assert metrics['macro_f1'] == pytest.approx(detail['macro_f1_sum'] / detail['label_count'])
    assert detail['label_count'] == 3
    assert detail['exact_matches'] == 1
    assert metrics['subset_accuracy'] == pytest.approx(1 / 3)


def test_resolution_details_distinguish_wrong_missing_and_unscored_values():
    def change(gold, predicted):
        predicted['documents'][0]['resolutions'] = [
            {'mention': '2026年10月21日', 'dimension': 'time', 'granularity': 'day', 'value': '2026-10-22'},
            {'mention': '2020年', 'dimension': 'time', 'granularity': 'year', 'value': '2020'},
        ]
    report = replay('spatiotemporal-resolution', change)
    detail = report['details']['module_calculation']
    assert detail['metrics'] == report['metrics']
    assert detail['metrics']['accuracy'] == 0
    assert detail['metrics']['total'] == 2
    assert detail['unscored_predictions'] == 1
    assert sum(item['predicted'] is None for item in detail['items']) == 1
    assert all(item['correct'] is False for item in detail['items'])
    assert detail['items'][0]['mention'] == '2026年10月21日'


@pytest.mark.parametrize('missing', [False, True])
def test_community_mapping_and_node_decisions_reproduce_optimal_score(missing):
    request = CommunityEvaluationRequest(items=[
        {'node_id': 'A', 'gold_community': 'first', 'predicted_community': None if missing else 'Y'},
        {'node_id': 'B', 'gold_community': 'second', 'predicted_community': None if missing else 'X'},
        {'node_id': 'C', 'gold_community': 'first', 'predicted_community': None if missing else 'Y'},
        {'node_id': 'D', 'gold_community': 'second', 'predicted_community': None},
    ])
    detail = evaluation.community_calculation(request)
    assert detail['metrics'] == evaluation.evaluate_communities(request)
    assert sum(item['correct'] for item in detail['items']) == detail['metrics']['correct']
    assert detail['metrics']['accuracy'] == (0 if missing else .75)
    if not missing:
        assert detail['mapping'] == {'X': 'second', 'Y': 'first'}
    else:
        assert detail['mapping'] == {}


def test_community_tied_optimum_still_has_consistent_node_decisions():
    facts = CommunityEvaluationRequest(items=[{'node_id': str(i), 'predicted_community': p, 'gold_community': g}
                                             for i, (p, g) in enumerate([('a', 'x'), ('a', 'y'), ('b', 'x'), ('b', 'y')])])
    detail = evaluation.community_calculation(facts)
    assert detail['metrics']['correct'] == sum(row['correct'] for row in detail['items']) == 2
    assert len(set(detail['mapping'].values())) == 2
