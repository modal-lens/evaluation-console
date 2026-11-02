"""仅用于测试的模型边界替身；评分、来源追踪、任务和 HTTP 链路均执行真实代码。"""

from tools.text_evaluation.predictors import make_tracing_service
from tools.graph_ablation import AblationStrategy


class FixturePredictor:
    def extract(self, document, options, *, domain=False, checkpoint=lambda: None):
        checkpoint()
        if options.reference_date == '1900-01-01':
            import httpx
            from openai import APIConnectionError
            raise APIConnectionError(request=httpx.Request('POST', 'http://model.invalid/v1/chat/completions'))
        if document.text.startswith('[slow-test]'):
            import time
            for _ in range(40):
                checkpoint()
                time.sleep(.05)
        if '雷达' in document.text:
            entities = [{'entity_id': 'p-org', 'name': '南京大学', 'type': '组织', 'attributes': {}}]
            if domain:
                entities.append({'entity_id': 'p-equipment', 'name': '相控阵雷达', 'type': '装备', 'attributes': {}})
            return {'source_doc': document.document_id, 'language': document.language, 'entities': entities, 'relations': [], 'resolutions': []}
        english = document.language == 'en'
        person, org, city = ('Zhang San', 'Nanjing University', 'Nanjing') if english else ('张三', '南京大学', '南京')
        return {'source_doc': document.document_id, 'language': document.language, 'entities': [
            {'entity_id': 'p-person', 'name': person, 'type': '人物', 'attributes': {}},
            {'entity_id': 'p-org', 'name': org, 'type': '组织', 'attributes': {'所在地': city, **({'成立年份': '1902'} if '1902' in document.text else {})}},
            {'entity_id': 'p-city', 'name': city, 'type': '地理空间', 'attributes': {}},
        ], 'relations': [
            {'head_entity_id': 'p-person', 'relationCode': '任职', 'tail_entity_id': 'p-org', 'evidence': ''},
            {'head_entity_id': 'p-org', 'relationCode': '位于', 'tail_entity_id': 'p-city', 'evidence': ''},
        ], 'resolutions': []}

    def classify(self, document, options, *, checkpoint=lambda: None):
        checkpoint()
        return {'tags': ['1']}

    def resolve(self, document, graph, options, *, checkpoint=lambda: None):
        checkpoint()
        return [{'mention': '2026年10月21日', 'dimension': 'time', 'granularity': 'day', 'value': '2026-10-21'}, {'mention': '南京', 'dimension': 'space', 'granularity': 'city', 'value': '南京'}]

    def fuse(self, graphs, options, *, checkpoint=lambda: None):
        checkpoint()
        service = make_tracing_service()
        graph = service.run(graphs, AblationStrategy(translate_output_to_chinese=False))
        return {'graph': graph, 'trace': service.trace}

    def communities(self, graph, *, checkpoint=lambda: None):
        from tools.text_evaluation.predictors import ProductionPredictor
        return ProductionPredictor().communities(graph, checkpoint=checkpoint)
