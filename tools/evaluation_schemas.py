"""评测专用输入；不改变现有抽取和 MQ 协议。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EntityFact(EvaluationModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)


class RelationFact(EvaluationModel):
    head_entity_id: str = Field(min_length=1, description="预测与标注使用同一实体 ID 空间")
    relationCode: str = Field(min_length=1)
    tail_entity_id: str = Field(min_length=1)


class EntitySample(EvaluationModel):
    sample_id: str = Field(min_length=1)
    predicted: list[EntityFact]
    gold: list[EntityFact]


class RelationSample(EvaluationModel):
    sample_id: str = Field(min_length=1)
    predicted: list[RelationFact]
    gold: list[RelationFact]


class SamplesRequest(EvaluationModel):
    @model_validator(mode="after")
    def unique_samples(self):
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("sample_id 不允许重复")
        return self


class EntityEvaluationRequest(SamplesRequest):
    samples: list[EntitySample] = Field(min_length=1)


class RelationEvaluationRequest(SamplesRequest):
    samples: list[RelationSample] = Field(min_length=1)


class DomainEntityEvaluationRequest(EntityEvaluationRequest):
    domain_entity_types: list[str] = Field(min_length=1, description="专业领域实体类型列表")
    baseline_samples: list[EntitySample] | None = Field(default=None, min_length=1)
    relation_samples: list[RelationSample] | None = Field(default=None, min_length=1, description="引入类型列表后的关系预测及同一批真实标注")
    baseline_relation_samples: list[RelationSample] | None = Field(default=None, min_length=1, description="不引入任何类型列表时的关系预测")

    @model_validator(mode="after")
    def validate_domain(self):
        if any(not value for value in self.domain_entity_types):
            raise ValueError("专业领域实体类型不能为空")
        if self.baseline_samples is not None:
            baseline = {sample.sample_id: sample for sample in self.baseline_samples}
            if len(baseline) != len(self.baseline_samples) or set(baseline) != {s.sample_id for s in self.samples}:
                raise ValueError("baseline_samples 必须与 samples 一一对应")
            for sample in self.samples:
                if {(x.name, x.type) for x in sample.gold} != {(x.name, x.type) for x in baseline[sample.sample_id].gold}:
                    raise ValueError("基线与领域配置后的人工标注必须相同")
        ids = {sample.sample_id for sample in self.samples}
        for rows in (self.relation_samples, self.baseline_relation_samples):
            if rows is not None and (len(rows) != len(ids) or {row.sample_id for row in rows} != ids):
                raise ValueError("关系样本必须与实体样本一一对应")
        if self.baseline_relation_samples is not None:
            if self.relation_samples is None or self.baseline_samples is None:
                raise ValueError("基线关系必须同时提供当前关系和基线实体样本")
            gold = {row.sample_id: {tuple(item.model_dump().values()) for item in row.gold} for row in self.relation_samples}
            if any({tuple(item.model_dump().values()) for item in row.gold} != gold[row.sample_id] for row in self.baseline_relation_samples):
                raise ValueError("基线与引入后的关系真实标注必须相同")
        return self


class FusionItem(EvaluationModel):
    item_id: str = Field(min_length=1, description="待合并实体、关系或属性的唯一出现 ID")
    document_id: str = Field(min_length=1)
    language: str = Field(min_length=1, description="统一语言代码，例如 zh、en")
    gold_cluster: str = Field(min_length=1)
    predicted_cluster: str | None = Field(default=None, min_length=1, description="未合并/漏预测可传 null")


class FusionEvaluationRequest(EvaluationModel):
    items: list[FusionItem] = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def unique_items(self):
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("item_id 不允许重复")
        return self


class ResolutionItem(EvaluationModel):
    sample_id: str = Field(min_length=1)
    mention_id: str = Field(min_length=1)
    dimension: Literal["time", "space"]
    granularity: str = Field(min_length=1, description="如 year、day、country、city；在相同粒度精确比较")
    gold: str = Field(min_length=1, description="人工标注的标准化值")
    predicted: str | None = Field(default=None, min_length=1)


class ResolutionEvaluationRequest(EvaluationModel):
    items: list[ResolutionItem] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_items(self):
        keys = [(x.sample_id, x.mention_id, x.dimension, x.granularity) for x in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("相同样本、提及、时空类型和粒度的标注不允许重复")
        return self


class ClassificationSample(EvaluationModel):
    sample_id: str = Field(min_length=1)
    predicted: list[str]
    gold: list[str]

    @model_validator(mode="after")
    def nonempty_labels(self):
        if any(not label for label in self.predicted + self.gold):
            raise ValueError("标签不能为空")
        return self


class ClassificationEvaluationRequest(SamplesRequest):
    samples: list[ClassificationSample] = Field(min_length=1)


class CommunityItem(EvaluationModel):
    node_id: str = Field(min_length=1)
    gold_community: str = Field(min_length=1)
    predicted_community: str | None = Field(default=None, min_length=1)


class CommunityEvaluationRequest(EvaluationModel):
    items: list[CommunityItem] = Field(min_length=1, max_length=5000)

    @model_validator(mode="after")
    def unique_nodes(self):
        ids = [item.node_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("node_id 不允许重复")
        if len({x.gold_community for x in self.items} | {x.predicted_community for x in self.items if x.predicted_community}) > 200:
            raise ValueError("社区标签总数不能超过 200")
        return self
