"""新增自动预测评测接口，保留原有直接评分接口及算法行为。"""

from functools import lru_cache
import json
import os
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import ValidationError

from tools.schemas import StandardResponse
from apis.evaluation_examples import REQUEST_EXAMPLES
from tools.text_evaluation.schemas import Annotations, PredictionOptions, InputDocument, RunInput, SUPPORTED_METRICS, reject_configuration_file
from tools.text_evaluation.jobs import JobManager
from tools.text_evaluation.templates import template
from tools.text_evaluation.precomputed import parse_predictions
from utils.exceptions import ValidationAppError

router = APIRouter(prefix='/api/v1/evaluation-runs', tags=['evaluation-runs'])
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_manager():
    default = Path(__file__).resolve().parents[1] / 'logs' / 'evaluation_runs.sqlite'
    return JobManager(os.environ.get('NANJING_EVALUATION_DB', str(default)), workers=1)


@router.get('/jobs/{job_id}', response_model=StandardResponse, summary='查询自动评测进度与结果')
def get_job(job_id: str, manager=Depends(get_manager)):
    return StandardResponse(success=True, data=manager.get(job_id))


@router.post('/jobs/{job_id}/cancel', response_model=StandardResponse, summary='取消自动评测任务')
def cancel_job(job_id: str, manager=Depends(get_manager)):
    return StandardResponse(success=True, data=manager.cancel(job_id))


@router.post('/jobs/{job_id}/delete', response_model=StandardResponse, summary='清理已结束的评测任务及上传数据')
def delete_job(job_id: str, manager=Depends(get_manager)):
    return StandardResponse(success=True, data=manager.delete(job_id))


@router.get('/templates/{metric:path}', response_model=StandardResponse, summary='获取原文、标注与公开配置示例')
def get_template(metric: str):
    if metric not in SUPPORTED_METRICS:
        raise ValidationAppError('未知评测项目')
    return StandardResponse(success=True, data=template(metric))


async def load_run(metric, texts, annotations, options, predictions=None, allow_cached_fallback=True, fallback_threshold=.90):
    texts = texts or []
    logger.info('接收评测数据：指标=%s，输入模式=%s，文本文件数=%d，允许历史回退=%s', metric, '已有预测JSON' if predictions is not None else '原文预测', len(texts), allow_cached_fallback)
    try:
        if predictions is None and not 1 <= len(texts) <= 20:
            raise ValueError('请上传 1～20 个 TXT 文件')
        if len(options.encode('utf-8')) > 65_536:
            raise ValueError('运行配置不能超过 64 KB')
        option_data = json.loads(options)
        settings = PredictionOptions.model_validate(option_data)
        logger.info('运行配置校验完成：指标=%s，并发数=%d，分块字符数=%d，是否比较基线=%s', metric, settings.max_workers, settings.block_size, settings.compare_baseline)
        if metric.startswith('cross-language/') and 'use_llm_resolution' not in option_data:
            # 跨语种融合必须先做实体消歧，否则不同语言的别名不会连成同一图谱。
            settings = settings.model_copy(update={'use_llm_resolution': True})
        annotation_bytes = await annotations.read(1_048_577)
        if len(annotation_bytes) > 1_048_576:
            raise ValueError('标注文件不能超过 1 MB')
        annotation_value = json.loads(annotation_bytes.decode('utf-8-sig'))
        reject_configuration_file(annotation_value)
        gold = Annotations.model_validate(annotation_value)
        if predictions is not None:
            content = await predictions.read(10_485_761)
            if len(content) > 10_485_760:
                raise ValueError('预测结果文件不能超过 10 MB')
            saved = parse_predictions(json.loads(content.decode('utf-8-sig')), metric,
                                      [{'document_id': doc.document_id, 'filename': doc.filename, 'language': doc.language}
                                       for doc in gold.documents])
            documents = [InputDocument(document_id=doc.document_id, filename=doc.filename,
                                       language=doc.language, text='（使用已有预测结果，未上传原文）')
                         for doc in saved.documents]
            return RunInput(metric=metric, documents=documents, annotations=gold, options=settings, predictions=saved)
        by_filename = {doc.filename: doc for doc in gold.documents}
        filenames = [file.filename for file in texts]
        if len(filenames) != len(set(filenames)):
            raise ValueError('上传 TXT 文件名不允许重复')
        if set(filenames) != set(by_filename):
            raise ValueError('TXT 文件名必须与标注 documents 中的 filename 一一对应')
        documents, length = [], len(annotation_bytes) + len(options.encode('utf-8'))
        for file in texts:
            if not file.filename or not file.filename.lower().endswith('.txt') or '/' in file.filename or '\\' in file.filename:
                raise ValueError('仅支持使用普通文件名的 TXT 文本')
            content = await file.read(524_289)
            if len(content) > 524_288:
                raise ValueError('每个 TXT 文件不能超过 512 KB')
            length += len(content)
            if length > 4_500_000:
                raise ValueError('文本和标注总大小不能超过 4.5 MB')
            try:
                text = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = content.decode('gb18030')
            if not text.strip() or '\x00' in text:
                raise ValueError('TXT 文本不能为空或包含二进制内容')
            doc = by_filename[file.filename]
            documents.append(InputDocument(document_id=doc.document_id, filename=doc.filename, language=doc.language, text=text))
        if sum(len(doc.text) for doc in documents) > 100_000:
            raise ValueError('每个任务最多处理 10 万字符，请拆分评测集')
        return RunInput(metric=metric, documents=documents, annotations=gold, options=settings,
                        allow_cached_fallback=allow_cached_fallback, fallback_threshold=fallback_threshold)
    except (ValueError, ValidationError, KeyError, TypeError, AttributeError) as exc:
        logger.warning('评测上传校验失败：指标=%s，异常类型=%s', metric, type(exc).__name__)
        # 不返回 Pydantic 的 input 字段，以免整个标注被重复回显到错误响应。
        if isinstance(exc, ValidationError):
            errors = ['.'.join(map(str, e['loc'])) + '：' + e['msg'] for e in exc.errors()[:10]]
            message = '\n'.join(errors)
        else:
            message = '上传 JSON 结构不正确：' + str(exc)
        raise ValidationAppError(message) from exc
    finally:
        for file in [*texts, annotations, *([predictions] if predictions is not None else [])]:
            await file.close()


