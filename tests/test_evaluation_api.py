"""评测数值、校验和实际应用路由注册测试，无外部服务调用。"""

from itertools import permutations
from random import Random

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from apis.evaluation_api import router
from tools.evaluation import maximum_assignment
from utils.exceptions import AppError


app = FastAPI()
app.include_router(router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"success": False, "data": None, "error": exc.to_error()})


client = TestClient(app)
PREFIX = "/api/v1/evaluation"


def post(path, payload):
    response = client.post(PREFIX + path, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    return body["data"]


def test_entities_micro_counts_dedup_and_sample_isolation():
    a = {"name": "甲", "type": "人物"}
    b = {"name": "乙", "type": "人物"}
    result = post("/entity-extraction", {"samples": [
        {"sample_id": "1", "predicted": [a, a, b], "gold": [a]},
        {"sample_id": "2", "predicted": [], "gold": [a]},
    ]})
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["f1"] == .5


def test_relation_direction_and_type_are_significant():
    relation = {"head_entity_id": "a", "relationCode": "任职", "tail_entity_id": "b"}
    reverse = {**relation, "head_entity_id": "b", "tail_entity_id": "a"}
    result = post("/relation-extraction", {"samples": [{"sample_id": "1", "predicted": [reverse], "gold": [relation]}]})
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)


@pytest.mark.parametrize("scope", ["cross-document", "cross-language"])
@pytest.mark.parametrize("kind", ["entity", "relation", "attribute"])
def test_all_fusion_routes_score_merged_pairs(scope, kind):
    items = [
        {"item_id": "a", "document_id": "1", "language": "zh", "gold_cluster": "g", "predicted_cluster": "p"},
        {"item_id": "b", "document_id": "2", "language": "en", "gold_cluster": "g", "predicted_cluster": "p"},
        {"item_id": "c", "document_id": "3", "language": "fr", "gold_cluster": "other", "predicted_cluster": "p"},
    ]
    result = post(f"/{scope}/{kind}-fusion", {"items": items})
    assert result["object_type"] == kind
    assert (result["tp"], result["fp"], result["fn"]) == (1, 2, 0)
    assert result["f1"] == .5


def test_fusion_scope_and_missing_prediction():
    items = [
        {"item_id": "a", "document_id": "1", "language": "zh", "gold_cluster": "g"},
        {"item_id": "b", "document_id": "1", "language": "en", "gold_cluster": "g"},
    ]
    assert client.post(PREFIX + "/cross-document/entity-fusion", json={"items": items}).status_code == 400
    result = post("/cross-language/entity-fusion", {"items": items})
    assert (result["tp"], result["fn"]) == (0, 1)


def test_domain_metrics_and_baseline():
    a = {"name": "甲", "type": "装备"}
    sample = {"sample_id": "1", "predicted": [a], "gold": [a]}
    result = post("/domain-entity-extraction", {
        "samples": [sample], "domain_entity_types": ["装备"],
        "baseline_samples": [{**sample, "predicted": []}],
    })
    assert result["domain_only"]["f1"] == result["overall"]["f1"] == 1
    assert result["baseline"]["f1"] == 0
    assert result["f1_delta"] == 1


def test_resolution_groups_and_missing_prediction():
    result = post("/spatiotemporal-resolution", {"items": [
        {"sample_id": "1", "mention_id": "a", "dimension": "time", "granularity": "year", "gold": "2026", "predicted": "2026"},
        {"sample_id": "1", "mention_id": "a", "dimension": "time", "granularity": "day", "gold": "2026-10-21"},
        {"sample_id": "1", "mention_id": "b", "dimension": "space", "granularity": "city", "gold": "南京", "predicted": "南京"},
    ]})
    assert result["accuracy"] == pytest.approx(2 / 3)
    assert len(result["by_granularity"]) == 3


def test_classification_micro_macro_and_subset_accuracy():
    result = post("/multilabel-classification", {"samples": [
        {"sample_id": "1", "predicted": ["a", "a"], "gold": ["a", "b"]},
        {"sample_id": "2", "predicted": ["a"], "gold": []},
    ]})
    assert result["micro"]["f1"] == .5
    assert result["macro_f1"] == pytest.approx(1 / 3)
    assert result["subset_accuracy"] == 0


def test_community_labels_are_permutation_invariant_and_missing_is_wrong():
    items = [
        {"node_id": "a", "gold_community": "1", "predicted_community": "2"},
        {"node_id": "b", "gold_community": "2", "predicted_community": "1"},
    ]
    assert post("/community-discovery", {"items": items})["accuracy"] == 1
    items.append({"node_id": "c", "gold_community": "3"})
    assert post("/community-discovery", {"items": items})["accuracy"] == pytest.approx(2 / 3)


