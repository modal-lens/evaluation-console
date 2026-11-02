import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseRequest } from '../dist/src/api/client.js';

test('实体评测提交前拒绝错误 JSON 和缺失人工标注，接受完整请求', () => {
  const operation = { schema: { type: 'object', required: ['samples'], properties: {
    samples: { type: 'array', minItems: 1, items: { type: 'object', required: ['sample_id', 'predicted', 'gold'], properties: {
      sample_id: { type: 'string', minLength: 1 }, predicted: { type: 'array' }, gold: { type: 'array' },
    } } },
  } } };
  assert.throws(() => parseRequest('{', operation, {}), /JSON/);
  assert.throws(() => parseRequest('{"samples":[]}', operation, {}), /至少/);
  assert.throws(() => parseRequest('{"samples":[{"sample_id":"1","predicted":[]}]}', operation, {}), /gold/);
  const payload = { samples: [{ sample_id: '1', predicted: [], gold: [] }] };
  assert.deepEqual(parseRequest(JSON.stringify(payload), operation, {}), payload);
});
