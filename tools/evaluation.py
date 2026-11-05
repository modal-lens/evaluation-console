"""独立的离线评测逻辑，所有分数范围为 [0, 1]，不调用外部服务。"""

from collections import defaultdict
from itertools import combinations
import logging

logger = logging.getLogger(__name__)

from tools.evaluation_schemas import (
    ClassificationEvaluationRequest, CommunityEvaluationRequest,
    DomainEntityEvaluationRequest, EntityEvaluationRequest, FusionEvaluationRequest,
    RelationEvaluationRequest, ResolutionEvaluationRequest,
)
from utils.exceptions import ValidationAppError


def f1_counts(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    logger.debug('F1计数计算：TP=%d，FP=%d，FN=%d，Precision=%.6f，Recall=%.6f', tp, fp, fn, precision, recall)
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall,
        "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        "support": tp + fn,
    }


def set_score(predicted: set, gold: set) -> dict:
    return f1_counts(len(predicted & gold), len(predicted - gold), len(gold - predicted))


def extraction_score(samples, key) -> dict:
    predicted, gold = set(), set()
    for sample in samples:
        predicted.update((sample.sample_id, key(item)) for item in sample.predicted)
        gold.update((sample.sample_id, key(item)) for item in sample.gold)
    result = {"averaging": "micro", "matching": "exact_set", **set_score(predicted, gold)}
    logger.info('精确集合评分完成：样本数=%d，预测去重数=%d，标注去重数=%d，TP=%d，FP=%d，FN=%d，F1=%.6f', len(samples), len(predicted), len(gold), result['tp'], result['fp'], result['fn'], result['f1'])
    return result


def entity_fact_key(item):
    return (item.name, item.type)


def relation_fact_key(item):
    return (item.head_entity_id, item.relationCode, item.tail_entity_id)


def evaluate_entities(request: EntityEvaluationRequest) -> dict:
    return extraction_score(request.samples, entity_fact_key)


def evaluate_relations(request: RelationEvaluationRequest) -> dict:
    return extraction_score(request.samples, relation_fact_key)


def evaluate_domain_entities(request: DomainEntityEvaluationRequest) -> dict:
    types = set(request.domain_entity_types)

    def selected(samples):
        predicted, gold = set(), set()
        for sample in samples:
            predicted.update((sample.sample_id, x.name, x.type) for x in sample.predicted if x.type in types)
            gold.update((sample.sample_id, x.name, x.type) for x in sample.gold if x.type in types)
        return set_score(predicted, gold)

    result = {"overall": evaluate_entities(request), "domain_only": selected(request.samples), "domain_entity_types": sorted(types)}
    if request.baseline_samples is not None:
        baseline = extraction_score(request.baseline_samples, lambda item: (item.name, item.type))
        result.update(baseline=baseline, f1_delta=result["overall"]["f1"] - baseline["f1"])
    if request.relation_samples is not None:
        result['relations'] = evaluate_relations(RelationEvaluationRequest(samples=request.relation_samples))
    if request.baseline_relation_samples is not None:
        result['baseline_relations'] = evaluate_relations(RelationEvaluationRequest(samples=request.baseline_relation_samples))
        if result['relations']['support']:
            result['relation_f1_delta'] = result['relations']['f1'] - result['baseline_relations']['f1']
    if request.relation_samples is not None:
        from tools.text_evaluation.domain_comparison import comparison_acceptance
        result['acceptance'] = comparison_acceptance(result)
    return result


def evaluate_fusion(request: FusionEvaluationRequest, scope: str, object_type: str) -> dict:
    tp = fp = fn = eligible = 0
    for left, right in combinations(request.items, 2):
        # 只评价指定场景的对象对。不同簇标签无需在预测和标注间对齐。
        if scope == "cross-document" and left.document_id == right.document_id:
            continue
        if scope == "cross-language" and left.language == right.language:
            continue
        eligible += 1
        expected = left.gold_cluster == right.gold_cluster
        predicted = left.predicted_cluster is not None and left.predicted_cluster == right.predicted_cluster
        tp += int(expected and predicted)
        fp += int(not expected and predicted)
        fn += int(expected and not predicted)
    if not eligible:
        raise ValidationAppError("没有满足指定跨文档/跨语种条件的对象对")
    logger.info('对象对融合评分完成：范围=%s，对象类型=%s，可评测对象对=%d，TP=%d，FP=%d，FN=%d', scope, object_type, eligible, tp, fp, fn)
    return {"scope": scope, "object_type": object_type, "matching": "pairwise_cluster", "eligible_pairs": eligible, **f1_counts(tp, fp, fn)}


