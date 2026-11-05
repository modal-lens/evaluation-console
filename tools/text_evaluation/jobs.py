"""有界后台任务与 SQLite 状态记录；不改变原 MQ 或业务任务表。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from threading import BoundedSemaphore, Event, Lock
from uuid import uuid4
from contextvars import copy_context
from time import perf_counter

from utils.exceptions import NotFoundAppError, ValidationAppError
from utils.log_context import log_context
from .schemas import RunInput
from .fallback import run_evaluation_with_fallback, ModelConnectionError

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self, db_path, *, runner=run_evaluation_with_fallback, workers=2, capacity=6):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='evaluation')
        self.slots = BoundedSemaphore(capacity)
        self.events = {}
        self.lock = Lock()
        self.db_lock = Lock()
        with self._db() as db:
            # 单进程执行器串行化短事务，避免 Windows 上频繁开关 WAL 连接的文件竞态。
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('CREATE TABLE IF NOT EXISTS evaluation_jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL, done INTEGER NOT NULL, total INTEGER NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, input TEXT NOT NULL, result TEXT, error TEXT)')
            # 此独立本地任务执行器要求单进程启动。重启时将中断任务明确标为失败，不伪造恢复。
            db.execute("UPDATE evaluation_jobs SET status='failed', stage='服务重启，任务已中断', error=?, updated=? WHERE status IN ('queued','running')", (json.dumps({'message': '服务重启导致任务中断，请重新提交'}, ensure_ascii=False), now()))
        logger.info('自动评测执行器初始化完成：任务线程数=%d，运行与排队总容量=%d', workers, capacity)

    @contextmanager
    def _db(self):
        with self.db_lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def submit(self, request: RunInput):
        if not self.slots.acquire(blocking=False):
            logger.warning('自动评测任务入队失败：队列容量已满，指标=%s', request.metric)
            raise ValidationAppError('评测任务已满（最多 2 个运行、4 个排队），请等待后重试')
        job_id, event = uuid4().hex, Event()
        try:
            with self._db() as db:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                db.execute("DELETE FROM evaluation_jobs WHERE updated < ? AND status IN ('succeeded','failed','cancelled')", (cutoff,))
                if db.execute('SELECT COUNT(*) FROM evaluation_jobs').fetchone()[0] >= 200:
                    raise ValidationAppError('历史任务数量已达到 200，请清理已完成的任务记录后再试')
                stamp = now()
                db.execute('INSERT INTO evaluation_jobs VALUES (?,?,?,?,?,?,?,?,?,?)', (job_id, 'queued', '等待执行', 0, 1, stamp, stamp, request.model_dump_json(), None, None))
            with self.lock:
                self.events[job_id] = event
            logger.info('自动评测任务已入队：指标=%s，输入模式=%s，文档数=%d，原文字符数=%d，预测并发=%d', request.metric, '已有预测JSON' if request.predictions is not None else '原文预测', len(request.documents), sum(len(d.text) for d in request.documents), request.options.max_workers, extra={'job_id': job_id})
            self.pool.submit(copy_context().run, self._execute, job_id, request.model_copy(deep=True), event)
        except BaseException:
            with self.lock:
                self.events.pop(job_id, None)
            self.slots.release()
            raise
        return self.get(job_id)

    def get(self, job_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM evaluation_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise NotFoundAppError('评测任务不存在或已过期')
        return {'job_id': row['id'], 'metric': json.loads(row['input'])['metric'], 'status': row['status'], 'stage': row['stage'], 'completed': row['done'], 'total': row['total'], 'progress': round(100 * row['done'] / max(row['total'], 1), 1), 'created_at': row['created'], 'updated_at': row['updated'], 'result': json.loads(row['result']) if row['result'] else None, 'error': json.loads(row['error']) if row['error'] else None}

    def cancel(self, job_id):
        logger.info('收到自动评测取消请求：已发送的模型请求将等待返回后停止', extra={'job_id': job_id})
        self.get(job_id)
        with self.lock:
            event = self.events.get(job_id)
            if event:
                event.set()
        with self._db() as db:
            db.execute("UPDATE evaluation_jobs SET status='cancelled', stage='已取消；正在进行的模型调用会在返回后停止', updated=? WHERE id=? AND status IN ('queued','running')", (now(), job_id))
        return self.get(job_id)

    def delete(self, job_id):
        job = self.get(job_id)
        if job['status'] in ('queued', 'running'):
            raise ValidationAppError('请先取消任务，再清理任务记录')
        with self._db() as db:
            db.execute('DELETE FROM evaluation_jobs WHERE id=?', (job_id,))
        logger.info('自动评测任务数据已清理', extra={'job_id': job_id})
        return {'job_id': job_id, 'deleted': True}

    def _execute(self, job_id, request, event):
        with log_context(job=job_id):
            return self._execute_job(job_id, request, event)

    def _execute_job(self, job_id, request, event):
        started = perf_counter()
        def checkpoint():
            if event.is_set():
                raise Cancelled()
        def progress(stage, done, total):
            checkpoint()
            logger.info('自动评测阶段进度：指标=%s，阶段=%s，已完成=%d，总步骤=%d', request.metric, stage, done, total)
            with self._db() as db:
                db.execute("UPDATE evaluation_jobs SET stage=?, done=?, total=?, updated=? WHERE id=? AND status='running'", (stage, done, total, now(), job_id))
        try:
            checkpoint()
            logger.info('自动评测任务开始：指标=%s，预测并发=%d', request.metric, request.options.max_workers)
            with self._db() as db:
                db.execute("UPDATE evaluation_jobs SET status='running', stage='开始预测', updated=? WHERE id=? AND status='queued'", (now(), job_id))
            result = self.runner(request, checkpoint=checkpoint, progress=progress)
            checkpoint()
            with self._db() as db:
                db.execute("UPDATE evaluation_jobs SET status='succeeded', stage='评测完成', done=total, result=?, updated=? WHERE id=? AND status='running'", (json.dumps(result, ensure_ascii=False), now(), job_id))
            logger.info('自动评测任务完成：指标=%s，总耗时=%.3f秒', request.metric, perf_counter() - started)
        except Cancelled:
            logger.info('自动评测任务已停止：指标=%s，总耗时=%.3f秒', request.metric, perf_counter() - started)
        except Exception as exc:
            logger.exception('自动评测任务失败：指标=%s，异常类型=%s，总耗时=%.3f秒', request.metric, type(exc).__name__, perf_counter() - started)
            # 不将旧模型客户端包含地址/堆栈的异常直接公开给前端。
            text = str(exc) if isinstance(exc, (ValidationAppError, ModelConnectionError)) else f'预测或评测失败（{type(exc).__name__}），请检查算法依赖及后端日志。'
            error = {'message': text}
            if isinstance(exc, ModelConnectionError):
                error.update(code=exc.error_code, retryable=True, stage='model_connection')
            with self._db() as db:
                db.execute("UPDATE evaluation_jobs SET status='failed', stage='执行失败', error=?, updated=? WHERE id=? AND status IN ('queued','running')", (json.dumps(error, ensure_ascii=False), now(), job_id))
        finally:
            with self.lock:
                self.events.pop(job_id, None)
            self.slots.release()

    def close(self):
        logger.info('正在关闭自动评测执行器，等待运行中的任务退出')
        self.pool.shutdown(wait=True)
        logger.info('自动评测执行器已关闭')
