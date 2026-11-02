from copy import deepcopy

import pytest

from tools.text_evaluation.precomputed import export_predictions
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.schemas import PredictionBundle
from tests.test_text_evaluation_runner import request, Predictor


def test_repeated_evidence_for_same_fact_exports_and_replays_without_score_changes():
    class Repeated(Predictor):
        def extract(self, *args, **kwargs):
            graph = super().extract(*args, **kwargs)
            graph['relations'].append({**graph['relations'][0], 'evidence': '另一处相同事实的证据'})
            return graph
    req = request('domain-entity-extraction')
    actual = run_evaluation(req, predictor=Repeated())
    assert len(actual['predictions'][0]['graph']['relations']) == 2
    assert len(actual['prediction_input']['documents'][0]['relations']) == 1
    assert actual['metrics']['relations']['tp'] == 1
    assert actual['metrics']['relations']['fp'] == actual['metrics']['relations']['fn'] == 0
    saved = req.model_copy(update={'predictions': PredictionBundle.model_validate(actual['prediction_input'])})
    assert run_evaluation(saved)['metrics'] == actual['metrics']


def test_conflicting_duplicate_prediction_clusters_are_not_silently_discarded():
    req = request()
    graph = Predictor().extract(req.documents[0], req.options)
    graph['relations'][0]['cluster'] = 'first'
    graph['relations'].append({**deepcopy(graph['relations'][0]), 'cluster':'second'})
    with pytest.raises(ValueError, match='分组冲突'):
        export_predictions([d.model_dump() for d in req.documents], {'d1':graph}, {})
