"""PostgreSQL 连接池封装。

仓储层不直接创建数据库连接，而是通过本模块获取游标。这样可以统一管理连接池、
事务提交、异常回滚和连接归还，避免每个 repository 重复处理底层细节。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Iterator

# 直接执行 ``python utils/database.py`` 时，Python 默认只把 utils 目录加入
# sys.path，项目根目录中的 conf、repositories 等包无法被导入。补充项目根目录
# 后，直接执行和 ``python -m utils.database`` 都可以正常工作。
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool

from conf.load_config import get_section
from utils.exceptions import ExternalServiceError


class PostgresClient:
    """进程内懒加载 PostgreSQL 客户端。

    连接池只在第一次真正访问数据库时创建，应用启动时不会强制连接数据库。
    这样 `/health`、OpenAPI 文档等轻量接口不依赖数据库立即可用。
    """

    def __init__(self) -> None:
        self._pool: SimpleConnectionPool | None = None

    def _create_pool(self) -> SimpleConnectionPool:
        """根据 `conf/config.yml` 创建 psycopg2 连接池。"""

        config = get_section("PostgreSQL")
        try:
            return SimpleConnectionPool(
                minconn=int(config.get("min_connections", 1)),
                maxconn=int(config.get("max_connections", 5)),
                host=config["url"],
                port=int(config.get("port", 5432)),
                dbname=config["db_name"],
                user=config["username"],
                password=config["password"],
                cursor_factory=RealDictCursor,
            )
        except Exception as exc:
            raise ExternalServiceError(f"创建 PostgreSQL 连接池失败\n{traceback.format_exc()}") from exc

    @property
    def pool(self) -> SimpleConnectionPool:
        """返回可用连接池；如果尚未创建，则先创建再返回。"""

        if self._pool is None:
            self._pool = self._create_pool()
        return self._pool

    @contextmanager
    def cursor(self) -> Iterator[RealDictCursor]:
        """提供带事务管理的数据库游标。

        正常执行时自动提交事务；发生异常时自动回滚；最后无论成功失败都会把
        连接归还给连接池，避免连接泄漏。
        """

        connection = self.pool.getconn()
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
        except Exception:
            print(traceback.format_exc())
            connection.rollback()
            raise
        finally:
            self.pool.putconn(connection)

    def close(self) -> None:
        """关闭连接池中所有连接，通常用于进程退出或测试清理。"""

        if self._pool is not None:
            self._pool.closeall()
            self._pool = None










    def tags_query(self, sql: str | None = None) -> dict[str, str]:
        """查询标签并返回 ``{"标签ID": "标签名称"}`` 映射。"""

        query = sql or """
            SELECT JSON_AGG(
                JSON_BUILD_OBJECT(
                    'id', id,
                    'tag', tag
                )
                ORDER BY id
            ) AS tag_list
            FROM biz_tag;
            """
        with self.cursor() as cursor:
            cursor.execute(query)
            row = cursor.fetchone()

        tag_list = (row or {}).get("tag_list") or []
        return {str(item["id"]): str(item["tag"]) for item in tag_list}


    def llm_model_config_query(self, config_id: int | str) -> dict[str, Any] | None:
        """按 ID 查询 ``public.biz_llm_model_config`` 模型配置。

        参数：
            config_id: ``biz_llm_model_config.id`` 的值。

        返回：
            对应的模型配置记录；ID 为空或没有匹配记录时返回 ``None``。
        """

        if config_id is None:
            return None
        if isinstance(config_id, str) and not config_id.strip():
            return None

        query = """
            SELECT *
            FROM public.biz_llm_model_config
            WHERE id = %s
            LIMIT 1;
            """
        with self.cursor() as cursor:
            cursor.execute(query, (config_id,))
            row = cursor.fetchone()

        return dict(row) if row else None


# 全局单例供 repository 复用，避免每个仓储对象重复创建连接池。
postgres_client = PostgresClient()