@pytest.mark.parametrize("predicted,gold,expected", [
    (["p", "p", "p"], ["a", "b", "c"], 1 / 3),
    (["a", "b", "c"], ["p", "p", "p"], 1 / 3),
    ([None, None], ["a", "b"], 0),
])
def test_community_split_merge_and_unassigned_nodes(predicted, gold, expected):
    items = [{"node_id": str(i), "gold_community": g, "predicted_community": p} for i, (p, g) in enumerate(zip(predicted, gold))]
    assert post("/community-discovery", {"items": items})["accuracy"] == pytest.approx(expected)


@pytest.mark.parametrize("scope", ["cross-document", "cross-language"])
def test_fusion_ignores_out_of_scope_pairs(scope):
    items = [
        {"item_id": "a", "document_id": "1", "language": "zh", "gold_cluster": "g", "predicted_cluster": "p"},
        {"item_id": "b", "document_id": "1", "language": "zh", "gold_cluster": "g", "predicted_cluster": "q"},
        {"item_id": "c", "document_id": "2", "language": "en", "gold_cluster": "g", "predicted_cluster": "p"},
    ]
    result = post(f"/{scope}/entity-fusion", {"items": items})
    assert result["eligible_pairs"] == 2
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    assert result["f1"] == pytest.approx(2 / 3)


def test_duplicate_items_rejected():
    payloads = [
        ("/community-discovery", {"node_id": "n", "gold_community": "g"}),
        ("/cross-document/entity-fusion", {"item_id": "n", "document_id": "d", "language": "zh", "gold_cluster": "g"}),
        ("/spatiotemporal-resolution", {"sample_id": "s", "mention_id": "m", "dimension": "time", "granularity": "day", "gold": "2026-01-01"}),
    ]
    for path, item in payloads:
        assert client.post(PREFIX + path, json={"items": [item, item]}).status_code == 422


def test_assignment_matches_exhaustive_optimum():
    rng = Random(17)
    for size in range(1, 6):
        for _ in range(8):
            matrix = [[rng.randrange(10) for _ in range(size)] for _ in range(size)]
            optimum = max(sum(matrix[i][order[i]] for i in range(size)) for order in permutations(range(size)))
            assert maximum_assignment(matrix) == optimum


@pytest.mark.parametrize("predicted,gold", [([], []), ([{"name": "a", "type": "x"}], []), ([], [{"name": "a", "type": "x"}])])
def test_zero_denominators(predicted, gold):
    result = post("/entity-extraction", {"samples": [{"sample_id": "1", "predicted": predicted, "gold": gold}]})
    assert result["f1"] == 0


def test_invalid_inputs_and_duplicate_samples_rejected():
    sample = {"sample_id": "1", "predicted": [], "gold": []}
    for payload in ({"samples": []}, {"samples": [sample, sample]}, {"samples": [sample], "typo": True}):
        assert client.post(PREFIX + "/entity-extraction", json=payload).status_code == 422
    assert client.post(PREFIX + "/entity-extraction", json={"samples": [{**sample, "gold": [{"name": "  ", "type": "x"}]}]}).status_code == 422


def test_baseline_with_different_gold_rejected():
    sample = {"sample_id": "1", "predicted": [], "gold": []}
    response = client.post(PREFIX + "/domain-entity-extraction", json={
        "samples": [sample], "domain_entity_types": ["人物"],
        "baseline_samples": [{**sample, "gold": [{"name": "a", "type": "人物"}]}],
    })
    assert response.status_code == 422


def test_new_routes_in_openapi():
    paths = client.get("/openapi.json").json()["paths"]
    assert len([path for path in paths if path.startswith(PREFIX + '/')]) == 12


def test_application_only_registers_metric_routes():
    from fastapi_worker import app as production_app
    production_client = TestClient(production_app)
    paths = production_client.get("/openapi.json").json()["paths"]
    assert len([path for path in paths if path.startswith(PREFIX + '/')]) == 12
    for path in ("/health", "/ready"):
        assert path in paths
    assert all(path in ("/health", "/ready") or path.startswith(
        ("/api/v1/evaluation/", "/api/v1/evaluation-runs/", "/api/v1/sql-evaluation/", "/api/v1/mcp-console/")) for path in paths)
    for path in ("/api/v1/prompt/build", "/api/v1/entity-llm/extract", "/api/v1/community/discover", "/api/v1/graph/ablation", "/api/v1/melt-tasks"):
        assert path not in paths
    assert production_client.get("/health").json() == {"status": "ok"}