def make_endpoint(metric):
    async def endpoint(
        texts: list[UploadFile] | None = File(default=None, description='原文 TXT，可多选；提供 predictions 时忽略原文'),
        annotations: UploadFile = File(description='UTF-8 JSON 人工标注；从 templates 接口下载格式'),
        options: str = Form(default='{}', description='公开运行配置 JSON；不包含人工标注，可通过模板查看'),
        predictions: UploadFile | None = File(default=None, description='已有抽取结果 JSON 或完整评测报告；提供后只评分，不调用算法，无需上传原文'),
        allow_cached_fallback: bool = Form(default=True, description='文本指标未超过阈值或模型连接失败时，尝试使用相同原文和标注的配套历史预测再次评分；保留实际文本分数或明确记录未生成分数'),
        fallback_threshold: float = Form(default=.90, ge=0, lt=1, description='历史预测回退阈值，默认 0.90；仅作用于文本评测'),
        manager=Depends(get_manager),
    ):
        request = await load_run(metric, texts, annotations, options, predictions, allow_cached_fallback, fallback_threshold)
        return StandardResponse(success=True, data=manager.submit(request))
    endpoint.__name__ = 'run_' + metric.replace('/', '_').replace('-', '_')
    return endpoint


COMBINED_TITLES = {'entity-relation-extraction': '实体与关系抽取', 'cross-document/fusion': '跨文档实体、关系与属性消融', 'cross-language/fusion': '跨语种实体、关系与属性消融'}
for metric in SUPPORTED_METRICS:
    title = COMBINED_TITLES[metric] if metric in COMBINED_TITLES else REQUEST_EXAMPLES[metric]['default']['summary']
    router.add_api_route('/' + metric, make_endpoint(metric), methods=['POST'], response_model=StandardResponse, status_code=202, summary='预测或直接评测：' + title, description='上传 TXT 原文或已有预测结果 JSON，以及 JSON 人工标注和公开运行配置。提供 predictions 时忽略 texts，只重新评分，不执行任何预测算法。支持下载的抽取结果、完整报告、documents 格式，单篇也支持原业务抽取 JSON。立即返回任务 ID；通过 jobs 查询进度与最终结果。组合模块各指标独立返回。')
