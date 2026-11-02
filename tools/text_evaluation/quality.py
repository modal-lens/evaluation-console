"""Current-result quality fields; baseline scores and improvement deltas are excluded."""


def quality_scores(metric, report):
    result = {}
    def add(prefix, score):
        for field in ('f1', 'precision', 'recall', 'accuracy', 'macro_f1', 'subset_accuracy'):
            if field in score:
                result[prefix + field] = score[field]
    scores = report['metrics']
    if metric == 'entity-relation-extraction' or metric.endswith('/fusion'):
        for name, score in scores.items():
            add(name + '/', score)
            if 'object_coverage' in score:
                add(name + '/object_coverage/', score['object_coverage'])
    elif metric == 'domain-entity-extraction':
        add('overall/', scores['overall'])
        add('domain_only/', scores['domain_only'])
    elif metric == 'multilabel-classification':
        add('', scores)
        add('micro/', scores['micro'])
        for name, score in scores['per_label'].items():
            add('label-' + name + '/', score)
    else:
        add('', scores)
        if 'object_coverage' in scores:
            add('object_coverage/', scores['object_coverage'])
        for row in scores.get('by_granularity', []):
            add(row['dimension'] + '/' + row['granularity'] + '/', row)
    return result
