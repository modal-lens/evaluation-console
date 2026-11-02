"""Explain extraction scores with the same validated facts and keys used by scoring."""

from tools import evaluation


def explain_extraction(samples, kind, filenames):
    key = evaluation.entity_fact_key if kind == 'entity' else evaluation.relation_fact_key
    documents = []
    for sample in samples:
        predicted = {key(item): item.model_dump() for item in sample.predicted}
        gold = {key(item): item.model_dump() for item in sample.gold}
        items = []
        for status, keys in (
            ('tp', predicted.keys() & gold.keys()),
            ('fp', predicted.keys() - gold.keys()),
            ('fn', gold.keys() - predicted.keys()),
        ):
            for value in sorted(keys):
                items.append({'status': status, 'predicted': predicted.get(value), 'gold': gold.get(value)})
        documents.append({
            'document_id': sample.sample_id, 'filename': filenames[sample.sample_id],
            'predicted_raw_count': len(sample.predicted), 'gold_raw_count': len(sample.gold),
            'predicted_count': len(predicted), 'gold_count': len(gold),
            'predicted_duplicates_removed': len(sample.predicted) - len(predicted),
            'gold_duplicates_removed': len(sample.gold) - len(gold),
            'score': evaluation.set_score(set(predicted), set(gold)), 'items': items,
        })
    return {'kind': kind, 'overall': evaluation.extraction_score(samples, key), 'documents': documents}


def explain_classification(samples, metrics, filenames, names):
    documents = []
    for sample in samples:
        predicted, gold = set(sample.predicted), set(sample.gold)
        documents.append({
            'document_id': sample.sample_id, 'filename': filenames[sample.sample_id],
            'predicted': sorted(predicted), 'gold': sorted(gold),
            'tp_labels': sorted(predicted & gold), 'fp_labels': sorted(predicted - gold),
            'fn_labels': sorted(gold - predicted), 'exact_match': predicted == gold,
            'predicted_duplicates_removed': len(sample.predicted) - len(predicted),
            'gold_duplicates_removed': len(sample.gold) - len(gold),
            'score': evaluation.set_score(predicted, gold),
        })
    return {'kind': 'classification', 'metrics': metrics, 'documents': documents, 'label_names': names,
            'macro_f1_sum': sum(item['f1'] for item in metrics['per_label'].values()),
            'label_count': len(metrics['per_label']), 'exact_matches': sum(doc['exact_match'] for doc in documents),
            'total_documents': len(documents)}


def explain_domain_entities(groups, domain_types):
    """Visible domain membership based on the same per-document scoring alignment."""
    per_type = {name: {'tp': 0, 'fp': 0, 'fn': 0} for name in domain_types}
    overall = {doc['document_id']: doc for doc in groups['当前总体']['documents']}
    baseline = {doc['document_id']: doc for doc in groups.get('引入前基线', {}).get('documents', [])}
    has_baseline = '引入前基线' in groups
    entities = []
    for doc in groups['领域内']['documents']:
        current = overall[doc['document_id']]
        predicted_types, gold_types = {}, {}
        for item in current['items']:
            if item['predicted']:
                fact = item['predicted']
                predicted_types.setdefault(fact['name'], set()).add(fact['type'])
            if item['gold']:
                fact = item['gold']
                gold_types.setdefault(fact['name'], set()).add(fact['type'])
        baseline_keys = {(item['predicted']['name'], item['predicted']['type'])
                         for item in baseline.get(doc['document_id'], {}).get('items', []) if item['predicted']}
        for item in doc['items']:
            fact = item['predicted'] or item['gold']
            per_type[fact['type']][item['status']] += 1
            baseline_recognized = (fact['name'], fact['type']) in baseline_keys if has_baseline else None
            entities.append({
                'document_id': doc['document_id'], 'filename': doc['filename'],
                'name': fact['name'], 'domain_type': fact['type'], 'status': item['status'],
                'predicted_types': sorted(predicted_types.get(fact['name'], [])),
                'gold_types': sorted(gold_types.get(fact['name'], [])),
                'baseline_recognized': baseline_recognized,
                'new_correct_entity': has_baseline and not baseline_recognized and item['status'] == 'tp',
            })
    return {'domain_types': list(domain_types), 'has_baseline': has_baseline, 'entities': entities,
            'per_type': [{'type': name, **evaluation.f1_counts(**counts)} for name, counts in per_type.items()]}
