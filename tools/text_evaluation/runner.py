"""原文预测完成后才读取标注评分，保留真实预测及对齐记录。"""

from itertools import combinations
import json

from tools import evaluation
from tools.evaluation_schemas import (
    EntityEvaluationRequest, RelationEvaluationRequest, DomainEntityEvaluationRequest,
    ClassificationEvaluationRequest, ResolutionEvaluationRequest, CommunityEvaluationRequest,
)
from utils.exceptions import ValidationAppError
from .schemas import RunInput, COMBINED_METRICS
from .predictors import ProductionPredictor
from .precomputed import graphs_from_documents, saved_fusion, export_predictions
from .calculation import explain_extraction, explain_classification, explain_domain_entities
from .domain_comparison import baseline_options


def _identity(entity):
    return (str(entity.get('name', '')).strip(), str(entity.get('type', '')).strip())


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _entity_samples(request, predictions):
    return [{'sample_id': doc.document_id, 'predicted': [{'name': e['name'], 'type': e['type']} for e in predictions[doc.document_id]['entities']], 'gold': [{'name': e.name, 'type': e.type} for e in doc.entities]} for doc in request.annotations.documents]


def _relations(graph):
    by_id = {str(e['entity_id']): _identity(e) for e in graph.get('entities', [])}
    return [(_encoded(('entity', *by_id.get(str(r['head_entity_id']), ('missing', str(r['head_entity_id']))))), str(r['relationCode']), _encoded(('entity', *by_id.get(str(r['tail_entity_id']), ('missing', str(r['tail_entity_id'])))))) for r in graph.get('relations', [])]


def _relation_samples(request, predictions):
    samples = []
    for doc in request.annotations.documents:
        gold_graph = {'entities': [e.model_dump() for e in doc.entities], 'relations': [r.model_dump() for r in doc.relations]}
        convert = lambda values: [{'head_entity_id': h, 'relationCode': r, 'tail_entity_id': t} for h, r, t in values]
        samples.append({'sample_id': doc.document_id, 'predicted': convert(_relations(predictions[doc.document_id])), 'gold': convert(_relations(gold_graph))})
    return samples


def _occurrences(graph, kind, trace=None):
    """返回按原文档内语义精确对齐的对象，合并分组来自原算法追踪。"""
    doc = graph['source_doc']
    entities = {str(e['entity_id']): e for e in graph['entities']}
    rows = []
    if kind == 'entity':
        for index, item in enumerate(graph['entities']):
            key = _identity(item)
            cluster = trace['entities'].get((doc, str(item['entity_id']))) if trace else item.get('cluster')
            rows.append((key, cluster, str(item['entity_id'])))
    elif kind == 'relation':
        for index, item in enumerate(graph.get('relations', [])):
            key = (_identity(entities.get(str(item['head_entity_id']), {})), str(item['relationCode']), _identity(entities.get(str(item['tail_entity_id']), {})))
            cluster = trace['relations'].get((doc, index)) if trace else item.get('cluster')
            rows.append((key, cluster, str(item.get('relation_id', index))))
    else:
        if trace is None:
            for item in graph.get('attributes', []):
                key = (_identity(entities[str(item['entity_id'])]), item['key'], item['value'])
                rows.append((key, item.get('cluster'), item['attribute_id']))
        else:
            for entity in graph['entities']:
                for key, value in entity.get('attributes', {}).items():
                    selector = (_identity(entity), str(key), str(value).strip())
                    cluster = trace['attributes'].get((doc, str(entity['entity_id']), str(key), str(value)))
                    rows.append((selector, cluster, f"{entity['entity_id']}:{key}"))
    # 与旧名称级抽取口径一致，同文档相同对象只计一次。
    return {key: (cluster, item_id) for key, cluster, item_id in rows}