def evaluate_resolution(request: ResolutionEvaluationRequest) -> dict:
    groups = defaultdict(list)
    correct = 0
    for item in request.items:
        matched = item.predicted == item.gold
        correct += matched
        groups[(item.dimension, item.granularity)].append(matched)
    logger.info('标准值准确率计算完成：标注项数=%d，正确数=%d，分组数=%d，准确率=%.6f', len(request.items), correct, len(groups), correct / len(request.items))
    return {
        "accuracy": correct / len(request.items), "correct": correct, "total": len(request.items),
        "by_granularity": [
            {"dimension": dimension, "granularity": granularity, "accuracy": sum(values) / len(values), "correct": sum(values), "total": len(values)}
            for (dimension, granularity), values in sorted(groups.items())
        ],
    }


def evaluate_classification(request: ClassificationEvaluationRequest) -> dict:
    predicted, gold, labels = set(), set(), set()
    exact = 0
    for sample in request.samples:
        p, g = set(sample.predicted), set(sample.gold)
        predicted.update((sample.sample_id, label) for label in p)
        gold.update((sample.sample_id, label) for label in g)
        labels.update(p | g)
        exact += p == g
    per_label = {
        label: set_score({item for item in predicted if item[1] == label}, {item for item in gold if item[1] == label})
        for label in sorted(labels)
    }
    logger.info('多标签分类计数完成：样本数=%d，标签类别数=%d，预测去重数=%d，标注去重数=%d，整组匹配数=%d', len(request.samples), len(labels), len(predicted), len(gold), exact)
    return {
        "micro": set_score(predicted, gold),
        "macro_f1": sum(x["f1"] for x in per_label.values()) / len(labels) if labels else 0.0,
        "subset_accuracy": exact / len(request.samples), "per_label": per_label,
    }


def maximum_assignment(weights: list[list[int]], *, return_assignment=False):
    """匈牙利算法：对社区标签做最优一一匹配，避免标签编号影响准确率。"""
    size = len(weights)
    u, v, p, way = ([0] * (size + 1) for _ in range(4))
    for row in range(1, size + 1):
        p[0] = row
        column = 0
        minimum = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = p[column]
            delta, next_column = float("inf"), 0
            for j in range(1, size + 1):
                if not used[j]:
                    cost = -weights[current_row - 1][j - 1] - u[current_row] - v[j]
                    if cost < minimum[j]:
                        minimum[j], way[j] = cost, column
                    if minimum[j] < delta:
                        delta, next_column = minimum[j], j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            column = next_column
            if p[column] == 0:
                break
        while column:
            previous = way[column]
            p[column] = p[previous]
            column = previous
    correct = sum(weights[p[j] - 1][j - 1] for j in range(1, size + 1))
    assignment = {p[j] - 1: j - 1 for j in range(1, size + 1)}
    return (correct, assignment) if return_assignment else correct


def community_calculation(request: CommunityEvaluationRequest) -> dict:
    predicted = sorted({x.predicted_community for x in request.items if x.predicted_community is not None})
    gold = sorted({x.gold_community for x in request.items})
    predicted_index = {label: i for i, label in enumerate(predicted)}
    gold_index = {label: i for i, label in enumerate(gold)}
    size = max(len(predicted), len(gold))
    weights = [[0] * size for _ in range(size)]
    for item in request.items:
        if item.predicted_community is not None:
            weights[predicted_index[item.predicted_community]][gold_index[item.gold_community]] += 1
    correct, assignment = maximum_assignment(weights, return_assignment=True)
    logger.info('社区最优指派计算完成：节点数=%d，预测社区数=%d，标注社区数=%d，正确节点数=%d，准确率=%.6f', len(request.items), len(predicted), len(gold), correct, correct / len(request.items))
    mapping = {label: gold[assignment[index]] if assignment[index] < len(gold) else None for index, label in enumerate(predicted)}
    metrics = {"matching": "optimal_one_to_one_community_assignment", "accuracy": correct / len(request.items), "correct": correct, "total": len(request.items)}
    return {'kind': 'community', 'metrics': metrics, 'predicted_groups': predicted, 'gold_groups': gold,
            'weights': [row[:len(gold)] for row in weights[:len(predicted)]], 'mapping': mapping,
            'items': [{**item.model_dump(), 'mapped_community': mapping.get(item.predicted_community),
                       'correct': item.predicted_community is not None and mapping.get(item.predicted_community) == item.gold_community}
                      for item in request.items]}


def evaluate_communities(request: CommunityEvaluationRequest) -> dict:
    return community_calculation(request)['metrics']
