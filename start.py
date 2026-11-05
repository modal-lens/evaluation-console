"""Start this copy's backend and frontend together; Ctrl+C stops both."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import logging
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent


def available_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        if os.name == 'nt':
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        connection.bind((host, port))


def wait_ready(process, url, timeout=120):
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Service exited with code {process.returncode}: {url}')
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(.2)
    raise TimeoutError('Service startup timed out: ' + url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--backend-port', type=int, default=50111)
    parser.add_argument('--frontend-port', type=int, default=50112)
    parser.add_argument('--check', action='store_true', help='Check dependencies and ports, without starting services')
    parser.add_argument('--smoke-test', action='store_true', help='Start, verify all seven upload pages/APIs, and stop')
    args = parser.parse_args()
    if args.backend_port == args.frontend_port or not all(1 <= p <= 65535 for p in (args.backend_port, args.frontend_port)):
        parser.error('Choose two distinct ports between 1 and 65535.')
    missing = [name for name in ('fastapi', 'uvicorn', 'multipart', 'pydantic', 'yaml', 'httpx',
        'psycopg2', 'openai', 'igraph', 'leidenalg')
        if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError('Missing Python packages: ' + ', '.join(missing) +
                           '. Run: python -m pip install -r requirements-standalone.txt')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js 22 or newer is required.')
    node_version = subprocess.check_output([node, '--version'], text=True).strip()
    if int(node_version.lstrip('v').split('.')[0]) < 22:
        raise RuntimeError('Node.js 22 or newer is required; found ' + node_version)
    for name in ('conf/config.yml', 'frontend/dist/index.html', 'frontend/dist/src/app.js'):
        if not (ROOT / name).is_file():
            raise FileNotFoundError(f'Missing {name}. For frontend assets run npm ci && npm run build in frontend/.')
    for port in (args.backend_port, args.frontend_port):
        available_port(args.host, port)
    print(json.dumps({'project': str(ROOT), 'python': sys.executable, 'node': node_version,
        'backend_port': args.backend_port, 'frontend_port': args.frontend_port,
        'checks': 'passed'}, ensure_ascii=False), flush=True)
    if args.check:
        return 0
    from utils.logger import setup_logging
    from conf.load_config import get_max_workers
    setup_logging(service='startup')
    logger = logging.getLogger(__name__)
    logger.info('启动检查通过：后端端口=%d，前端端口=%d，模型默认并发=%d', args.backend_port, args.frontend_port, get_max_workers())
    environment = os.environ.copy()
    environment.update({
        'PYTHONPATH': str(ROOT), 'APP_CONFIG_PATH': str(ROOT / 'conf/config.yml'),
        'PYTHONIOENCODING': 'utf-8',
        'NANJING_EVALUATION_DB': str(ROOT / 'logs/evaluation_runs.sqlite'),
        'NANJING_EVALUATION_PREDICTIONS': str(ROOT / 'data/evaluation_predictions'),
        'NANJING__SERVER__HOST': args.host, 'NANJING__SERVER__PORT': str(args.backend_port),
        'NANJING__SERVER__RELOAD': 'false',
    })
    children = []
    local_address = '127.0.0.1' if args.host == '0.0.0.0' else args.host
    hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'fastapi_worker:app',
            '--app-dir', str(ROOT), '--host', args.host, '--port', str(args.backend_port), '--no-access-log'],
            cwd=ROOT, env=environment, **hidden))
        logger.info('后端进程已启动：进程ID=%d，正在等待健康检查', children[0].pid)
        wait_ready(children[0], f'http://{local_address}:{args.backend_port}/health')
        frontend_environment = {**environment, 'HOST': args.host, 'PORT': str(args.frontend_port),
                                'BACKEND_URL': f'http://{local_address}:{args.backend_port}'}
        children.append(subprocess.Popen([node, 'server.mjs'], cwd=ROOT / 'frontend',
                                          env=frontend_environment, **hidden))
        logger.info('前端进程已启动：进程ID=%d，正在等待页面服务就绪', children[1].pid)
        wait_ready(children[1], f'http://{local_address}:{args.frontend_port}/')
        logger.info('前后端启动完成：页面=http://%s:%d/，接口文档=http://%s:%d/docs', local_address, args.frontend_port, local_address, args.frontend_port)
        if args.smoke_test:
            from scripts.verify_http import verify_http
            verify_http(f'http://{local_address}:{args.frontend_port}')
            return 0
        logger.info('服务运行中，按 Ctrl+C 可同时停止前后端')
        while all(child.poll() is None for child in children):
            time.sleep(.5)
        raise RuntimeError('A service exited; stopping the remaining service.')
    except KeyboardInterrupt:
        logger.info('收到停止指令，正在关闭本项目的前后端进程')
        return 0
    finally:
        for child in reversed(children):
            logger.info('正在检查并关闭子进程：进程ID=%d', child.pid)
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.getLogger(__name__).exception('程序启动失败：异常类型=%s', type(exc).__name__)
        print('程序启动失败：' + str(exc), file=sys.stderr)
        raise SystemExit(1)
