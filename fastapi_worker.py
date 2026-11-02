"""七个指标评测模块的 FastAPI 入口。"""

from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apis.evaluation_api import router as evaluation_router
from apis.evaluation_runs_api import router as evaluation_runs_router
from conf.load_config import get_section
from utils.exceptions import AppError
from utils.logger import get_logger


logger = get_logger(__name__)


def create_app() -> FastAPI:
    """仅注册直接评分、自动评测及健康检查接口。"""

    app = FastAPI(title="指标评测 API", version="1.0.0")
    app.include_router(evaluation_router)
    app.include_router(evaluation_runs_router)

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

        logger.exception("未预期系统异常，路径=%s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
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
    )
