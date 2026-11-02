"""项目统一异常定义。

API 层通过这些异常类型把业务错误、参数错误、外部依赖错误转换成稳定的
HTTP JSON 响应；MQ worker 也可以根据异常类型决定是否记录、重试或拒绝消息。
"""

from __future__ import annotations


class AppError(RuntimeError):
    """所有可预期业务异常的基类。

    子类通过 `status_code` 和 `error_code` 声明对外响应语义，构造函数保存
    可读错误信息和可选调试细节。未继承该类的异常会被视为非预期系统错误。
    """

    status_code = 500
    error_code = "internal_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        detail: object | None = None,
        stage: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.stage = stage
        if retryable is not None:
            self.retryable = retryable

    def to_error(self) -> dict[str, object]:
        """转换为不包含内部堆栈的稳定错误结构。"""

        error: dict[str, object] = {
            "code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.stage:
            error["stage"] = self.stage
        if self.detail is not None:
            error["detail"] = self.detail
        return error


class ValidationAppError(AppError):
    """请求内容不满足业务要求时使用。

    典型场景包括 `text` 和 `file_path` 同时为空、文件路径不存在、上游消息缺少
    必需字段等。它会被 API 转换成 HTTP 400。
    """

    status_code = 400
    error_code = "validation_error"


class ExternalServiceError(AppError):
    """外部依赖调用失败时使用。

    典型场景包括 PostgreSQL 连接失败、RabbitMQ 连接失败、远程文本下载失败、
    LLM 接口超时或返回非 2xx。它会被 API 转换成 HTTP 502。
    """

    status_code = 502
    error_code = "external_service_error"
    retryable = True


class NotFoundAppError(AppError):
    """业务数据不存在时使用。

    当前代码主要为后续扩展预留，例如指定模板、标签或文件记录不存在时可以
    抛出该异常，并由 API 转换成 HTTP 404。
    """

    status_code = 404
    error_code = "not_found"


class MessageValidationError(ValidationAppError):
    """MQ 消息不符合后端—算法交互协议。"""

    error_code = "MESSAGE_INVALID"


class FileReadError(AppError):
    """文件或远程正文读取失败。"""

    status_code = 400
    error_code = "FILE_READ_FAILED"


class TagConfigError(AppError):
    """标签不存在或数据库标签配置非法。"""

    status_code = 422
    error_code = "TAG_CONFIG_INVALID"


class TemplateConfigError(AppError):
    """抽取模板缺失或结构不完整。"""

    status_code = 422
    error_code = "TEMPLATE_CONFIG_INVALID"


class LLMServiceError(ExternalServiceError):
    """OpenAI 兼容模型调用失败。"""

    error_code = "LLM_SERVICE_ERROR"


class LLMResponseError(ExternalServiceError):
    """模型结果无法通过 JSON 或业务白名单校验。"""

    error_code = "LLM_RESPONSE_INVALID"


class ResultPublishError(ExternalServiceError):
    """MQ 结果回传失败。"""

    error_code = "RESULT_PUBLISH_FAILED"
