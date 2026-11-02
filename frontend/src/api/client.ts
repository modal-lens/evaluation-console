export interface Schema {
  $ref?: string;
  type?: string;
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  anyOf?: Schema[];
  enum?: unknown[];
  minLength?: number;
  minItems?: number;
  maxItems?: number;
  additionalProperties?: boolean;
  description?: string;
}

export interface Operation {
  path: string;
  title: string;
  schema: Schema;
  example: unknown;
  exampleDescription: string;
}

export interface Contract {
  operations: Record<string, Operation>;
  schemas: Record<string, Schema>;
}

export type Result = Record<string, unknown>;

export function resolveSchema(schema: Schema, schemas: Contract['schemas']): Schema {
  if (!schema.$ref) return schema;
  const name = schema.$ref.split('/').at(-1) ?? '';
  const resolved = schemas[name];
  if (!resolved) throw new Error(`无法识别参数定义 ${name}，请刷新页面或更新接口契约。`);
  return resolved;
}

function validate(value: unknown, raw: Schema, schemas: Contract['schemas'], path: string): string[] {
  const schema = resolveSchema(raw, schemas);
  if (schema.anyOf) {
    const candidates = schema.anyOf.map(option => validate(value, option, schemas, path));
    if (candidates.some(errors => errors.length === 0)) return [];
    return candidates.find((_, i) => schema.anyOf?.[i]?.type !== 'null') ?? [`${path}：值类型不符合要求`];
  }
  if (schema.type === 'null') return value === null ? [] : [`${path}：必须为 null`];
  if (schema.type === 'object') {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return [`${path}：必须为对象`];
    const object = value as Record<string, unknown>;
    const errors: string[] = [];
    for (const key of schema.required ?? []) if (!(key in object)) errors.push(`${path}.${key}：必填参数缺失`);
    for (const [key, item] of Object.entries(object)) {
      const field = schema.properties?.[key];
      if (field) errors.push(...validate(item, field, schemas, `${path}.${key}`));
      else if (schema.additionalProperties === false) errors.push(`${path}.${key}：未知参数`);
      if (errors.length >= 10) break;
    }
    return errors.slice(0, 10);
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value)) return [`${path}：必须为数组`];
    if (schema.minItems !== undefined && value.length < schema.minItems) return [`${path}：至少需要 ${schema.minItems} 项`];
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return [`${path}：最多允许 ${schema.maxItems} 项`];
    const errors: string[] = [];
    if (schema.items) for (let i = 0; i < value.length && errors.length < 10; i++) errors.push(...validate(value[i], schema.items, schemas, `${path}[${i}]`));
    return errors.slice(0, 10);
  }
  if (schema.type === 'string') {
    if (typeof value !== 'string') return [`${path}：必须为字符串`];
    if (schema.minLength !== undefined && value.trim().length < schema.minLength) return [`${path}：字符串不能为空`];
  }
  if (schema.enum && !schema.enum.includes(value)) return [`${path}：允许值为 ${schema.enum.join('、')}`];
  return [];
}

export function parseRequest(text: string, operation: Pick<Operation, 'schema'>, schemas: Contract['schemas']): unknown {
  let payload: unknown;
  try { payload = JSON.parse(text); }
  catch { throw new Error('JSON 格式不正确，请检查引号、逗号和括号。'); }
  const errors = validate(payload, operation.schema, schemas, '请求');
  if (errors.length) throw new Error(errors.join('\n'));
  return payload;
}

export function errorMessage(body: unknown, status: number): string {
  if (typeof body !== 'object' || body === null) return `接口返回异常（HTTP ${status}）`;
  const data = body as Record<string, unknown>;
  if (data.error && typeof data.error === 'object') {
    const error = data.error as Record<string, unknown>;
    if (typeof error.message === 'string') return error.message;
  }
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map(item => {
    const error = item as { loc?: (string | number)[]; msg?: string };
    return `${error.loc?.filter(part => part !== 'body').join('.') ?? '参数'}：${error.msg ?? '校验失败'}`;
  }).join('\n');
  return `接口返回异常（HTTP ${status}）`;
}

export async function evaluate(path: string, payload: unknown, signal?: AbortSignal): Promise<Result> {
  const timeout = AbortSignal.timeout(60_000);
  try {
    const response = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    let raw: unknown;
    try { raw = await response.json(); }
    catch { throw new Error(`服务返回了无法解析的响应（HTTP ${response.status}），请检查后端和代理配置。`); }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('接口返回的评测结果格式不正确。');
    const body = raw as Record<string, unknown>;
    if (!response.ok || body.success !== true) throw new Error(errorMessage(body, response.status));
    if (!body.data || typeof body.data !== 'object' || Array.isArray(body.data)) throw new Error('接口返回的评测结果格式不正确。');
    return body.data as Result;
  } catch (error) {
    if (signal?.aborted) throw new Error('本次评测已取消。');
    if (timeout.aborted) throw new Error('请求超过 60 秒，请稍后重试或减小评测数据量。');
    if (error instanceof TypeError) throw new Error('无法连接评测接口，请检查网络和后端服务。');
    throw error;
  }
}
