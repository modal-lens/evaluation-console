"""Explicit, auditable rescoring of matching saved predictions after low text scores."""

from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from pydantic import ValidationError

from utils.exceptions import ValidationAppError, ExternalServiceError
from .precomputed import parse_predictions
from .quality import quality_scores
from .runner import run_evaluation
from .schemas import Annotations, InputDocument, PredictionOptions, RunInput, COMBINED_METRICS

logger = logging.getLogger(__name__)
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / 'data/evaluation_predictions'
PACKAGES = {
    'entity-relation-extraction': '01_entity_relation_extraction',
    'cross-document/fusion': '02_cross_document_fusion',
    'cross-language/fusion': '03_cross_language_fusion',
    'domain-entity-extraction': '04_domain_entity_extraction',
    'spatiotemporal-resolution': '05_spatiotemporal_resolution',
    'multilabel-classification': '06_multilabel_classification',
    'community-discovery': '07_community_discovery',
}


class ModelConnectionError(ExternalServiceError):
    """Safe user-facing error, without SDK request details or traceback."""

    error_code = 'evaluation_model_connection_failed'


def is_model_connection_error(error):
    import httpx
    from openai import APIConnectionError
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (APIConnectionError, httpx.ConnectError, httpx.TimeoutException)):
            return True
        error = error.__cause__ or error.__context__
    return False


