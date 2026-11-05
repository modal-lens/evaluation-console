"""七个指标评测模块的 FastAPI 入口。"""

from __future__ import annotations

import traceback
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apis.evaluation_api import router as evaluation_router
from apis.evaluation_runs_api import router as evaluation_runs_router
from apis.sql_evaluation_api import router as sql_evaluation_router
from apis.mcp_console_api import router as mcp_console_router
from conf.load_config import get_section
from utils.exceptions import AppError
from utils.logger import get_logger
from utils.log_context import log_context


logger = get_logger(__name__)


def create_app() -> FastAPI:
    """仅注册直接评分、自动评测及健康检查接口。"""

    app = FastAPI(title="指标评测 API", version="1.0.0")
    app.include_router(evaluation_router)
    app.include_router(evaluation_runs_router)
    app.include_router(sql_evaluation_router)
    app.include_router(mcp_console_router)
    logger.info('评测服务初始化完成：直接评分、自动预测评测、SQL评测及健康检查接口已注册')

    @app.middleware('http')
    async def request_logging(request: Request, call_next):
        identifier = uuid4().hex
        request.state.request_id = identifier
        started = perf_counter()
        quiet = request.url.path in ('/health', '/ready') or (request.method == 'GET' and '/jobs' in request.url.path)
        level = logging.DEBUG if quiet else logging.INFO
        with log_context(request=identifier):
            logger.log(level, '接口请求开始：方法=%s，路径=%s', request.method, request.url.path)
            try:
                response = await call_next(request)
            except Exception as exc:
                logger.error('接口请求异常：方法=%s，路径=%s，异常类型=%s，耗时=%.1f毫秒', request.method, request.url.path, type(exc).__name__, (perf_counter() - started) * 1000)
                raise
            response.headers['X-Request-ID'] = identifier
            logger.log(logging.WARNING if response.status_code >= 400 else level,
                       '接口请求结束：方法=%s，路径=%s，状态码=%d，耗时=%.1f毫秒', request.method, request.url.path, response.status_code, (perf_counter() - started) * 1000)
            return response

    @app.get("/health")
    def health() -> dict[str, str]:
        """健康检查接口。

        该接口不连接数据库、MQ 或 LLM，只用于确认 HTTP 进程已启动并能正常响应。
        """

        return {"status": "ok"}

    @app.get("/ready")
    def ready() -> dict[str, object]:
        """检查启动所需配置是否完整，不主动调用生产依赖。"""

        checks = {
            "server": bool(get_section("server").get("host")),
        }
        return {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks}

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        """把可预期业务异常转换为统一 JSON 响应。

        例如参数错误返回 400，外部依赖失败返回 502。日志中记录路径和错误信息，
        响应体保持 `success/data/error` 结构。
        """

        logger.warning("已处理业务异常，路径=%s，错误=%s", request.url.path, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": exc.error_code,
                    "message": exc.message,
                    "detail": exc.detail,
                    "retryable": exc.retryable,
                    "stage": exc.stage,
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """处理未预期异常，并在 error 中返回简化信息和堆栈信息。"""

        logger.exception("未预期系统异常，路径=%s", request.url.path, exc_info=exc, extra={'request_id': getattr(request.state, 'request_id', '-')})
        return JSONResponse(
            status_code=500,
            headers={'X-Request-ID': getattr(request.state, 'request_id', '-')},
            content={
                "success": False,
                "data": None,
                "error": {
                    "message": str(exc),
                    "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                },
            },
        )

    return app


# uvicorn 默认导入的 ASGI 应用对象。
app = create_app()


if __name__ == "__main__":
    import uvicorn

    server_config = get_section("server")
    uvicorn.run(
        "fastapi_worker:app",
        host=server_config.get("host", "0.0.0.0"),
        port=int(server_config.get("port", 8000)),
        reload=bool(server_config.get("reload", False)),
        access_log=False,
    )