def _fusion_score(request, predictions, fusion):
    kind = request.metric.split('/')[1].removesuffix('-fusion')
    rows, matched, predicted_count, gold_count = [], 0, 0, 0
    for doc in request.annotations.documents:
        gold_graph = {'source_doc': doc.document_id, 'entities': [e.model_dump() for e in doc.entities], 'relations': [r.model_dump() for r in doc.relations], 'attributes': [a.model_dump() for a in doc.attributes]}
        expected = _occurrences(gold_graph, kind)
        predicted = _occurrences(predictions[doc.document_id], kind, fusion['trace'])
        gold_count += len(expected)
        predicted_count += len(predicted)
        for key in expected.keys() | predicted.keys():
            gold_item = expected.get(key)
            predicted_item = predicted.get(key)
            matched += bool(gold_item and predicted_item)
            rows.append({
                'document_id': doc.document_id, 'language': doc.language, 'selector': key,
                'gold_cluster': _encoded(('gold', gold_item[0])) if gold_item else _encoded(('unmatched_prediction', doc.document_id, key)),
                'predicted_cluster': _encoded(predicted_item[0]) if predicted_item and predicted_item[0] is not None else None,
                'alignment': 'matched' if gold_item and predicted_item else 'missed' if gold_item else 'extra',
            })
    if predicted_count > 1000 or len(rows) > 2000:
        raise ValidationAppError('本次抽取产生过多待消融对象，请缩小评测文本（预测对象最多 1000）')
    tp = fp = fn = eligible = 0
    pair_counts = {kind: 0 for kind in ('tp', 'fp', 'fn', 'tn')}
    examples = {kind: [] for kind in pair_counts}
    for (left_index, left), (right_index, right) in combinations(enumerate(rows), 2):
        if request.metric.startswith('cross-document/') and left['document_id'] == right['document_id']:
            continue
        if request.metric.startswith('cross-language/') and left['language'] == right['language']:
            continue
        eligible += 1
        expected = left['gold_cluster'] == right['gold_cluster']
        predicted = left['predicted_cluster'] is not None and left['predicted_cluster'] == right['predicted_cluster']
        tp += expected and predicted
        fp += not expected and predicted
        fn += expected and not predicted
        status = 'tp' if expected and predicted else 'fp' if predicted else 'fn' if expected else 'tn'
        pair_counts[status] += 1
        if len(examples[status]) < 50:
            examples[status].append({'left': left_index, 'right': right_index,
                                     'gold_same_group': expected, 'predicted_same_group': predicted})
    if not eligible:
        raise ValidationAppError('没有满足跨文档/跨语种条件的可评测对象对')
    score = {'matching': 'pairwise_cluster', 'scope': request.metric.split('/')[0], 'object_type': kind, 'eligible_pairs': eligible, **evaluation.f1_counts(tp, fp, fn)}
    # pairwise F1 本身不能惩罚所有漏抽单例，另提供对象覆盖指标，避免高分掩盖漏抽。
    score['object_coverage'] = evaluation.f1_counts(matched, predicted_count - matched, gold_count - matched)
    calculation = {'score': score, 'objects': rows, 'pair_counts': pair_counts, 'pair_examples': examples,
                   'examples_limit_per_status': 50,
                   'excluded_pairs': len(rows) * (len(rows) - 1) // 2 - eligible,
                   'coverage_counts': {'matched': matched, 'predicted': predicted_count, 'gold': gold_count}}
    return score, rows, calculation


