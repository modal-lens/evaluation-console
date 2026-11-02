"""通过实际 HTTP 服务运行模板原文的真实预测评测，分数不预设。"""

import argparse
import json
from pathlib import Path
import time
import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://127.0.0.1:50112')
    parser.add_argument('--metrics', nargs='+', default=['entity-extraction'])
    parser.add_argument('--timeout', type=int, default=1200)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    base = args.base.rstrip('/') + '/api/v1/evaluation-runs'
    metrics = args.metrics
    if metrics == ['all']:
        from sys import path
        path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tools.text_evaluation.schemas import METRICS
        metrics = METRICS
    reports = []
    try:
        for metric in metrics:
            response = requests.get(base + '/templates/' + metric, timeout=15)
            response.raise_for_status()
            example = response.json()['data']
            files = [('texts', (doc['filename'], doc['text'].encode('utf-8'), 'text/plain')) for doc in example['texts']]
            files.append(('annotations', ('annotations.json', json.dumps(example['annotations']).encode('utf-8'), 'application/json')))
            response = requests.post(base + '/' + metric, files=files, data={'options': json.dumps(example['options'])}, timeout=30)
            response.raise_for_status()
            job = response.json()['data']
            print(metric, 'submitted', job['job_id'], flush=True)
            deadline = time.monotonic() + args.timeout
            previous_stage = ''
            while job['status'] in ('queued', 'running'):
                if time.monotonic() >= deadline:
                    raise TimeoutError('客户端等待超时；后台任务可继续通过任务 ID 查询或取消')
                time.sleep(2)
                response = requests.get(base + '/jobs/' + job['job_id'], timeout=15)
                response.raise_for_status()
                job = response.json()['data']
                if job['stage'] != previous_stage:
                    print(' ', job['status'], job['progress'], job['stage'], flush=True)
                    previous_stage = job['stage']
            reports.append(job)
            print(metric, job['status'], job.get('error') or job.get('result', {}).get('metrics'), flush=True)
    finally:
        target = Path(args.output) if args.output else Path(__file__).resolve().parents[1] / 'artifacts' / f'automatic-live-results-{time.time_ns()}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Saved live report:', target, flush=True)
    if any(job['status'] != 'succeeded' for job in reports):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
