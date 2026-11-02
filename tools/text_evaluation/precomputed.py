"""Import/export prediction data without invoking extraction, fusion, or LLM services."""

import json

from .schemas import PredictionBundle, reject_configuration_file


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def graphs_from_documents(documents):
    result = {}
    for doc in documents:
        entities = [{**e.model_dump(), 'attributes': {}} for e in doc.entities]
        by_id = {e['entity_id']: e for e in entities}
        for attr in doc.attributes:
            if attr.key in by_id[attr.entity_id]['attributes']:
                raise ValueError('同一预测实体的同一属性键只能有一个值')
            by_id[attr.entity_id]['attributes'][attr.key] = attr.value
        result[doc.document_id] = {
            'source_doc': doc.document_id, 'language': doc.language, 'entities': entities,
            'relations': [r.model_dump() for r in doc.relations],
            'resolutions': [r.model_dump() for r in doc.resolutions], 'tags': doc.tags,
        }
    return result


def saved_fusion(bundle):
    trace = {'entities': {}, 'relations': {}, 'attributes': {}}
    for doc in bundle.documents:
        for entity in doc.entities:
            trace['entities'][(doc.document_id, entity.entity_id)] = entity.cluster
        for index, relation in enumerate(doc.relations):
            trace['relations'][(doc.document_id, index)] = relation.cluster
        for attr in doc.attributes:
            trace['attributes'][(doc.document_id, attr.entity_id, attr.key, attr.value)] = attr.cluster
    return {'graph': {}, 'trace': trace}


def export_predictions(documents, predictions, details, baseline=None):
    """Read predicted alignment only; no gold labels or previous metric values are used."""
    alignments = details.get('fusion_alignment', {})
    if isinstance(alignments, list):
        alignments = {'single': alignments}
    clusters = {kind: {} for kind in ('entity', 'relation', 'attribute')}
    for rows in alignments.values():
        for row in rows:
            if row.get('alignment') == 'missed':
                continue
            selector = row['selector']
            # Entity selectors are [name, type], relation/attribute selectors are nested.
            kind = 'entity' if len(selector) == 2 else 'relation' if isinstance(selector[2], (list, tuple)) else 'attribute'
            clusters[kind][(row['document_id'], encoded(selector))] = row.get('predicted_cluster')
    communities = {row['node_id']: row.get('predicted_community') for row in details.get('community_alignment', [])}

    def convert(doc, graph, *, include_details=True):
        doc_id = doc['document_id']
        identities = {str(e['entity_id']): (e['name'], e['type']) for e in graph.get('entities', [])}
        def cluster(kind, selector, fallback=None):
            return clusters[kind].get((doc_id, encoded(selector)), fallback) if include_details else None
        entities, relations, attributes = [], [], []
        for e in graph.get('entities', []):
            identity = identities[str(e['entity_id'])]
            entities.append({'entity_id': str(e['entity_id']), 'name': e['name'], 'type': e['type'],
                             'cluster': cluster('entity', identity, e.get('cluster')),
                             'community': communities.get(encoded(identity), e.get('community')) if include_details else None})
            for key, value in e.get('attributes', {}).items():
                attributes.append({'attribute_id': str(len(attributes)), 'entity_id': str(e['entity_id']),
                                   'key': str(key), 'value': str(value),
                                   'cluster': cluster('attribute', (identity, str(key), str(value).strip()))})
        relation_facts = {}
        for index, r in enumerate(graph.get('relations', [])):
            head, tail = str(r['head_entity_id']), str(r['tail_entity_id'])
            row = {'relation_id': str(r.get('relation_id', index)), 'head_entity_id': head,
                              'tail_entity_id': tail, 'relationCode': r['relationCode'],
                              'cluster': cluster('relation', (identities[head], r['relationCode'], identities[tail]), r.get('cluster'))}
            key = (head, r['relationCode'], tail)
            if key in relation_facts:
                if relation_facts[key]['cluster'] != row['cluster']:
                    raise ValueError('相同预测关系的分组冲突，无法无损导出')
                # Extraction scoring already uses exact sets. Different evidence
                # for the identical fact is one prediction, including false positives.
                continue
            relation_facts[key] = row
            relations.append(row)
        return {'document_id': doc_id, 'filename': doc['filename'], 'language': doc.get('language', graph.get('language', 'zh')),
                'entities': entities, 'relations': relations, 'attributes': attributes,
                'resolutions': details.get('normalized_predictions', {}).get(doc_id, []) if include_details else [],
                'tags': [str(tag) for tag in graph.get('tags', [])]}

    result = {'documents': [convert(d, predictions[d['document_id']]) for d in documents]}
    label_names = dict(details.get('label_names', {}))
    for graph in predictions.values():
        label_names.update(graph.get('labels', {}))
        label_names.update(graph.get('label_names', {}))
        label_names.update(graph.get('tag_names', {}))
        if isinstance(graph.get('tags'), dict):
            label_names.update(graph['tags'])
    result['label_names'] = label_names
    if baseline:
        result['baseline_documents'] = [convert(d, baseline[d['document_id']], include_details=False) for d in documents]
    return PredictionBundle.model_validate(result)


def parse_predictions(value, metric, metadata=None):
    if not isinstance(value, dict):
        raise ValueError('预测结果 JSON 顶层必须为对象')
    if value.get('success') is True and isinstance(value.get('data'), dict):
        value = value['data']
    if isinstance(value.get('job'), dict):
        value = value['job']
    if isinstance(value.get('result'), dict):
        value = value['result']
    reject_configuration_file(value)
    if value.get('metric', metric) != metric:
        raise ValueError('预测报告所属模块与当前评测模块不一致')
    if 'prediction_input' in value:
        value = value['prediction_input']
    elif 'entities' in value or 'tags' in value:
        if metadata is None or len(metadata) != 1:
            raise ValueError('单篇业务抽取 JSON 只能对应一篇标注；多篇请使用 documents 格式')
        doc = metadata[0]
        return export_predictions([doc], {doc['document_id']: value}, {
            # Business entity-disambiguation resolutions are unrelated to time/space values.
            'normalized_predictions': {doc['document_id']: value.get('resolutions', []) if metric == 'spatiotemporal-resolution' else []},
        })
    elif 'predictions' in value:
        documents = value.get('documents')
        if not isinstance(documents, list) or not documents:
            raise ValueError('完整预测报告缺少 documents 文档信息，请上传下载的抽取结果 JSON')
        predictions = {row['document_id']: row['graph'] for row in value['predictions']}
        if len(predictions) != len(value['predictions']) or set(predictions) != {d['document_id'] for d in documents}:
            raise ValueError('报告预测文档必须唯一并与 documents 一一对应')
        details = value.get('details', {})
        if 'fusion' in metric and 'fusion_alignment' not in details:
            raise ValueError('融合报告缺少预测分组，请提供含 cluster 的抽取结果 JSON')
        if metric == 'community-discovery' and 'community_alignment' not in details:
            raise ValueError('社区报告缺少预测社区分组')
        if metric == 'spatiotemporal-resolution' and 'normalized_predictions' not in details:
            raise ValueError('时空报告缺少标准化预测值')
        return export_predictions(documents, predictions, details, details.get('baseline_predictions'))
    bundle = PredictionBundle.model_validate(value)
    graphs_from_documents(bundle.documents)  # Validate attribute cardinality before queuing.
    return bundle
