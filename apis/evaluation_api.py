"""独立评测 API：接收预测与人工标注，不启动抽取任务或修改业务数据。"""

from typing import Annotated

from fastapi import APIRouter, Body

from apis.evaluation_examples import REQUEST_EXAMPLES

from tools import evaluation
from tools.evaluation_schemas import (
    ClassificationEvaluationRequest, CommunityEvaluationRequest,
    DomainEntityEvaluationRequest, EntityEvaluationRequest, FusionEvaluationRequest,
    RelationEvaluationRequest, ResolutionEvaluationRequest,
)
from tools.schemas import StandardResponse


router = APIRouter(prefix="/api/v1/evaluation", tags=["evaluation"])


@router.post("/entity-extraction", response_model=StandardResponse, summary="实体抽取 F1")
def entity_extraction(request: Annotated[EntityEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["entity-extraction"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_entities(request))


@router.post("/relation-extraction", response_model=StandardResponse, summary="关系抽取 F1")
def relation_extraction(request: Annotated[RelationEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["relation-extraction"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_relations(request))


@router.post("/cross-document/entity-fusion", response_model=StandardResponse, summary="跨文档实体消融 F1")
def cross_document_entities(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-document/entity-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-document", "entity"))


@router.post("/cross-document/relation-fusion", response_model=StandardResponse, summary="跨文档关系消融 F1")
def cross_document_relations(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-document/relation-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-document", "relation"))


@router.post("/cross-document/attribute-fusion", response_model=StandardResponse, summary="跨文档属性消融 F1")
def cross_document_attributes(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-document/attribute-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-document", "attribute"))


@router.post("/cross-language/entity-fusion", response_model=StandardResponse, summary="跨语种实体消融 F1")
def cross_language_entities(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-language/entity-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-language", "entity"))


@router.post("/cross-language/relation-fusion", response_model=StandardResponse, summary="跨语种关系消融 F1")
def cross_language_relations(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-language/relation-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-language", "relation"))


@router.post("/cross-language/attribute-fusion", response_model=StandardResponse, summary="跨语种属性消融 F1")
def cross_language_attributes(request: Annotated[FusionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["cross-language/attribute-fusion"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_fusion(request, "cross-language", "attribute"))


@router.post("/domain-entity-extraction", response_model=StandardResponse, summary="引入专业领域实体类型列表后的实体抽取 F1")
def domain_entity_extraction(request: Annotated[DomainEntityEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["domain-entity-extraction"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_domain_entities(request))


@router.post("/spatiotemporal-resolution", response_model=StandardResponse, summary="多粒度时空消歧准确率")
def spatiotemporal_resolution(request: Annotated[ResolutionEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["spatiotemporal-resolution"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_resolution(request))


@router.post("/multilabel-classification", response_model=StandardResponse, summary="多标签分类 F1")
def multilabel_classification(request: Annotated[ClassificationEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["multilabel-classification"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_classification(request))


@router.post("/community-discovery", response_model=StandardResponse, summary="社区网络发现准确率")
def community_discovery(request: Annotated[CommunityEvaluationRequest, Body(openapi_examples=REQUEST_EXAMPLES["community-discovery"])]) -> StandardResponse:
    return StandardResponse(success=True, data=evaluation.evaluate_communities(request))
