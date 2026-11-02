"""上传标注与公开算法配置；标注数据不进入预测器。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

METRICS = (
    'entity-extraction', 'relation-extraction', 'domain-entity-extraction',
    'cross-document/entity-fusion', 'cross-document/relation-fusion', 'cross-document/attribute-fusion',
    'cross-language/entity-fusion', 'cross-language/relation-fusion', 'cross-language/attribute-fusion',
    'spatiotemporal-resolution', 'multilabel-classification', 'community-discovery',
)
COMBINED_METRICS = {
    'entity-relation-extraction': ('entity-extraction', 'relation-extraction'),
    'cross-document/fusion': tuple('cross-document/' + kind + '-fusion' for kind in ('entity', 'relation', 'attribute')),
    'cross-language/fusion': tuple('cross-language/' + kind + '-fusion' for kind in ('entity', 'relation', 'attribute')),
}
SUPPORTED_METRICS = (*METRICS, *COMBINED_METRICS)
DEFAULT_TYPES = ['人物', '组织', '企业', '地理空间', '事件', '产品', '项目', '平台', '会议', '协议', '文件政策']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class GoldEntity(StrictModel):
    entity_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    cluster: str | None = Field(default=None, min_length=1)
    community: str | None = Field(default=None, min_length=1)


class GoldRelation(StrictModel):
    relation_id: str = Field(min_length=1)
    head_entity_id: str = Field(min_length=1)
    relationCode: str = Field(min_length=1)
    tail_entity_id: str = Field(min_length=1)
    cluster: str | None = Field(default=None, min_length=1)


class GoldAttribute(StrictModel):
    attribute_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    cluster: str | None = Field(default=None, min_length=1)


class GoldResolution(StrictModel):
    mention: str = Field(min_length=1)
    dimension: Literal['time', 'space']
    granularity: str = Field(min_length=1)
    value: str = Field(min_length=1)

    @model_validator(mode='after')
    def normalized_time(self):
        if self.dimension == 'time':
            import re
            from datetime import date
            patterns = {'year': r'\d{4}', 'month': r'\d{4}-\d{2}', 'day': r'\d{4}-\d{2}-\d{2}'}
            if self.granularity not in patterns or not re.fullmatch(patterns[self.granularity], self.value):
                raise ValueError('时间标准值需按 year/month/day 使用 YYYY、YYYY-MM、YYYY-MM-DD')
            date.fromisoformat(self.value + ('-01-01' if self.granularity == 'year' else '-01' if self.granularity == 'month' else ''))
        return self


class GoldDocument(StrictModel):
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1, description='与上传 TXT 文件名精确对应')
    language: str = Field(default='zh', min_length=1)
    entities: list[GoldEntity] = Field(default_factory=list)
    relations: list[GoldRelation] = Field(default_factory=list)
    attributes: list[GoldAttribute] = Field(default_factory=list)
    resolutions: list[GoldResolution] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator('language')
    @classmethod
    def language_code(cls, value):
        import re
        if not re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*', value):
            raise ValueError('language 请使用语言代码，例如 zh、en、ja，而非中文语言名称')
        # 区域/文字变体不是不同语言；EN/en、zh-CN/zh-TW 统一后判定跨语种条件。
        return value.lower().split('-')[0]

    @model_validator(mode='after')
    def validate_references(self):
        def unique(items, key):
            values = [key(item) for item in items]
            if len(values) != len(set(values)):
                raise ValueError('同一文档中的 ID 或对象匹配键不允许重复')
        unique(self.entities, lambda x: x.entity_id)
        unique(self.entities, lambda x: (x.name, x.type))
        unique(self.relations, lambda x: x.relation_id)
        unique(self.relations, lambda x: (x.head_entity_id, x.relationCode, x.tail_entity_id))
        unique(self.attributes, lambda x: x.attribute_id)
        unique(self.attributes, lambda x: (x.entity_id, x.key, x.value))
        unique(self.resolutions, lambda x: (x.mention, x.dimension, x.granularity))
        ids = {x.entity_id for x in self.entities}
        if any(x.head_entity_id not in ids or x.tail_entity_id not in ids for x in self.relations):
            raise ValueError('关系端点必须引用本文档中的标注实体 ID')
        if any(x.entity_id not in ids for x in self.attributes):
            raise ValueError('属性必须引用本文档中的标注实体 ID')
        if any(not x for x in self.tags):
            raise ValueError('分类标签 ID 不能为空')
        return self


class Annotations(StrictModel):
    documents: list[GoldDocument] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def validate_documents(self):
        for field in ('document_id', 'filename'):
            values = [getattr(doc, field) for doc in self.documents]
            if len(values) != len(set(values)):
                raise ValueError(f'{field} 不允许重复')
        return self


class PredictionOptions(StrictModel):
    name_mapping_policy: Literal['source', 'legacy'] = Field(default='source', description='source 保留模型基于正文抽取的名称，不应用外部 CSV 别名表；legacy 复现原业务名称映射')
    entity_types: list[str] = Field(default_factory=lambda: list(DEFAULT_TYPES), min_length=1, max_length=100)
    entity_attributes: dict[str, list[str]] = Field(default_factory=dict)
    relation_types: list[str] = Field(default_factory=list, max_length=100)
    domain_entity_types: list[str] = Field(default_factory=lambda: ['装备'], min_length=1, max_length=100)
    compare_baseline: bool = True
    block_size: int = Field(default=1000, ge=100, le=4000)
    similarity_threshold: float = Field(default=.88, ge=0, le=1)
    attribute_similarity_threshold: float = Field(default=.92, ge=0, le=1)
    relation_similarity_threshold: float = Field(default=.90, ge=0, le=1)
    use_llm_resolution: bool = False
    time_granularities: list[Literal['year', 'month', 'day']] = Field(default_factory=lambda: ['year', 'month', 'day'], max_length=3)
    space_granularities: list[Literal['country', 'province', 'city', 'district']] = Field(default_factory=lambda: ['country', 'province', 'city', 'district'], max_length=4)
    reference_date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')

    @model_validator(mode='after')
    def nonempty_config(self):
        values = self.entity_types + self.domain_entity_types + self.relation_types
        if any(not item.strip() for item in values):
            raise ValueError('类型与关系名称不能为空')
        if any(not key.strip() or any(not value.strip() for value in names) for key, names in self.entity_attributes.items()):
            raise ValueError('实体属性配置不能为空字符串')
        if self.reference_date:
            from datetime import date
            date.fromisoformat(self.reference_date)
        return self


def reject_configuration_file(value):
    if isinstance(value, dict) and not any(key in value for key in ('documents', 'predictions', 'prediction_input', 'entities', 'tags')) and set(value) & set(PredictionOptions.model_fields):
        raise ValueError('这是运行配置 options.json，请通过“公开运行配置”上传；真实结果请上传 gold.json，已有抽取结果请上传预测 JSON。')


class InputDocument(StrictModel):
    document_id: str
    filename: str
    language: str
    text: str = Field(min_length=1)


class PredictionBundle(StrictModel):
    """已有算法结果；cluster/community 均为预测分组，不是人工答案。"""

    documents: list[GoldDocument] = Field(min_length=1, max_length=20)
    baseline_documents: list[GoldDocument] | None = Field(default=None, max_length=20)
    label_names: dict[str, str] = Field(default_factory=dict, description='分类标签 ID 到实际类别名称的映射，仅用于展示')

    def to_upload(self, metric):
        data = self.model_dump()
        if metric != 'multilabel-classification' or not self.label_names:
            data.pop('label_names', None)
        return data

    @model_validator(mode='after')
    def validate_documents(self):
        Annotations(documents=self.documents)
        if self.baseline_documents is not None:
            Annotations(documents=self.baseline_documents)
            if {(d.document_id, d.filename, d.language) for d in self.baseline_documents} != {
                (d.document_id, d.filename, d.language) for d in self.documents
            }:
                raise ValueError('基线结果必须与预测结果的文档 ID、文件名和语言一一对应')
        return self


class RunInput(StrictModel):
    metric: str
    documents: list[InputDocument]
    annotations: Annotations
    options: PredictionOptions = Field(default_factory=PredictionOptions)
    predictions: PredictionBundle | None = None
    allow_cached_fallback: bool = True
    fallback_threshold: float = Field(default=.90, ge=0, lt=1)

    @model_validator(mode='after')
    def validate_metric(self):
        if self.metric not in SUPPORTED_METRICS:
            raise ValueError('未知评测项目')
        if self.predictions is not None:
            if {(d.document_id, d.filename, d.language) for d in self.predictions.documents} != {
                (d.document_id, d.filename, d.language) for d in self.annotations.documents
            }:
                raise ValueError('预测结果与真实标注的文档 ID、文件名和语言必须一一对应')
        if self.metric in COMBINED_METRICS:
            # 组合任务逐项复用原校验，缺少任一目标标注会在预测前报错。
            for child in COMBINED_METRICS[self.metric]:
                self.model_copy(update={'metric': child}).validate_metric()
        if {doc.document_id for doc in self.documents} != {doc.document_id for doc in self.annotations.documents}:
            raise ValueError('文本与标注文档不对应')
        if len(self.documents) != len(self.annotations.documents):
            raise ValueError('文档数量不一致')
        gold_by_id = {doc.document_id: doc for doc in self.annotations.documents}
        if any((doc.filename, doc.language) != (gold_by_id[doc.document_id].filename, gold_by_id[doc.document_id].language) for doc in self.documents):
            raise ValueError('文本与标注的文件名或语言不一致')
        if self.metric.startswith('cross-document/') and len(self.documents) < 2:
            raise ValueError('跨文档评测至少需要两篇文本')
        if self.metric.startswith('cross-language/') and len({doc.language for doc in self.documents}) < 2:
            raise ValueError('跨语种评测至少需要两种语言')
        if '/entity-fusion' in self.metric or '/relation-fusion' in self.metric or '/attribute-fusion' in self.metric:
            field = 'entities' if 'entity-fusion' in self.metric else 'relations' if 'relation-fusion' in self.metric else 'attributes'
            items = [item for doc in self.annotations.documents for item in getattr(doc, field)]
            if len(items) < 2 or any(item.cluster is None for item in items):
                raise ValueError('消融标注至少包含两个对象，每个对象必须提供 cluster（包括不应合并的对象）')
            if sum(len(getattr(doc, field)) for doc in self.annotations.documents) > 1000:
                raise ValueError('消融标注最多 1000 个对象')
            present = [doc for doc in self.annotations.documents if getattr(doc, field)]
            if self.metric.startswith('cross-document/') and len(present) < 2:
                raise ValueError('跨文档消融标注必须分布在至少两个文档中')
            if self.metric.startswith('cross-language/') and len({doc.language for doc in present}) < 2:
                raise ValueError('跨语种消融标注必须覆盖至少两种语言')
        if self.metric == 'community-discovery' and any(entity.community is None for doc in self.annotations.documents for entity in doc.entities):
            raise ValueError('社区评测需为每个标注实体填写 community')
        if self.metric == 'community-discovery':
            entities = [entity for doc in self.annotations.documents for entity in doc.entities]
            if not entities or len(entities) > 5000:
                raise ValueError('社区标注需包含 1～5000 个节点')
            communities = {}
            for entity in entities:
                key = (entity.name, entity.type)
                if key in communities and communities[key] != entity.community:
                    raise ValueError('同名同类型实体的社区标注不一致')
                communities[key] = entity.community
            if len(set(communities.values())) > 200:
                raise ValueError('社区标注最多 200 个社区')
        if self.metric == 'spatiotemporal-resolution':
            if not any(doc.resolutions for doc in self.annotations.documents):
                raise ValueError('请提供时空消歧标注')
            for doc in self.annotations.documents:
                for item in doc.resolutions:
                    levels = self.options.time_granularities if item.dimension == 'time' else self.options.space_granularities
                    if item.granularity not in levels:
                        raise ValueError('标注粒度未包含在公开运行配置中')
        return self
