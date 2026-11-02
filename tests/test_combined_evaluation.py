import pytest

from tools.text_evaluation.schemas import COMBINED_METRICS, RunInput
from tools.text_evaluation.runner import run_evaluation
from tests.test_text_evaluation_algorithms import make_request
from frontend.tests.fixture_predictor import FixturePredictor


class CountingPredictor(FixturePredictor):
    def __init__(self):
        self.extracted = []
        self.fusions = 0

    def extract(self, document, options, **kwargs):
        self.extracted.append(document.model_dump())
        return super().extract(document, options, **kwargs)

    def fuse(self, graphs, options, **kwargs):
        self.fusions += 1
        return super().fuse(graphs, options, **kwargs)


@pytest.mark.parametrize('module,children', COMBINED_METRICS.items())
def test_combined_runs_predict_once_and_match_all_original_scores(module, children):
    request = make_request(module)
    predictor = CountingPredictor()
    result = run_evaluation(request, predictor=predictor)
    assert len(predictor.extracted) == len(request.documents)
    assert predictor.fusions == (0 if module == 'entity-relation-extraction' else 1)
    assert set(result['metrics']) == set(children)
    for child in children:
        individual = run_evaluation(request.model_copy(update={'metric': child}), predictor=FixturePredictor())
        assert result['metrics'][child] == individual['metrics']
    assert 'annotations' not in str(predictor.extracted)
    assert len(result['predictions']) == len(request.documents)


@pytest.mark.parametrize('module', ['cross-document/fusion', 'cross-language/fusion'])
@pytest.mark.parametrize('field', ['entities', 'relations', 'attributes'])
def test_combined_fusion_validates_every_target_before_prediction(module, field):
    request = make_request(module).model_dump()
    del request['annotations']['documents'][0][field][0]['cluster']
    with pytest.raises(ValueError, match='cluster'):
        RunInput.model_validate(request)


def test_combined_cancellation_checkpoint_stops_later_scoring():
    request = make_request('cross-document/fusion')
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) > 3:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        run_evaluation(request, predictor=CountingPredictor(), checkpoint=checkpoint)