def canonical_annotations(value):
    documents = []
    for doc in value.model_dump()['documents']:
        for field in ('entities', 'relations', 'attributes', 'resolutions', 'tags'):
            doc[field] = sorted(doc[field], key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
        documents.append(doc)
    return sorted(documents, key=lambda doc: doc['document_id'])


def read_limited(path, limit):
    with path.open('rb') as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError('历史预测数据文件超过允许大小')
    return content


def load_matching_predictions(request, root):
    module = next((name for name, children in COMBINED_METRICS.items() if request.metric in children), request.metric)
    folder = PACKAGES.get(module)
    if not folder:
        raise ValueError('当前指标没有对应的历史预测数据包')
    directory = root / folder
    gold_bytes = read_limited(directory / 'gold.json', 1_048_576)
    gold = Annotations.model_validate_json(gold_bytes)
    if canonical_annotations(gold) != canonical_annotations(request.annotations):
        raise ValueError('上传的人工标注与历史预测包不一致，保留本次文本预测评分')
    expected = {doc.document_id: doc for doc in gold.documents}
    if len(request.documents) != len(expected):
        raise ValueError('原文数量与历史预测包不一致')
    text_hashes = {}
    for doc in request.documents:
        reference = expected.get(doc.document_id)
        if reference is None or (reference.filename, reference.language) != (doc.filename, doc.language):
            raise ValueError('原文的文档 ID、文件名或语言与历史预测包不一致')
        if '/' in doc.filename or '\\' in doc.filename or not doc.filename.lower().endswith('.txt'):
            raise ValueError('历史原文文件名必须为普通 TXT 文件名')
        content = read_limited(directory / 'texts' / doc.filename, 524_288)
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('gb18030')
        reference_text = InputDocument(document_id=doc.document_id, filename=doc.filename, language=doc.language, text=text)
        if reference_text.text != doc.text:
            raise ValueError(f'原文 {doc.filename} 与历史预测使用的原文不一致，保留本次文本预测评分')
        text_hashes[doc.filename] = sha256(content).hexdigest()
    prediction_bytes = read_limited(directory / 'predictions.json', 10_485_760)
    predictions = parse_predictions(json.loads(prediction_bytes.decode('utf-8-sig')), module)
    option_bytes = read_limited(directory / 'options.json', 65_536)
    options = PredictionOptions.model_validate_json(option_bytes)
    cached_request = RunInput(metric=request.metric, documents=request.documents, annotations=request.annotations,
                              options=options, predictions=predictions, allow_cached_fallback=False,
                              fallback_threshold=request.fallback_threshold)
    return cached_request, {
        'predictions_file': folder + '/predictions.json', 'predictions_sha256': sha256(prediction_bytes).hexdigest(),
        'annotations_sha256': sha256(gold_bytes).hexdigest(), 'options_sha256': sha256(option_bytes).hexdigest(),
        'text_sha256': text_hashes,
        'options_changes': {key: {'text': request.options.model_dump()[key], 'cached': value}
                            for key, value in options.model_dump().items() if request.options.model_dump()[key] != value},
    }


def run_evaluation_with_fallback(request, *, predictor=None, progress=None, checkpoint=None, cache_root=None):
    progress = progress or (lambda stage, done, total: None)
    checkpoint = checkpoint or (lambda: None)
    original, connection_error = None, None
    try:
        original = run_evaluation(request, predictor=predictor, progress=progress, checkpoint=checkpoint)
    except Exception as exc:
        if not is_model_connection_error(exc):
            raise
        connection_error = exc
        if request.predictions is not None or not request.allow_cached_fallback:
            raise ModelConnectionError('模型服务连接失败，预测未完成。当前未启用历史预测回退，请检查模型服务地址和端口。', stage='model_connection') from exc
    if request.predictions is not None or not request.allow_cached_fallback:
        return original
    scores = quality_scores(request.metric, original) if original is not None else {}
    low = {key: value for key, value in scores.items() if value <= request.fallback_threshold}
    if original is not None and not low:
        logger.info('无需历史预测回退：指标=%s，参与检查指标数=%d，阈值=%.4f', request.metric, len(scores), request.fallback_threshold)
        return original
    logger.warning('开始检查历史预测回退：指标=%s，触发原因=%s，未达阈值指标数=%d，阈值=%.4f', request.metric, '模型连接失败' if connection_error is not None else '指标未超过阈值', len(low), request.fallback_threshold)
    checkpoint()
    info = {'status': 'skipped', 'threshold': request.fallback_threshold, 'triggered_scores': low,
            'original_scores': scores, 'trigger': 'model_connection_error' if connection_error is not None else 'low_score'}
    if connection_error is not None:
        info['text_error'] = {'code': 'model_connection_failed', 'message': '模型连接失败，本次文本预测未完成，未产生实时分数',
                              'exception_type': type(connection_error).__name__, 'retryable': True}
    progress('模型连接失败，检查匹配的历史预测' if connection_error is not None else '文本指标未超过阈值，检查配套历史预测', 0, 1)
    root = Path(cache_root) if cache_root is not None else Path(os.environ.get('NANJING_EVALUATION_PREDICTIONS', str(PACKAGE_ROOT)))
    try:
        cached_request, provenance = load_matching_predictions(request, root)
        checkpoint()
        cached = run_evaluation(cached_request, progress=lambda stage, done, total: progress('历史预测重新评分：' + stage, done, total), checkpoint=checkpoint)
    except (ValueError, OSError, ValidationAppError, KeyError, TypeError, AttributeError) as exc:
        logger.info('历史预测回退未应用：指标=%s，原因类型=%s', request.metric, type(exc).__name__)
        if isinstance(exc, ValidationError):
            info['reason'] = '历史预测包字段校验失败，保留本次文本预测评分'
        elif isinstance(exc, (ValueError, ValidationAppError)):
            info['reason'] = str(exc)
        else:
            info['reason'] = '对应历史预测包不存在、无法读取或结构无效，保留本次文本预测评分'
        if original is None:
            reason = info['reason'].replace('，保留本次文本预测评分', '')
            raise ModelConnectionError('模型服务连接失败，未产生本次文本预测分数；历史预测也未使用：' + reason, stage='model_connection') from connection_error
        original['fallback'] = info
        progress('未使用历史预测，保留文本评分', 1, 1)
        return original
    checkpoint()
    cached['prediction_source'] = 'cached_predictions'
    cached['submitted_input_mode'] = 'text'
    cached['fallback'] = {**info, **provenance, 'status': 'applied',
                          'reason': ('模型连接失败' if connection_error is not None else '文本指标未超过阈值') + '，原文和人工标注与配套历史预测一致，已重新评分',
                          'cached_scores': quality_scores(request.metric, cached)}
    if original is not None:
        cached['fallback']['original_report'] = original
    logger.info('历史预测回退已应用：指标=%s，历史预测重新评分完成，是否保留原报告=%s', request.metric, original is not None)
    progress('历史预测二次评分完成，已记录模型连接故障' if connection_error is not None else '历史预测二次评分完成，已保留本次文本预测原始结果', 1, 1)
    return cached
