import pytest

from tools.text_evaluation.schemas import RunInput
from tools.text_evaluation.runner import run_evaluation


class Predictor:
    def __init__(self):
        self.inputs = []

    def extract(self, document, options, *, domain=False, checkpoint=lambda: None):
        self.inputs.append((document.model_dump(), options.model_dump(), domain))
        return {'source_doc': document.document_id, 'language': document.language, 'entities': [
            {'entity_id': 'random-1', 'name': '张三', 'type': '人物', 'attributes': {}},
            {'entity_id': 'random-2', 'name': '南京大学', 'type': '组织', 'attributes': {}},
        ], 'relations': [{'head_entity_id': 'random-1', 'relationCode': '任职', 'tail_entity_id': 'random-2'}], 'resolutions': []}


def request(metric='relation-extraction'):
    return RunInput.model_validate({'metric': metric, 'documents': [{'document_id': 'd1', 'filename': 'a.txt', 'language': 'zh', 'text': '张三在南京大学任职。'}], 'annotations': {'documents': [
        {'document_id': 'd1', 'filename': 'a.txt', 'entities': [
            {'entity_id': 'gold-10', 'name': '张三', 'type': '人物'},
            {'entity_id': 'gold-20', 'name': '南京大学', 'type': '组织'},
        ], 'relations': [{'relation_id': 'r1', 'head_entity_id': 'gold-10', 'relationCode': '任职', 'tail_entity_id': 'gold-20'}]}
    ]}})


def test_auto_relation_evaluation_aligns_random_ids_without_passing_gold_to_predictor():
    predictor = Predictor()
    result = run_evaluation(request(), predictor=predictor)
    assert result['metrics']['f1'] == 1
    assert result['predictions'][0]['graph']['entities'][0]['entity_id'] == 'random-1'
    assert 'gold-10' not in str(predictor.inputs)
    assert 'annotations' not in str(predictor.inputs)


def test_gold_changes_only_change_scoring_not_prediction_inputs():
    first = request('entity-extraction')
    second = first.model_copy(deep=True)
    second.annotations.documents[0].entities[0].name = '李四'
    a, b = Predictor(), Predictor()
    result_a = run_evaluation(first, predictor=a)
    result_b = run_evaluation(second, predictor=b)
    assert a.inputs == b.inputs
    assert result_a['metrics']['f1'] == 1
    assert result_b['metrics']['f1'] == .5
