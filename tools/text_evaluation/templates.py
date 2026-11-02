"""示例原文与真实结果标注模板；不预设算法的预测或评测分数。"""

from .schemas import SUPPORTED_METRICS, COMBINED_METRICS, PredictionOptions, Annotations, PredictionBundle


def template(metric: str) -> dict:
    if metric not in SUPPORTED_METRICS:
        raise ValueError('未知评测项目')
    english = metric.startswith('cross-language/')
    count = 2 if 'fusion' in metric else 1
    documents, texts = [], []
    for index in range(count):
        en = english and index == 1
        person, organization, city = ('Zhang San', 'Nanjing University', 'Nanjing') if en else ('张三', '南京大学', '南京')
        filename = f'document-{index + 1}.txt'
        if 'fusion' in metric:
            text = 'Nanjing University is located in Nanjing and was founded in 1902. Zhang San works at Nanjing University.' if en else '南京大学位于南京，成立于1902年。张三在南京大学任职。2026年10月21日，张三在南京参加会议。'
        else:
            text = 'Nanjing University is located in Nanjing. Zhang San works at Nanjing University.' if en else '南京大学位于南京。张三在南京大学任职。2026年10月21日，张三在南京参加会议。'
        entities = [
            {'entity_id': 'person-1', 'name': person, 'type': '人物', 'cluster': 'person-group', 'community': 'community-A'},
            {'entity_id': 'org-1', 'name': organization, 'type': '组织', 'cluster': 'org-group', 'community': 'community-A'},
            {'entity_id': 'place-1', 'name': city, 'type': '地理空间', 'cluster': 'place-group', 'community': 'community-A'},
        ]
        document = {
            'document_id': f'doc-{index + 1}', 'filename': filename, 'language': 'en' if en else 'zh', 'entities': entities,
            'relations': [
                {'relation_id': 'relation-1', 'head_entity_id': 'person-1', 'relationCode': '任职', 'tail_entity_id': 'org-1', 'cluster': 'employment-group'},
                {'relation_id': 'relation-2', 'head_entity_id': 'org-1', 'relationCode': '位于', 'tail_entity_id': 'place-1', 'cluster': 'location-group'},
            ],
            'attributes': [{'attribute_id': 'attr-1', 'entity_id': 'org-1', 'key': '所在地', 'value': city, 'cluster': 'location-attribute-group'}],
            'resolutions': [
                {'mention': '2026年10月21日', 'dimension': 'time', 'granularity': 'day', 'value': '2026-10-21'},
                {'mention': '南京', 'dimension': 'space', 'granularity': 'city', 'value': '南京'},
            ] if metric == 'spatiotemporal-resolution' else [],
            'tags': ['1'] if metric == 'multilabel-classification' else [],
        }
        if 'fusion' in metric:
            document['attributes'].append({'attribute_id': 'attr-2', 'entity_id': 'org-1', 'key': '成立年份', 'value': '1902', 'cluster': 'foundation-year-group'})
        if metric == 'domain-entity-extraction':
            text = '南京大学研发了一部相控阵雷达。'
            document.update(entities=[{'entity_id': 'org-1', 'name': '南京大学', 'type': '组织'}, {'entity_id': 'equipment-1', 'name': '相控阵雷达', 'type': '装备'}], relations=[{'relation_id': 'r1', 'head_entity_id': 'org-1', 'relationCode': '研发', 'tail_entity_id': 'equipment-1'}], attributes=[])
        documents.append(document)
        texts.append({'filename': filename, 'text': text})
    options = PredictionOptions(entity_types=['人物', '组织', '地理空间'], entity_attributes={'组织': ['所在地'], '人物': ['所属组织']}, relation_types=['任职', '位于'], domain_entity_types=['装备']).model_dump()
    if 'fusion' in metric:
        # 与组合融合标注匹配：任职已由关系评测，不再把同一关系作为人物属性重复计分。
        options['entity_attributes'] = {'组织': ['所在地', '成立年份']}
    if english:
        options['use_llm_resolution'] = True
    if metric == 'domain-entity-extraction':
        # 与配套专业领域长文包一致，避免默认只允许装备而将技术错分/过滤。
        options.update(entity_types=['人物', '组织'], entity_attributes={}, relation_types=['研发'], domain_entity_types=['装备', '技术'])
    return {'metric': metric, 'included_metrics': list(COMBINED_METRICS.get(metric, (metric,))), 'annotations': {'documents': documents}, 'texts': texts, 'options': options, 'prediction_example': {'documents': documents}, 'prediction_schema': PredictionBundle.model_json_schema(), 'annotation_schema': Annotations.model_json_schema(), 'options_schema': PredictionOptions.model_json_schema(), 'notes': [
        '已有结果模式上传抽取结果 JSON 或完整报告，仅重新计算指标，不执行抽取、融合、分类、时空标准化或社区发现。',
        '预测 JSON 的 documents 与真实标注按 document_id、filename、language 对应；cluster/community 填写算法实际输出的分组，缺失预测按漏检或未分配评分。',
        '专业领域已有结果若包含 baseline_documents，则可比较基线；未提供时不运行基线算法，也不返回 F1 变化。',
        '专业领域文本实验：基线不传任何实体/属性/关系类型列表；引入后传入 entity_types、domain_entity_types、entity_attributes、relation_types。两组使用同一份完整实体和关系标注，分别计算实体及关系 F1 差值。',
        '领域指标要求：基线实体 F1 > 0.88、基线关系 F1 > 0.75，引入后总体实体 0.95 < F1 < 1；未提供基线或关系标注时不视为验收通过，真实分数不截断也不补齐。',
        '组合模块上传一份完整标注，共用一次抽取及必要的融合，各项指标分别计算；所有目标对象均需完整标注。',
        '自动评测默认 name_mapping_policy=source，不使用外部 CSV 别名表替换抽取名称；legacy 可复现原业务映射。模型根据正文识别的全称与简称仍正常处理。',
        '这是格式模板，不保证现有算法能达到满分。请根据自己的原文完整标注，不要只标注预测成功的对象。',
        'filename 必须与上传 TXT 文件名一致；document_id 唯一。预测与标注的随机实体 ID 无需相同。',
        'cluster 仅表示人工合并分组，community 仅表示人工社区分组；二者都不会传给算法。',
        '分类 tags 使用实际数据库标签 ID，示例中的 1 请替换为真实标签；预测仍使用原分类函数的数据库标签。',
        '跨语种使用统一语言代码（zh/en）；属性值为字符串，关系引用同文档标注实体 ID。',
        '跨语种融合默认启用 LLM 实体消歧；请在 entity_attributes 中列出标注要求评测的每个属性键。',
    ]}
