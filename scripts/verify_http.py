"""Exercise the built frontend and real backend through the Node HTTP proxy."""

import json
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODULES = {
    'entity-relation-extraction': '01_entity_relation_extraction',
    'cross-document/fusion': '02_cross_document_fusion',
    'cross-language/fusion': '03_cross_language_fusion',
    'domain-entity-extraction': '04_domain_entity_extraction',
    'spatiotemporal-resolution': '05_spatiotemporal_resolution',
    'multilabel-classification': '06_multilabel_classification',
    'community-discovery': '07_community_discovery',
}


def verify_http(base_url):
    reference = json.loads((ROOT / 'verification/standalone-apis.json').read_text(encoding='utf-8'))
    expected = {row['metric']: row['metrics'] for row in reference['modules']}
    results = []
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        for path in ('/', '/health', '/ready', '/docs', '/openapi.json', '/src/app.js', '/styles.css'):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        for metric, directory in MODULES.items():
            assert client.get('/evaluation/' + metric).status_code == 200
            assert client.get('/api/v1/evaluation-runs/templates/' + metric).status_code == 200
            folder = ROOT / 'data/evaluation_261029' / directory
            response = client.post('/api/v1/evaluation-runs/' + metric,
                files={field: (name, (folder / name).read_bytes(), 'application/json')
                       for field, name in (('annotations', 'gold.json'), ('predictions', 'predictions.json'))},
                data={'options': (folder / 'options.json').read_text(encoding='utf-8'),
                      'allow_cached_fallback': 'false'})
            assert response.status_code == 202, (metric, response.text)
            job_id = response.json()['data']['job_id']
            deadline = time.monotonic() + 45
            while True:
                response = client.get('/api/v1/evaluation-runs/jobs/' + job_id)
                assert response.status_code == 200, response.text
                job = response.json()['data']
                if job['status'] not in ('queued', 'running'):
                    break
                assert time.monotonic() < deadline, metric
                time.sleep(.1)
            assert job['status'] == 'succeeded', (metric, job['error'])
            assert job['result']['input_mode'] == 'precomputed'
            assert job['result']['metrics'] == expected[metric], metric
            assert client.post('/api/v1/evaluation-runs/jobs/' + job_id + '/delete').status_code == 200
            results.append({'metric': metric, 'passed': True, 'metrics': job['result']['metrics']})
            print('PROXY PASS ' + metric, flush=True)
    output = {'base_url': base_url, 'passed': True, 'modules': results,
              'model_calls': 0, 'mode': 'uploaded predictions, real HTTP frontend proxy and backend'}
    (ROOT / 'verification/proxy-http.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:50112')
    verify_http(parser.parse_args().base_url)