def run_evaluation(request: RunInput, *, predictor=None, progress=None, checkpoint=None) -> dict:
    saved = request.predictions
    if saved is None:
        predictor = predictor or ProductionPredictor()
    checkpoint = checkpoint or (lambda: None)
    progress = progress or (lambda stage, done, total: None)
    metric = request.metric
    total = len(request.documents) + 1
    if saved is None and metric == 'domain-entity-extraction' and request.options.compare_baseline:
        total += len(request.documents)
    if saved is None and metric == 'spatiotemporal-resolution':
        total += len(request.documents)
    if 'fusion' in metric or metric == 'community-discovery':
        total += 1
    done, predictions, baseline = 0, {}, {}
    progress('读取已有预测结果' if saved is not None else '准备预测', done, total)
    uploaded = graphs_from_documents(saved.documents) if saved is not None else {}
    if saved is not None and saved.baseline_documents and request.options.compare_baseline:
        baseline = graphs_from_documents(saved.baseline_documents)
    for document in request.documents:
        checkpoint()
        if saved is not None:
            graph = uploaded[document.document_id]
        elif metric == 'multilabel-classification':
            graph = predictor.classify(document, request.options, checkpoint=checkpoint)
        else:
            graph = predictor.extract(document, request.options, domain=metric == 'domain-entity-extraction', checkpoint=checkpoint)
        predictions[document.document_id] = graph
        done += 1
        progress(f'已读取 {document.filename} 的预测结果' if saved is not None else f'已完成 {document.filename} 的预测', done, total)
        if saved is None and metric == 'domain-entity-extraction' and request.options.compare_baseline:
            baseline[document.document_id] = predictor.extract(document, baseline_options(request.options), domain=False, checkpoint=checkpoint)
            done += 1
            progress(f'已完成 {document.filename} 的基线预测', done, total)

    details = {}
    # 以上预测调用的输入仅包含 document 与 options；从此处才读取人工标注。
    checkpoint()
    if metric in ('entity-relation-extraction', 'entity-extraction', 'relation-extraction'):
        scores, calculations = {}, {}
        filenames = {doc.document_id: doc.filename for doc in request.documents}
        if metric != 'relation-extraction':
            facts = EntityEvaluationRequest(samples=_entity_samples(request, predictions))
            scores['entity-extraction'] = evaluation.evaluate_entities(facts)
            calculations['entity-extraction'] = explain_extraction(facts.samples, 'entity', filenames)
        if metric != 'entity-extraction':
            facts = RelationEvaluationRequest(samples=_relation_samples(request, predictions))
            scores['relation-extraction'] = evaluation.evaluate_relations(facts)
            calculations['relation-extraction'] = explain_extraction(facts.samples, 'relation', filenames)
        metrics = scores if metric == 'entity-relation-extraction' else scores[metric]
        details['extraction_calculation'] = calculations
    elif metric == 'domain-entity-extraction':
        facts = DomainEntityEvaluationRequest(samples=_entity_samples(request, predictions), domain_entity_types=request.options.domain_entity_types,
            baseline_samples=_entity_samples(request, baseline) if baseline else None,
            relation_samples=_relation_samples(request, predictions),
            baseline_relation_samples=_relation_samples(request, baseline) if baseline else None)
        metrics = evaluation.evaluate_domain_entities(facts)
        filenames = {doc.document_id: doc.filename for doc in request.documents}
        domain_types = set(facts.domain_entity_types)
        selected = [sample.model_copy(update={'predicted': [item for item in sample.predicted if item.type in domain_types],
                                              'gold': [item for item in sample.gold if item.type in domain_types]}) for sample in facts.samples]
        groups = {'当前总体': explain_extraction(facts.samples, 'entity', filenames),
                  '领域内': explain_extraction(selected, 'entity', filenames)}
        if facts.baseline_samples is not None:
            groups['引入前基线'] = explain_extraction(facts.baseline_samples, 'entity', filenames)
        details['module_calculation'] = {'kind': 'domain', 'groups': groups,
                                         'domain_entity_types': facts.domain_entity_types, 'f1_delta': metrics.get('f1_delta')}
        relation_groups = {'引入后关系': explain_extraction(facts.relation_samples, 'relation', filenames)}
        if facts.baseline_relation_samples is not None:
            relation_groups['引入前关系'] = explain_extraction(facts.baseline_relation_samples, 'relation', filenames)
        details['module_calculation'].update(relation_groups=relation_groups, relation_f1_delta=metrics.get('relation_f1_delta'), acceptance=metrics['acceptance'])
        details['comparison_configuration'] = {
            'source': 'uploaded_predictions' if saved is not None else 'live_open_vs_configured',
            'baseline': {'entity_types': [], 'entity_attributes': {}, 'relation_types': []} if saved is None and baseline else None,
            'configured': {'entity_types': list(dict.fromkeys(request.options.entity_types + request.options.domain_entity_types)),
                           'entity_attributes': request.options.entity_attributes, 'relation_types': request.options.relation_types},
        }
        details['domain_entities'] = explain_domain_entities(groups, facts.domain_entity_types)
        details['baseline_predictions'] = baseline
    elif metric == 'multilabel-classification':
        samples = [{'sample_id': doc.document_id, 'predicted': predictions[doc.document_id]['tags'], 'gold': doc.tags} for doc in request.annotations.documents]
        facts = ClassificationEvaluationRequest(samples=samples)
        metrics = evaluation.evaluate_classification(facts)
        label_names = dict(saved.label_names) if saved is not None else {}
        for graph in predictions.values():
            label_names.update(graph.get('label_names', {}))
            label_names.update(graph.get('tag_names', {}))
        details['label_names'] = label_names
        details['module_calculation'] = explain_classification(facts.samples, metrics, {doc.document_id: doc.filename for doc in request.documents}, label_names)
        details['classification_samples'] = [
            {'document_id': doc.document_id, 'filename': doc.filename,
             'predicted': [{'id': str(tag), 'name': label_names.get(str(tag))} for tag in predictions[doc.document_id]['tags']],
             'gold': [{'id': str(tag), 'name': label_names.get(str(tag))} for tag in doc.tags]}
            for doc in request.annotations.documents
        ]
    elif metric == 'spatiotemporal-resolution':
        normalized = {}
        for doc in request.documents:
            checkpoint()
            normalized[doc.document_id] = predictions[doc.document_id]['resolutions'] if saved is not None else predictor.resolve(doc, predictions[doc.document_id], request.options, checkpoint=checkpoint)
            if saved is None:
                done += 1
                progress(f'已标准化 {doc.filename} 的时空信息', done, total)
        items = []
        for doc in request.annotations.documents:
            by_key = {(x['mention'], x['dimension'], x['granularity']): x['value'] for x in normalized[doc.document_id]}
            for index, gold in enumerate(doc.resolutions):
                items.append({'sample_id': doc.document_id, 'mention_id': str(index), 'dimension': gold.dimension, 'granularity': gold.granularity, 'gold': gold.value, 'predicted': by_key.get((gold.mention, gold.dimension, gold.granularity))})
        metrics = evaluation.evaluate_resolution(ResolutionEvaluationRequest(items=items))
        details['normalized_predictions'] = normalized
        details['resolution_alignment'] = items
        gold_by_doc = {doc.document_id: doc for doc in request.annotations.documents}
        details['module_calculation'] = {
            'kind': 'resolution', 'metrics': metrics,
            'items': [{**item, 'filename': gold_by_doc[item['sample_id']].filename,
                       'mention': gold_by_doc[item['sample_id']].resolutions[int(item['mention_id'])].mention,
                       'correct': item['predicted'] == item['gold']} for item in items],
            'unscored_predictions': sum(
                (row['mention'], row['dimension'], row['granularity']) not in
                {(x.mention, x.dimension, x.granularity) for x in gold_by_doc[doc_id].resolutions}
                for doc_id, values in normalized.items() for row in values),
        }
    else:
        checkpoint()
        fusion = saved_fusion(saved) if saved is not None else predictor.fuse(list(predictions.values()), request.options, checkpoint=checkpoint)
        done += 1
        progress('已读取预测分组' if saved is not None else '已完成图谱消融', done, total)
        details['fused_graph'] = fusion['graph']
        if metric == 'community-discovery':
            network = {} if saved is not None else predictor.communities(fusion['graph'], checkpoint=checkpoint)
            node_assignments = {str(node['id']): str(node['community_id']) for node in network.get('nodes', [])}
            candidates = {}
            # 用真实来源链对齐，而不是比较消融后可能被映射或翻译过的展示名称。
            for doc_id, graph in predictions.items():
                for entity in graph['entities']:
                    global_id = fusion['trace']['entities'].get((doc_id, str(entity['entity_id'])))
                    output_id = fusion['trace'].get('output_ids', {}).get(global_id)
                    community = entity.get('community') if saved is not None else node_assignments.get(str(output_id)) if output_id is not None else None
                    candidates.setdefault(_identity(entity), set()).add(community)
            assigned = {key: next(iter(values)) if len(values) == 1 else None for key, values in candidates.items()}
            gold_by_key = {}
            for doc in request.annotations.documents:
                for item in doc.entities:
                    key = (item.name, item.type)
                    if key in gold_by_key and gold_by_key[key] != item.community:
                        raise ValidationAppError('同名同类型的社区标注冲突；跨文档统一对象应属于同一社区')
                    gold_by_key[key] = item.community
            if not gold_by_key:
                raise ValidationAppError('社区评测至少需要一个人工标注节点')
            items = [{'node_id': _encoded(key), 'gold_community': value, 'predicted_community': assigned.get(key)} for key, value in gold_by_key.items()]
            calculation = evaluation.community_calculation(CommunityEvaluationRequest(items=items))
            metrics = calculation['metrics']
            details['module_calculation'] = calculation
            details['community_network'] = network
            details['community_alignment'] = items
        elif metric in COMBINED_METRICS:
            metrics, alignments, calculations = {}, {}, {}
            for child in COMBINED_METRICS[metric]:
                checkpoint()
                metrics[child], alignments[child], calculations[child] = _fusion_score(request.model_copy(update={'metric': child}), predictions, fusion)
            details['fusion_alignment'] = alignments
            details['module_calculation'] = {'kind': 'fusion', 'groups': calculations}
        else:
            metrics, details['fusion_alignment'], calculation = _fusion_score(request, predictions, fusion)
            details['module_calculation'] = {'kind': 'fusion', 'groups': {metric: calculation}}
    checkpoint()
    progress('评测完成', total, total)
    prediction_input = export_predictions([doc.model_dump() for doc in request.documents], predictions, details, baseline)
    return {
        'metric': metric, 'metrics': metrics,
        'input_mode': 'precomputed' if saved is not None else 'text',
        'prediction_input': prediction_input.to_upload(metric),
        'predictions': [{'document_id': doc.document_id, 'filename': doc.filename, 'graph': predictions[doc.document_id]} for doc in request.documents],
        'details': details, 'options': request.options.model_dump(),
        'annotations': request.annotations.model_dump(),
        'documents': [doc.model_dump() for doc in request.documents],
        'matching_policy': '同文档对象按名称、类型及关系/属性语义精确对齐；不使用随机预测 ID 与标注 ID 直接比较。',
    }
