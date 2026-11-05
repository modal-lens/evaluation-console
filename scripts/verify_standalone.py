"""Verify the metric-only inventory, local imports, and all seven upload APIs."""

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['APP_CONFIG_PATH'] = str(ROOT / 'conf/config.yml')

from fastapi.testclient import TestClient
from fastapi_worker import create_app
from apis.evaluation_runs_api import get_manager
from tools.text_evaluation.jobs import JobManager
from tools.text_evaluation.runner import run_evaluation
from tools.text_evaluation.fallback import PACKAGES


class NoAlgorithms:
    def __getattr__(self, name):
        raise AssertionError('Uploaded predictions must not invoke algorithms: ' + name)


def main():
    manifest = json.loads((ROOT / 'COPY_MANIFEST.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        assert sha256((ROOT / row['path']).read_bytes()).hexdigest() == row['sha256'], row['path']
    # All project packages must be loaded from the new directory, even when this
    # directory is moved out of the original workspace.
    imports = {}
    for name, module in list(sys.modules.items()):
        if name.split('.')[0] in ('apis', 'conf', 'tools', 'utils', 'repositories', 'fastapi_worker'):
            location = getattr(module, '__file__', None)
            if location:
                assert Path(location).resolve().is_relative_to(ROOT), (name, location)
                imports[name] = Path(location).relative_to(ROOT).as_posix()
    results = []
    with TemporaryDirectory(prefix='standalone-api-') as temporary:
        manager = JobManager(Path(temporary) / 'jobs.sqlite', workers=1,
            runner=lambda request, **kwargs: run_evaluation(request, predictor=NoAlgorithms(), **kwargs))
        app = create_app()
        app.dependency_overrides[get_manager] = lambda: manager
        try:
            with TestClient(app) as client:
                for route in ('/health', '/ready', '/openapi.json', '/docs'):
                    response = client.get(route)
                    assert response.status_code == 200, (route, response.text)
                paths = client.get('/openapi.json').json()['paths']
                assert all(path in ('/health', '/ready') or path.startswith(
                    ('/api/v1/evaluation/', '/api/v1/evaluation-runs/', '/api/v1/sql-evaluation/', '/api/v1/mcp-console/')) for path in paths)
                for metric, directory in PACKAGES.items():
                    folder = ROOT / 'data/evaluation_261029' / directory
                    response = client.get('/api/v1/evaluation-runs/templates/' + metric)
                    assert response.status_code == 200, response.text
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
                        assert response.status_code == 200
                        job = response.json()['data']
                        if job['status'] not in ('queued', 'running'):
                            break
                        assert time.monotonic() < deadline, metric
                        time.sleep(.05)
                    assert job['status'] == 'succeeded', (metric, job['error'])
                    assert job['result']['input_mode'] == 'precomputed'
                    if metric == 'entity-relation-extraction':
                        assert .89 < job['result']['metrics']['entity-extraction']['f1'] < .94
                        assert .8 < job['result']['metrics']['relation-extraction']['f1'] < .9
                    results.append({'metric': metric, 'status': 'passed', 'metrics': job['result']['metrics']})
                    print('PASS ' + metric, flush=True)
                    response = client.post('/api/v1/evaluation-runs/jobs/' + job_id + '/delete')
                    assert response.status_code == 200
        finally:
            manager.close()
    output = {'metric_project_hashes_verified': len(manifest['files']), 'local_imports': imports,
              'modules': results, 'algorithm_calls': 0, 'passed': True}
    destination = ROOT / 'verification/standalone-apis.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Verified all seven upload modules without external services.', flush=True)


if __name__ == '__main__':
    main()
