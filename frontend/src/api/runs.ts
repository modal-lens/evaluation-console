import { errorMessage, type Result, type Schema } from './client.js';

export interface RunReport {
  metric: string; metrics: Result; predictions: { document_id: string; filename: string; graph: Result }[];
  details: Result; options: unknown; annotations: unknown; documents: { filename: string; text: string }[];
  prediction_input?: unknown; input_mode?: 'text' | 'precomputed';
  prediction_source?: 'cached_predictions'; submitted_input_mode?: 'text';
  fallback?: {
    status: 'applied' | 'skipped'; threshold: number; reason: string;
    original_scores: Record<string, number>; cached_scores?: Record<string, number>;
    original_report?: RunReport; predictions_file?: string;
    options_changes?: Record<string, { text: unknown; cached: unknown }>;
    trigger?: 'low_score' | 'model_connection_error';
    text_error?: { code: string; message: string; exception_type: string; retryable: boolean };
  };
}
export interface Job {
  job_id: string; metric: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  stage: string; progress: number; result: RunReport | null; error: { message: string; code?: string } | null;
}
export interface RunTemplate {
  annotations: unknown; options: unknown; texts: { filename: string; text: string }[]; notes: string[];
  annotation_schema: Schema & { $defs?: Record<string, Schema> };
  options_schema: Schema & { $defs?: Record<string, Schema> };
  prediction_example?: unknown;
}
const prefix = '/api/v1/evaluation-runs';

export function assertEvaluationData(value: unknown): void {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return;
  const keys = Object.keys(value);
  if (!keys.some(key => ['documents', 'predictions', 'prediction_input', 'entities', 'tags'].includes(key)) &&
      keys.some(key => ['name_mapping_policy', 'entity_types', 'entity_attributes', 'relation_types', 'domain_entity_types', 'compare_baseline', 'block_size', 'max_workers', 'similarity_threshold', 'attribute_similarity_threshold', 'use_llm_resolution', 'time_granularities', 'space_granularities', 'reference_date'].includes(key))) {
    throw new Error('这是运行配置 options.json，请通过“公开运行配置”上传；真实结果请上传 gold.json，已有抽取结果请上传预测 JSON。');
  }
}

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  try {
    const timeout = AbortSignal.timeout(30_000);
    const response = await fetch(prefix + path, { ...init, signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
    let body: { success: boolean; data: T };
    try { body = await response.json() as typeof body; }
    catch { throw new Error(`后端响应格式不正确（HTTP ${response.status}），请检查服务版本。`); }
    if (!response.ok || body.success !== true) throw new Error(errorMessage(body, response.status));
    return body.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof TypeError) throw new Error('无法连接自动评测服务，请检查本地后端及网络。');
    if (error instanceof DOMException && error.name === 'TimeoutError') throw new Error('查询或上传超时；后台任务不会因此自动取消，可点击重新查询。');
    throw error;
  }
}

export const getTemplate = (metric: string, signal?: AbortSignal): Promise<RunTemplate> => request(`/templates/${metric}`, {}, signal);
export const getJob = (id: string, signal?: AbortSignal): Promise<Job> => request(`/jobs/${encodeURIComponent(id)}`, {}, signal);
export const cancelJob = (id: string): Promise<Job> => request(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
export const deleteJob = (id: string): Promise<unknown> => request(`/jobs/${encodeURIComponent(id)}/delete`, { method: 'POST' });
export function submitRun(metric: string, files: File[], annotation: unknown, options: unknown, signal?: AbortSignal, predictions?: unknown, allowCachedFallback = true): Promise<Job> {
  const form = new FormData();
  if (predictions !== undefined) form.append('predictions', new Blob([JSON.stringify(predictions)], { type: 'application/json' }), 'predictions.json');
  else for (const file of files) form.append('texts', file, file.name);
  form.append('annotations', new Blob([JSON.stringify(annotation)], { type: 'application/json' }), 'annotations.json');
  form.append('options', JSON.stringify(options));
  form.append('allow_cached_fallback', String(predictions === undefined && allowCachedFallback));
  return request('/' + metric, { method: 'POST', body: form }, signal);
}

export function delay(signal: AbortSignal, ms = 1000): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}
