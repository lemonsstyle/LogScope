from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import mimetypes
import re
import secrets
import sys
import threading
import time as time_module
import webbrowser
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


APP_NAME = "LogScope"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_QUERY_TIMEOUT_SECONDS = 300
BROWSER_OPEN_DELAY_SECONDS = 1.0
MAX_LIMIT = 1000
MAX_SQL_ROWS = 1000
MAX_JSON_BODY_BYTES = 1_000_000
DEFAULT_LIMIT = 200
ALLOWED_SORT_ORDERS = {"asc", "desc"}
TIME_COLUMN_HINTS = {
    "time",
    "send_time",
    "create_time",
    "created_at",
    "updated_at",
    "timestamp",
    "log_time",
    "event_time",
    "occurred_at",
    "datetime",
}
TEXT_SEARCH_TYPES = {
    "char",
    "character",
    "character varying",
    "nchar",
    "ntext",
    "nvarchar",
    "varchar",
    "varchar2",
    "nvarchar2",
    "tinytext",
    "text",
    "mediumtext",
    "longtext",
    "clob",
    "nclob",
    "enum",
    "set",
    "uuid",
    "uniqueidentifier",
    "json",
    "jsonb",
    "xml",
}
DANGEROUS_SQL_PATTERN = re.compile(
    r"\binto\s+(?:out|dump)file\b"
    r"|\b(?:load_file|sleep|pg_sleep|benchmark|get_lock|release_lock|is_free_lock|is_used_lock)\s*\("
    r"|\b(?:pg_read_file|pg_ls_dir|pg_stat_file|openrowset|opendatasource)\s*\("
    r"|\b(?:xp_cmdshell|utl_file|dbms_scheduler|dbms_java)\b"
    r"|\b(?:bulk\s+insert|load\s+data)\b"
    r"|\bcopy\b[\s\S]*\b(?:from|to)\s+(?:program\b|')"
    r"|\bwaitfor\b",
    re.IGNORECASE,
)
SQL_STATEMENT_TYPE_PATTERN = re.compile(r"^\s*([A-Za-z]+)")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
DATABASE_TYPES = {"auto", "mysql", "postgresql", "sqlserver", "oracle", "xugu"}
DEFAULT_DATABASE_PORTS = {
    "mysql": 3306,
    "postgresql": 5432,
    "sqlserver": 1433,
    "oracle": 1521,
    "xugu": 5138,
}
AUTO_PROBE_ORDER = ("mysql", "postgresql", "sqlserver", "oracle", "xugu")
DATABASE_TYPE_LABELS = {
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "sqlserver": "SQL Server",
    "oracle": "Oracle",
    "xugu": "虚谷数据库",
}


def _load_mysql_driver():
    for module_name in ("pymysql", "mysql.connector"):
        try:
            module = __import__(module_name, fromlist=["*"])
            return module_name, module
        except ImportError:
            continue
    return None, None


MYSQL_DRIVER_NAME, MYSQL_DRIVER = _load_mysql_driver()


def _load_postgresql_driver():
    try:
        module = __import__("psycopg", fromlist=["*"])
        rows_module = __import__("psycopg.rows", fromlist=["dict_row"])
        return "psycopg", module, rows_module.dict_row
    except ImportError:
        return None, None, None


def _load_sqlserver_driver():
    try:
        module = __import__("pymssql", fromlist=["*"])
        return "pymssql", module
    except ImportError:
        return None, None


def _load_oracle_driver():
    try:
        module = __import__("oracledb", fromlist=["*"])
        return "oracledb", module
    except ImportError:
        return None, None


def _load_xugu_driver():
    try:
        module = __import__("xgcondb", fromlist=["*"])
        return "xgcondb", module
    except ImportError:
        return None, None


POSTGRES_DRIVER_NAME, POSTGRES_DRIVER, POSTGRES_DICT_ROW = _load_postgresql_driver()
SQLSERVER_DRIVER_NAME, SQLSERVER_DRIVER = _load_sqlserver_driver()
ORACLE_DRIVER_NAME, ORACLE_DRIVER = _load_oracle_driver()
XUGU_DRIVER_NAME, XUGU_DRIVER = _load_xugu_driver()


class AppError(Exception):
    def __init__(self, message: str, status: int = 400, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


def slugify(value: str) -> str:
    trimmed = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return trimmed or f"conn-{secrets.token_hex(4)}"


def ensure_identifier(name: str, label: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise AppError(f"{label} 包含不允许的字符。", 400)
    return name


def normalize_database_type(value: str | None, default: str = "auto") -> str:
    database_type = (value or default).strip().lower()
    if database_type not in DATABASE_TYPES:
        raise AppError("数据库类型不支持。", 400)
    return database_type


def quote_identifier(name: str) -> str:
    ensure_identifier(name, "标识符")
    return f"`{name}`"


def normalize_limit(value: Any) -> int:
    try:
        limit = int(value or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise AppError("结果条数必须是整数。", 400)
    if limit < 1:
        raise AppError("结果条数必须大于 0。", 400)
    return min(limit, MAX_LIMIT)


def is_time_like(column_name: str, data_type: str) -> bool:
    normalized_type = data_type.lower()
    return (
        normalized_type in {"datetime", "datetime2", "smalldatetime", "datetimeoffset", "timestamp", "date", "time"}
        or normalized_type.startswith("timestamp")
        or column_name.lower() in TIME_COLUMN_HINTS
    )


def is_text_searchable(data_type: str) -> bool:
    return data_type.lower() in TEXT_SEARCH_TYPES


def parse_sql_statement(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        raise AppError("SQL 不能为空。", 400)
    if ";" in stripped.rstrip(";"):
        raise AppError("只允许执行单条 SQL 语句。", 400)
    if DANGEROUS_SQL_PATTERN.search(stripped):
        raise AppError("SQL 包含不允许的服务器文件、系统命令或阻塞表达式。", 400)
    return stripped.rstrip(";")


def sql_statement_type(sql: str) -> str:
    match = SQL_STATEMENT_TYPE_PATTERN.match(sql)
    return match.group(1).upper() if match else "SQL"


def escape_like_literal(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def normalize_keyword_terms(terms: list[str] | None, fallback_keyword: str | None = None) -> list[str]:
    values = terms[:] if terms else []
    if fallback_keyword:
        values.append(fallback_keyword)
    normalized: list[str] = []
    for value in values:
        text = value.strip()
        if text:
            normalized.append(text)
    return normalized


def normalize_keyword_field_terms(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column") or "").strip()
        terms = normalize_keyword_terms(item.get("terms") if isinstance(item.get("terms"), list) else None)
        if column and terms:
            normalized.append({"column": column, "terms": terms})
    return normalized


def _parse_datetime_candidate(value: str) -> tuple[datetime, str] | None:
    text = value.strip()
    candidates = [
        ("%Y%m%d", "day"),
        ("%Y-%m-%d", "day"),
    ]
    for fmt, precision in candidates:
        try:
            return datetime.strptime(text, fmt), precision
        except ValueError:
            continue
    return None


def normalize_time_input(value: str | None, boundary: str) -> str | None:
    if not value:
        return None
    parsed = _parse_datetime_candidate(value)
    if parsed is None:
        raise AppError("时间格式不正确。支持 20260606。", 400)
    dt, precision = parsed
    if precision == "day":
        dt = dt.replace(hour=0, minute=0, second=0) if boundary == "start" else dt.replace(hour=23, minute=59, second=59)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def normalize_time_point(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    parsed = _parse_datetime_candidate(value)
    if parsed is None:
        raise AppError("时间点格式不正确。支持 20260606。", 400)
    dt, precision = parsed
    start = dt
    end = dt
    if precision == "day":
        start = dt.replace(hour=0, minute=0, second=0)
        end = dt.replace(hour=23, minute=59, second=59)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def close_quietly(resource: Any) -> None:
    try:
        resource.close()
    except Exception:
        pass


def redact_sensitive_text(text: str, *secret_values: str | None) -> str:
    redacted = text
    for secret_value in secret_values:
        if secret_value:
            redacted = redacted.replace(secret_value, "******")
    return redacted


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


@dataclass
class ConnectionProfile:
    id: str
    name: str
    host: str
    port: int
    username: str
    database: str
    charset: str = "utf8mb4"
    database_type: str = "mysql"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "database": self.database,
            "database_type": self.database_type,
            "charset": self.charset,
            "password_persisted": False,
        }


class ConnectionStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "connections.json"
        if not self.file_path.exists():
            self.file_path.write_text("[]", encoding="utf-8")

    def list_connections(self) -> list[ConnectionProfile]:
        try:
            raw_items = json.loads(self.file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AppError("本地连接配置文件已损坏。", 500, str(exc))
        items: list[ConnectionProfile] = []
        for raw_item in raw_items:
            item = dict(raw_item)
            item["database_type"] = normalize_database_type(item.get("database_type"), default="mysql")
            items.append(ConnectionProfile(**item))
        return items

    def get(self, connection_id: str) -> ConnectionProfile:
        for item in self.list_connections():
            if item.id == connection_id:
                return item
        raise AppError("连接不存在。", 404)

    def save(self, profile: ConnectionProfile) -> ConnectionProfile:
        items = self.list_connections()
        for index, item in enumerate(items):
            if item.id == profile.id:
                items[index] = profile
                self._write(items)
                return profile
        items.append(profile)
        self._write(items)
        return profile

    def delete(self, connection_id: str) -> None:
        items = self.list_connections()
        remaining = [item for item in items if item.id != connection_id]
        if len(remaining) == len(items):
            raise AppError("连接不存在。", 404)
        self._write(remaining)

    def _write(self, items: list[ConnectionProfile]) -> None:
        payload = [item.__dict__ for item in items]
        self.file_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class DatabaseGateway:
    database_type = ""
    display_name = ""
    text_cast_type = "CHAR"

    def __init__(self, query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS) -> None:
        self.query_timeout_seconds = query_timeout_seconds

    def _ensure_driver(self) -> None:
        raise NotImplementedError

    def connect(self, profile: ConnectionProfile, password: str | None):
        raise NotImplementedError

    def _cursor(self, conn, unbuffered: bool = False):
        return conn.cursor()

    def bind_placeholder(self, position: int) -> str:
        return "%s"

    def quote_identifier(self, name: str) -> str:
        ensure_identifier(name, "标识符")
        return f'"{name}"'

    def table_reference(self, schema: str, table: str) -> str:
        ensure_identifier(schema, "数据库/Schema 名")
        ensure_identifier(table, "表名")
        return f"{self.quote_identifier(schema)}.{self.quote_identifier(table)}"

    def text_cast(self, expression: str) -> str:
        return f"CAST({expression} AS {self.text_cast_type})"

    def limit_clause(self, parameter_index: int = 1) -> str:
        return f" LIMIT {self.bind_placeholder(parameter_index)}"

    def apply_limit(self, sql: str, params: tuple[Any, ...], limit: int) -> tuple[str, tuple[Any, ...]]:
        parameter_index = len(params) + 1
        return sql + self.limit_clause(parameter_index), (*params, limit)

    def current_database_query(self) -> str:
        raise NotImplementedError

    def list_schemas_query(self) -> tuple[str, tuple[Any, ...]]:
        return (
            """
            SELECT schema_name
            FROM information_schema.schemata
            ORDER BY schema_name
            """,
            (),
        )

    def list_tables_query(self, schema: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "数据库/Schema 名")
        schema_placeholder = self.bind_placeholder(1)
        return (
            f"""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = {schema_placeholder}
            ORDER BY table_name
            """,
            (schema,),
        )

    def list_columns_query(self, schema: str, table: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "数据库/Schema 名")
        ensure_identifier(table, "表名")
        schema_placeholder = self.bind_placeholder(1)
        table_placeholder = self.bind_placeholder(2)
        return (
            f"""
            SELECT column_name, data_type, data_type AS column_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = {schema_placeholder} AND table_name = {table_placeholder}
            ORDER BY ordinal_position
            """,
            (schema, table),
        )

    def test_connection(self, profile: ConnectionProfile, password: str | None) -> dict[str, Any]:
        try:
            conn = self.connect(profile, password)
            cursor = None
            try:
                cursor = self._cursor(conn)
                cursor.execute(self.current_database_query())
                raw_row = cursor.fetchone()
                row = self._row_to_dict(raw_row, cursor.description) if raw_row is not None else {}
                return {
                    "ok": True,
                    "database": self._row_value(row, "current_database"),
                    "version": self._row_value(row, "version"),
                }
            finally:
                if cursor:
                    close_quietly(cursor)
                close_quietly(conn)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("连接失败。", 400, redact_sensitive_text(str(exc), password))

    def list_schemas(self, profile: ConnectionProfile, password: str | None) -> list[str]:
        sql, params = self.list_schemas_query()
        rows, _ = self._fetch_all(profile, password, sql, params)
        return [str(value) for row in rows if (value := self._row_value(row, "schema_name")) is not None]

    def list_tables(self, profile: ConnectionProfile, password: str | None, schema: str) -> list[dict[str, Any]]:
        sql, params = self.list_tables_query(schema)
        rows, _ = self._fetch_all(profile, password, sql, params)
        return [
            {
                "table_name": self._row_value(row, "table_name"),
                "table_type": self._row_value(row, "table_type"),
            }
            for row in rows
            if self._row_value(row, "table_name") is not None
        ]

    def list_columns(self, profile: ConnectionProfile, password: str | None, schema: str, table: str) -> list[dict[str, Any]]:
        sql, params = self.list_columns_query(schema, table)
        rows, _ = self._fetch_all(profile, password, sql, params)
        columns: list[dict[str, Any]] = []
        for row in rows:
            column_name = self._row_value(row, "column_name")
            if column_name is None:
                continue
            data_type = self._row_value(row, "data_type") or ""
            column_type = self._row_value(row, "column_type") or data_type
            normalized_row = {
                "column_name": str(column_name),
                "data_type": str(data_type),
                "column_type": str(column_type),
                "is_nullable": self._row_value(row, "is_nullable"),
            }
            normalized_row["is_time_like"] = is_time_like(normalized_row["column_name"], normalized_row["data_type"])
            normalized_row["is_text_searchable"] = is_text_searchable(normalized_row["data_type"])
            columns.append(normalized_row)
        return columns

    def search_rows(
        self,
        profile: ConnectionProfile,
        password: str | None,
        schema: str,
        table: str,
        keyword_terms: list[str] | None,
        keyword_columns: list[str] | None,
        keyword_field_terms: list[dict[str, Any]] | None,
        time_column: str | None,
        time_mode: str | None,
        time_point: str | None,
        time_from: str | None,
        time_to: str | None,
        limit: int,
        sort_by: str | None,
        sort_order: str,
        visible_columns: list[str] | None,
    ) -> dict[str, Any]:
        columns = self.list_columns(profile, password, schema, table)
        column_map = {row["column_name"]: row for row in columns}
        column_names = list(column_map.keys())
        if not column_names:
            raise AppError("表中没有可查询的列。", 400)

        selected_columns = visible_columns or column_names
        unknown_columns = [name for name in selected_columns if name not in column_map]
        if unknown_columns:
            raise AppError(f"字段不存在: {', '.join(unknown_columns)}", 400)

        where_parts: list[str] = []
        params: list[Any] = []

        if time_column:
            if time_column not in column_map:
                raise AppError("时间列不存在。", 400)
            normalized_from: str | None
            normalized_to: str | None
            if (time_mode or "range") == "point":
                normalized_from, normalized_to = normalize_time_point(time_point)
            else:
                normalized_from = normalize_time_input(time_from, "start")
                normalized_to = normalize_time_input(time_to, "end")

            if normalized_from:
                where_parts.append(f"{self.quote_identifier(time_column)} >= {self.bind_placeholder(len(params) + 1)}")
                params.append(normalized_from)
            if normalized_to:
                where_parts.append(f"{self.quote_identifier(time_column)} <= {self.bind_placeholder(len(params) + 1)}")
                params.append(normalized_to)

        normalized_keyword_field_terms = keyword_field_terms or []
        if normalized_keyword_field_terms:
            invalid_keyword_columns = [item["column"] for item in normalized_keyword_field_terms if item["column"] not in column_map]
            if invalid_keyword_columns:
                raise AppError(f"关键词字段不存在: {', '.join(invalid_keyword_columns)}", 400)

            for item in normalized_keyword_field_terms:
                metadata = column_map[item["column"]]
                expression = self.quote_identifier(item["column"])
                if not metadata["is_text_searchable"]:
                    expression = self.text_cast(expression)
                like_parts: list[str] = []
                for term in item["terms"]:
                    escaped_term = escape_like_literal(term)
                    like_parts.append(f"{expression} LIKE {self.bind_placeholder(len(params) + 1)} ESCAPE '!'")
                    params.append(f"%{escaped_term}%")
                where_parts.append("(" + " OR ".join(like_parts) + ")")

        normalized_terms = normalize_keyword_terms(keyword_terms)
        if normalized_terms and not normalized_keyword_field_terms:
            candidate_keyword_columns = keyword_columns or [
                row["column_name"] for row in columns if row["is_text_searchable"]
            ]
            if not candidate_keyword_columns:
                raise AppError("当前表没有适合做关键词搜索的文本字段。", 400)
            invalid_keyword_columns = [name for name in candidate_keyword_columns if name not in column_map]
            if invalid_keyword_columns:
                raise AppError(f"关键词字段不存在: {', '.join(invalid_keyword_columns)}", 400)

            for term in normalized_terms:
                escaped_term = escape_like_literal(term)
                like_parts: list[str] = []
                for name in candidate_keyword_columns:
                    metadata = column_map[name]
                    expression = self.quote_identifier(name)
                    if not metadata["is_text_searchable"]:
                        expression = self.text_cast(expression)
                    like_parts.append(f"{expression} LIKE {self.bind_placeholder(len(params) + 1)} ESCAPE '!'")
                    params.append(f"%{escaped_term}%")
                where_parts.append("(" + " OR ".join(like_parts) + ")")

        order_column = sort_by if sort_by in column_map else None
        if not order_column:
            order_column = next((row["column_name"] for row in columns if row["is_time_like"]), column_names[0])

        direction = (sort_order or "desc").lower()
        if direction not in ALLOWED_SORT_ORDERS:
            raise AppError("排序方向不正确。", 400)

        select_clause = ", ".join(self.quote_identifier(name) for name in selected_columns)
        sql = f"SELECT {select_clause} FROM {self.table_reference(schema, table)}"
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += f" ORDER BY {self.quote_identifier(order_column)} {'DESC' if direction == 'desc' else 'ASC'}"
        sql, query_params = self.apply_limit(sql, tuple(params), limit)
        rows, elapsed_time = self._fetch_all(profile, password, sql, query_params)
        return {
            "columns": selected_columns,
            "rows": rows,
            "applied_sort": {"column": order_column, "order": direction},
            "limit": limit,
            "keyword_terms": normalized_terms,
            "keyword_columns": keyword_columns or [],
            "keyword_field_terms": normalized_keyword_field_terms,
            "elapsed_time": elapsed_time,
        }

    def execute_sql(self, profile: ConnectionProfile, password: str | None, sql: str) -> dict[str, Any]:
        statement = parse_sql_statement(sql)
        return self._execute_sql_statement(profile, password, statement)

    def _description_names(self, description: Any) -> list[str]:
        names: list[str] = []
        for item in description or []:
            name = getattr(item, "name", None)
            if name is None:
                if isinstance(item, str):
                    name = item
                else:
                    name = item[0]
            names.append(str(name))
        return names

    def _row_to_dict(self, row: Any, description: Any = None) -> dict[str, Any]:
        if isinstance(row, Mapping):
            return dict(row)
        if hasattr(row, "_asdict"):
            return dict(row._asdict())
        names = self._description_names(description)
        if names:
            return dict(zip(names, row))
        return dict(row)

    def _rows_to_dicts(self, rows: Any, description: Any = None) -> list[dict[str, Any]]:
        return [self._row_to_dict(row, description) for row in rows]

    def _execute(self, cursor: Any, sql: str, params: tuple[Any, ...]) -> None:
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

    def _row_value(self, row: Mapping[str, Any], key: str) -> Any:
        if key in row:
            return row[key]
        normalized_key = key.casefold()
        for row_key, value in row.items():
            if str(row_key).casefold() == normalized_key:
                return value
        return None

    def _fetch_all(self, profile: ConnectionProfile, password: str | None, sql: str, params: tuple[Any, ...]) -> tuple[list[dict[str, Any]], float]:
        conn = None
        cursor = None
        start_time = datetime.now()
        try:
            conn = self.connect(profile, password)
            cursor = self._cursor(conn)
            self._execute(cursor, sql, params)
            rows = cursor.fetchall()
            elapsed_time = (datetime.now() - start_time).total_seconds()
            return self._rows_to_dicts(rows, cursor.description), elapsed_time
        except AppError:
            raise
        except Exception as exc:
            raise AppError("数据库查询失败。", 400, redact_sensitive_text(str(exc), password))
        finally:
            if cursor:
                close_quietly(cursor)
            if conn:
                close_quietly(conn)

    def _execute_sql_statement(
        self,
        profile: ConnectionProfile,
        password: str | None,
        sql: str,
    ) -> dict[str, Any]:
        conn = None
        cursor = None
        start_time = datetime.now()
        try:
            conn = self.connect(profile, password)
            cursor = self._cursor(conn, unbuffered=True)
            self._execute(cursor, sql, ())
            has_result_set = cursor.description is not None
            columns: list[str] = []
            rows: list[dict[str, Any]] = []
            truncated = False
            if has_result_set:
                fetched_rows = cursor.fetchmany(MAX_SQL_ROWS + 1)
                rows = self._rows_to_dicts(fetched_rows[:MAX_SQL_ROWS], cursor.description)
                columns = self._description_names(cursor.description)
                truncated = len(fetched_rows) > MAX_SQL_ROWS
            raw_rowcount = getattr(cursor, "rowcount", None)
            affected_rows = raw_rowcount if isinstance(raw_rowcount, int) and raw_rowcount >= 0 else None
            elapsed_time = (datetime.now() - start_time).total_seconds()
            return {
                "statement_type": sql_statement_type(sql),
                "has_result_set": has_result_set,
                "columns": columns,
                "rows": rows,
                "affected_rows": affected_rows,
                "elapsed_time": elapsed_time,
                "limit": MAX_SQL_ROWS,
                "truncated": truncated,
            }
        except AppError:
            raise
        except Exception as exc:
            raise AppError("SQL 执行失败。", 400, redact_sensitive_text(str(exc), password))
        finally:
            if cursor:
                close_quietly(cursor)
            if conn:
                close_quietly(conn)


class MySQLGateway(DatabaseGateway):
    database_type = "mysql"
    display_name = "MySQL"
    text_cast_type = "CHAR"

    def _ensure_driver(self) -> None:
        if MYSQL_DRIVER is None:
            raise AppError(
                "未找到 MySQL 驱动。先执行 `pip install -r requirements.txt`。",
                500,
                "支持的驱动: PyMySQL 或 mysql-connector-python。",
            )

    def quote_identifier(self, name: str) -> str:
        ensure_identifier(name, "标识符")
        return f"`{name}`"

    def current_database_query(self) -> str:
        return "SELECT DATABASE() AS current_database, VERSION() AS version"

    def list_columns_query(self, schema: str, table: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "数据库名")
        ensure_identifier(table, "表名")
        return (
            """
            SELECT column_name, data_type, column_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )

    def connect(self, profile: ConnectionProfile, password: str | None):
        self._ensure_driver()
        if not password:
            raise AppError("需要密码才能连接数据库。", 400)
        if MYSQL_DRIVER_NAME == "pymysql":
            return MYSQL_DRIVER.connect(
                host=profile.host,
                port=profile.port,
                user=profile.username,
                password=password,
                database=profile.database or None,
                charset=profile.charset,
                cursorclass=MYSQL_DRIVER.cursors.DictCursor,
                connect_timeout=5,
                read_timeout=self.query_timeout_seconds,
                write_timeout=self.query_timeout_seconds,
                autocommit=True,
            )
        if MYSQL_DRIVER_NAME == "mysql.connector":
            return MYSQL_DRIVER.connect(
                host=profile.host,
                port=profile.port,
                user=profile.username,
                password=password,
                database=profile.database or None,
                charset=profile.charset,
                connection_timeout=5,
                autocommit=True,
            )
        raise AppError("当前驱动不受支持。", 500)

    def _cursor(self, conn, unbuffered: bool = False):
        if MYSQL_DRIVER_NAME == "pymysql":
            if unbuffered:
                return conn.cursor(MYSQL_DRIVER.cursors.SSDictCursor)
            return conn.cursor()
        if MYSQL_DRIVER_NAME == "mysql.connector":
            return conn.cursor(dictionary=True, buffered=not unbuffered)
        raise AppError("当前驱动不受支持。", 500)


class PostgreSQLGateway(DatabaseGateway):
    database_type = "postgresql"
    display_name = "PostgreSQL"
    text_cast_type = "TEXT"

    def _ensure_driver(self) -> None:
        if POSTGRES_DRIVER is None:
            raise AppError(
                "未找到 PostgreSQL 驱动。先执行 `pip install -r requirements.txt`。",
                500,
                "需要 psycopg[binary]。",
            )

    def current_database_query(self) -> str:
        return "SELECT current_database() AS current_database, version() AS version"

    def list_schemas_query(self) -> tuple[str, tuple[Any, ...]]:
        return (
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE catalog_name = current_database()
            ORDER BY schema_name
            """,
            (),
        )

    def connect(self, profile: ConnectionProfile, password: str | None):
        self._ensure_driver()
        if not password:
            raise AppError("需要密码才能连接数据库。", 400)
        kwargs: dict[str, Any] = {
            "host": profile.host,
            "port": profile.port,
            "user": profile.username,
            "password": password,
            "connect_timeout": 5,
            "row_factory": POSTGRES_DICT_ROW,
            "options": f"-c statement_timeout={int(self.query_timeout_seconds * 1000)}",
        }
        if profile.database:
            kwargs["dbname"] = profile.database
        conn = POSTGRES_DRIVER.connect(**kwargs)
        conn.autocommit = True
        return conn


class SQLServerGateway(DatabaseGateway):
    database_type = "sqlserver"
    display_name = "SQL Server"
    text_cast_type = "NVARCHAR(MAX)"

    def _ensure_driver(self) -> None:
        if SQLSERVER_DRIVER is None:
            raise AppError(
                "未找到 SQL Server 驱动。先执行 `pip install -r requirements.txt`。",
                500,
                "需要 pymssql。",
            )

    def quote_identifier(self, name: str) -> str:
        ensure_identifier(name, "标识符")
        return f"[{name}]"

    def limit_clause(self, parameter_index: int = 1) -> str:
        return f" OFFSET 0 ROWS FETCH NEXT {self.bind_placeholder(parameter_index)} ROWS ONLY"

    def current_database_query(self) -> str:
        return "SELECT DB_NAME() AS current_database, @@VERSION AS version"

    def connect(self, profile: ConnectionProfile, password: str | None):
        self._ensure_driver()
        if not password:
            raise AppError("需要密码才能连接数据库。", 400)
        kwargs: dict[str, Any] = {
            "server": profile.host,
            "port": profile.port,
            "user": profile.username,
            "password": password,
            "as_dict": True,
            "login_timeout": 5,
            "timeout": self.query_timeout_seconds,
            "autocommit": True,
        }
        if profile.database:
            kwargs["database"] = profile.database
        return SQLSERVER_DRIVER.connect(**kwargs)


class OracleGateway(DatabaseGateway):
    database_type = "oracle"
    display_name = "Oracle"
    text_cast_type = "VARCHAR2(4000)"

    def _ensure_driver(self) -> None:
        if ORACLE_DRIVER is None:
            raise AppError(
                "未找到 Oracle 驱动。先执行 `pip install -r requirements.txt`。",
                500,
                "需要 python-oracledb。",
            )

    def bind_placeholder(self, position: int) -> str:
        return f":{position}"

    def apply_limit(self, sql: str, params: tuple[Any, ...], limit: int) -> tuple[str, tuple[Any, ...]]:
        placeholder = self.bind_placeholder(len(params) + 1)
        return f"SELECT * FROM ({sql}) WHERE ROWNUM <= {placeholder}", (*params, limit)

    def current_database_query(self) -> str:
        return (
            "SELECT SYS_CONTEXT('USERENV', 'DB_NAME') AS current_database, "
            "(SELECT banner FROM v$version WHERE ROWNUM = 1) AS version FROM dual"
        )

    def list_schemas_query(self) -> tuple[str, tuple[Any, ...]]:
        return (
            """
            SELECT username AS schema_name
            FROM all_users
            ORDER BY username
            """,
            (),
        )

    def list_tables_query(self, schema: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "Schema 名")
        first_schema = self.bind_placeholder(1)
        second_schema = self.bind_placeholder(2)
        return (
            f"""
            SELECT table_name, table_type
            FROM (
                SELECT table_name, 'BASE TABLE' AS table_type
                FROM all_tables
                WHERE owner = {first_schema}
                UNION ALL
                SELECT view_name AS table_name, 'VIEW' AS table_type
                FROM all_views
                WHERE owner = {second_schema}
            )
            ORDER BY table_name
            """,
            (schema.upper(), schema.upper()),
        )

    def list_columns_query(self, schema: str, table: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "Schema 名")
        ensure_identifier(table, "表名")
        owner_placeholder = self.bind_placeholder(1)
        table_placeholder = self.bind_placeholder(2)
        return (
            f"""
            SELECT column_name, data_type, data_type AS column_type, nullable AS is_nullable
            FROM all_tab_columns
            WHERE owner = {owner_placeholder} AND table_name = {table_placeholder}
            ORDER BY column_id
            """,
            (schema.upper(), table.upper()),
        )

    def connect(self, profile: ConnectionProfile, password: str | None):
        self._ensure_driver()
        if not password:
            raise AppError("需要密码才能连接数据库。", 400)
        if not profile.database:
            raise AppError("Oracle 连接需要填写服务名。", 400)
        dsn = ORACLE_DRIVER.makedsn(profile.host, profile.port, service_name=profile.database)
        conn = ORACLE_DRIVER.connect(user=profile.username, password=password, dsn=dsn)
        conn.call_timeout = int(self.query_timeout_seconds * 1000)
        conn.autocommit = True
        return conn


class XuguGateway(DatabaseGateway):
    database_type = "xugu"
    display_name = "虚谷数据库"
    text_cast_type = "VARCHAR"

    def _ensure_driver(self) -> None:
        if XUGU_DRIVER is None:
            raise AppError(
                "未找到虚谷数据库驱动。请安装厂商提供的 `xgcondb` Python 驱动。",
                500,
                "xgcondb 不在 PyPI，需要使用与当前 Python 和操作系统匹配的厂商驱动。",
            )

    def bind_placeholder(self, position: int) -> str:
        return "?"

    def apply_limit(self, sql: str, params: tuple[Any, ...], limit: int) -> tuple[str, tuple[Any, ...]]:
        return f"SELECT * FROM ({sql}) WHERE ROWNUM <= ?", (*params, limit)

    def current_database_query(self) -> str:
        return "SELECT DATABASE() AS current_database, NULL AS version FROM dual"

    def list_schemas_query(self) -> tuple[str, tuple[Any, ...]]:
        return (
            """
            SELECT schema_name
            FROM all_schemas
            WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
            ORDER BY schema_name
            """,
            (),
        )

    def list_tables_query(self, schema: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "Schema 名")
        return (
            """
            SELECT table_name,
                   CASE WHEN table_type = 0 THEN 'BASE TABLE' ELSE 'VIEW' END AS table_type
            FROM all_tables
            WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
              AND schema_id = (
                  SELECT schema_id
                  FROM all_schemas
                  WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
                    AND schema_name = ?
              )
            ORDER BY table_name
            """,
            (schema.upper(),),
        )

    def list_columns_query(self, schema: str, table: str) -> tuple[str, tuple[Any, ...]]:
        ensure_identifier(schema, "Schema 名")
        ensure_identifier(table, "表名")
        return (
            """
            SELECT col_name AS column_name,
                   type_name AS data_type,
                   type_name AS column_type,
                   CASE WHEN not_null THEN 'NO' ELSE 'YES' END AS is_nullable
            FROM all_columns
            WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
              AND table_id = (
                  SELECT table_id
                  FROM all_tables
                  WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
                    AND schema_id = (
                        SELECT schema_id
                        FROM all_schemas
                        WHERE db_id = (SELECT db_id FROM all_databases WHERE db_name = DATABASE())
                          AND schema_name = ?
                    )
                    AND table_name = ?
              )
            ORDER BY col_no
            """,
            (schema.upper(), table.upper()),
        )

    def connect(self, profile: ConnectionProfile, password: str | None):
        self._ensure_driver()
        if not password:
            raise AppError("需要密码才能连接数据库。", 400)
        if not profile.database:
            raise AppError("虚谷数据库连接需要填写数据库名。", 400)
        conn = XUGU_DRIVER.connect(
            host=profile.host,
            port=profile.port,
            database=profile.database,
            user=profile.username,
            password=password,
            charset="UTF8",
        )
        autocommit = getattr(conn, "autocommit", None)
        if callable(autocommit):
            autocommit(True)
        else:
            conn.autocommit = True
        return conn


class GatewayResolver:
    def __init__(
        self,
        query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
        gateways: dict[str, DatabaseGateway] | None = None,
    ) -> None:
        self.gateways = gateways or {
            "mysql": MySQLGateway(query_timeout_seconds=query_timeout_seconds),
            "postgresql": PostgreSQLGateway(query_timeout_seconds=query_timeout_seconds),
            "sqlserver": SQLServerGateway(query_timeout_seconds=query_timeout_seconds),
            "oracle": OracleGateway(query_timeout_seconds=query_timeout_seconds),
            "xugu": XuguGateway(query_timeout_seconds=query_timeout_seconds),
        }
        self._cache: dict[tuple[str, str, int, str, str], str] = {}

    def driver_status(self) -> dict[str, dict[str, Any]]:
        return {
            "mysql": {"available": MYSQL_DRIVER is not None, "driver": MYSQL_DRIVER_NAME},
            "postgresql": {"available": POSTGRES_DRIVER is not None, "driver": POSTGRES_DRIVER_NAME},
            "sqlserver": {"available": SQLSERVER_DRIVER is not None, "driver": SQLSERVER_DRIVER_NAME},
            "oracle": {"available": ORACLE_DRIVER is not None, "driver": ORACLE_DRIVER_NAME},
            "xugu": {"available": XUGU_DRIVER is not None, "driver": XUGU_DRIVER_NAME},
        }

    def _cache_key(self, profile: ConnectionProfile) -> tuple[str, str, int, str, str]:
        return (profile.id, profile.host, profile.port, profile.username, profile.database)

    def _type_for_standard_port(self, port: int) -> str | None:
        for database_type, default_port in DEFAULT_DATABASE_PORTS.items():
            if port == default_port:
                return database_type
        return None

    def _safe_error_detail(self, exc: AppError, password: str | None) -> str:
        detail = exc.details or exc.message
        detail = redact_sensitive_text(detail, password)
        detail = re.sub(r"\s+", " ", detail).strip()
        return detail[:180] + ("…" if len(detail) > 180 else "")

    def _probe_auto(self, profile: ConnectionProfile, password: str | None) -> tuple[str, DatabaseGateway, dict[str, Any]]:
        errors: list[str] = []
        for database_type in AUTO_PROBE_ORDER:
            gateway = self.gateways.get(database_type)
            if gateway is None:
                continue
            try:
                result = gateway.test_connection(profile, password)
                self._cache[self._cache_key(profile)] = database_type
                return database_type, gateway, result
            except AppError as exc:
                label = DATABASE_TYPE_LABELS.get(database_type, database_type)
                errors.append(f"{label}: {self._safe_error_detail(exc, password)}")
        raise AppError("自动识别连接失败。", 400, "；".join(errors))

    def resolve_gateway(self, profile: ConnectionProfile, password: str | None = None) -> tuple[str, DatabaseGateway]:
        database_type = normalize_database_type(profile.database_type)
        if database_type != "auto":
            return database_type, self.gateways[database_type]

        cached_type = self._cache.get(self._cache_key(profile))
        if cached_type:
            return cached_type, self.gateways[cached_type]

        port_type = self._type_for_standard_port(profile.port)
        if port_type:
            return port_type, self.gateways[port_type]

        resolved_type, gateway, _result = self._probe_auto(profile, password)
        return resolved_type, gateway

    def test_connection(self, profile: ConnectionProfile, password: str | None) -> dict[str, Any]:
        database_type = normalize_database_type(profile.database_type)
        if database_type == "auto":
            port_type = self._type_for_standard_port(profile.port)
            if port_type:
                gateway = self.gateways[port_type]
                result = gateway.test_connection(profile, password)
                self._cache[self._cache_key(profile)] = port_type
                return {**result, "database_type": "auto", "resolved_database_type": port_type}
            resolved_type, _gateway, result = self._probe_auto(profile, password)
            return {**result, "database_type": "auto", "resolved_database_type": resolved_type}

        gateway = self.gateways[database_type]
        result = gateway.test_connection(profile, password)
        return {**result, "database_type": database_type, "resolved_database_type": database_type}

    def list_schemas(self, profile: ConnectionProfile, password: str | None) -> list[str]:
        _database_type, gateway = self.resolve_gateway(profile, password)
        return gateway.list_schemas(profile, password)

    def list_tables(self, profile: ConnectionProfile, password: str | None, schema: str) -> list[dict[str, Any]]:
        _database_type, gateway = self.resolve_gateway(profile, password)
        return gateway.list_tables(profile, password, schema)

    def list_columns(self, profile: ConnectionProfile, password: str | None, schema: str, table: str) -> list[dict[str, Any]]:
        _database_type, gateway = self.resolve_gateway(profile, password)
        return gateway.list_columns(profile, password, schema, table)

    def search_rows(self, **kwargs: Any) -> dict[str, Any]:
        profile = kwargs["profile"]
        password = kwargs["password"]
        _database_type, gateway = self.resolve_gateway(profile, password)
        return gateway.search_rows(**kwargs)

    def execute_sql(self, profile: ConnectionProfile, password: str | None, sql: str) -> dict[str, Any]:
        _database_type, gateway = self.resolve_gateway(profile, password)
        return gateway.execute_sql(profile, password, sql)


class Application:
    def __init__(self, base_dir: Path, data_dir: Path, query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS):
        self.base_dir = base_dir
        self.static_dir = base_dir / "static"
        self.store = ConnectionStore(data_dir)
        self.gateway = GatewayResolver(query_timeout_seconds=query_timeout_seconds)
        self.query_timeout_seconds = query_timeout_seconds

    def make_handler(self):
        app = self

        class RequestHandler(BaseHTTPRequestHandler):
            server_version = "HeidiSQLLite/0.2"

            def do_GET(self):
                app.handle_request(self, "GET")

            def do_POST(self):
                app.handle_request(self, "POST")

            def do_PUT(self):
                app.handle_request(self, "PUT")

            def do_DELETE(self):
                app.handle_request(self, "DELETE")

            def log_message(self, fmt: str, *args: Any) -> None:
                # Security: Disable HTTP request logging to prevent password exposure
                # Original logs may contain sensitive data in request bodies
                pass

        return RequestHandler

    def handle_request(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        try:
            parsed = urlparse(handler.path)
            if parsed.path.startswith("/api/"):
                self._enforce_same_origin(handler)
                self._handle_api(handler, method, parsed)
                return
            if method != "GET":
                raise AppError("不支持的请求方法。", 405)
            self._serve_static(handler, parsed.path)
        except AppError as exc:
            self._send_json(handler, exc.status, {"error": exc.message, "details": exc.details})
        except Exception as exc:
            # Security: Don't print full traceback which may contain sensitive data
            error_type = type(exc).__name__
            print(f"[ERROR] {error_type}: {str(exc)}")
            self._send_json(handler, 500, {"error": "服务端出现未处理错误。", "details": error_type})

    def _enforce_same_origin(self, handler: BaseHTTPRequestHandler) -> None:
        origin = handler.headers.get("Origin")
        if not origin:
            return
        parsed_origin = urlparse(origin)
        request_host = handler.headers.get("Host", "")
        if parsed_origin.netloc.lower() != request_host.lower():
            raise AppError("跨来源请求被拒绝。", 403)

    def _handle_api(self, handler: BaseHTTPRequestHandler, method: str, parsed) -> None:
        path = parsed.path
        body = self._read_json(handler) if method in {"POST", "PUT"} else {}

        if path == "/api/health" and method == "GET":
            self._send_json(
                handler,
                200,
                {
                    "name": APP_NAME,
                    "drivers": self.gateway.driver_status(),
                    "query_timeout_seconds": self.query_timeout_seconds,
                },
            )
            return

        if path == "/api/connections" and method == "GET":
            items = [item.to_public_dict() for item in self.store.list_connections()]
            self._send_json(handler, 200, {"items": items})
            return

        if path == "/api/connections/test" and method == "POST":
            profile, password = self._profile_from_payload(body)
            result = self.gateway.test_connection(profile, password)
            self._send_json(handler, 200, result)
            return

        if path == "/api/connections" and method == "POST":
            profile, _password = self._profile_from_payload(body)
            self.store.save(profile)
            self._send_json(handler, 201, {"item": profile.to_public_dict()})
            return

        if path.startswith("/api/connections/"):
            connection_id = unquote(path.removeprefix("/api/connections/"))
            if method == "PUT":
                current = self.store.get(connection_id)
                data = self._normalize_profile_payload(body, fallback_id=current.id)
                profile = ConnectionProfile(**data)
                self.store.save(profile)
                self._send_json(handler, 200, {"item": profile.to_public_dict()})
                return
            if method == "DELETE":
                self.store.delete(connection_id)
                self._send_json(handler, 200, {"deleted": True})
                return

        if path == "/api/schemas" and method == "POST":
            profile, password = self._profile_and_password_from_body(body)
            items = self.gateway.list_schemas(profile, password)
            self._send_json(handler, 200, {"items": items})
            return

        if path == "/api/tables" and method == "POST":
            profile, password = self._profile_and_password_from_body(body)
            schema = self._require_body_value(body, "schema")
            items = self.gateway.list_tables(profile, password, schema)
            self._send_json(handler, 200, {"items": items})
            return

        if path == "/api/table-columns" and method == "POST":
            profile, password = self._profile_and_password_from_body(body)
            schema = self._require_body_value(body, "schema")
            table = self._require_body_value(body, "table")
            items = self.gateway.list_columns(profile, password, schema, table)
            self._send_json(handler, 200, {"items": items})
            return

        if path == "/api/query/search" and method == "POST":
            profile, password = self._profile_and_password_from_body(body)
            schema = self._require_body_value(body, "schema")
            table = self._require_body_value(body, "table")
            result = self.gateway.search_rows(
                profile=profile,
                password=password,
                schema=schema,
                table=table,
                keyword_terms=normalize_keyword_terms(body.get("keyword_terms"), (body.get("keyword") or "").strip() or None),
                keyword_columns=body.get("keyword_columns"),
                keyword_field_terms=normalize_keyword_field_terms(body.get("keyword_field_terms")),
                time_column=(body.get("time_column") or "").strip() or None,
                time_mode=(body.get("time_mode") or "range").strip(),
                time_point=(body.get("time_point") or "").strip() or None,
                time_from=(body.get("time_from") or "").strip() or None,
                time_to=(body.get("time_to") or "").strip() or None,
                limit=normalize_limit(body.get("limit")),
                sort_by=(body.get("sort_by") or "").strip() or None,
                sort_order=(body.get("sort_order") or "desc"),
                visible_columns=body.get("visible_columns"),
            )
            self._send_json(handler, 200, result)
            return

        if path == "/api/query/sql" and method == "POST":
            profile, password = self._profile_and_password_from_body(body)
            sql = self._require_body_value(body, "sql")
            result = self.gateway.execute_sql(profile, password, sql)
            self._send_json(handler, 200, result)
            return

        raise AppError("接口不存在。", 404)

    def _serve_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (self.static_dir / relative).resolve()
        static_root = self.static_dir.resolve()
        if file_path != static_root and static_root not in file_path.parents:
            raise AppError("静态文件路径无效。", 403)
        if not file_path.exists() or not file_path.is_file():
            raise AppError("页面不存在。", 404)
        content_type, _ = mimetypes.guess_type(str(file_path))
        payload = file_path.read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", content_type or "application/octet-stream")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _read_json(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            raise AppError("Content-Length 不合法。", 400)
        if length < 0:
            raise AppError("Content-Length 不合法。", 400)
        if length > MAX_JSON_BODY_BYTES:
            raise AppError("请求体过大。", 413)
        raw = handler.rfile.read(length) if length > 0 else b"{}"
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise AppError("请求体不是合法 JSON。", 400)

    def _send_json(self, handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=json_default).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(data)

    def _normalize_profile_payload(self, payload: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        host = str(payload.get("host") or "").strip()
        username = str(payload.get("username") or "").strip()
        database = str(payload.get("database") or "").strip()
        database_type = normalize_database_type(str(payload.get("database_type") or "auto"), default="auto")
        charset = str(payload.get("charset") or "utf8mb4").strip() or "utf8mb4"
        if not all([name, host, username]):
            raise AppError("连接名称、主机、用户名不能为空。", 400)
        try:
            default_port = DEFAULT_DATABASE_PORTS.get(database_type, 3306)
            port = int(payload.get("port") or default_port)
        except (TypeError, ValueError):
            raise AppError("端口必须是数字。", 400)
        connection_id = fallback_id or str(payload.get("id") or slugify(name))
        return {
            "id": connection_id,
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "database": database,
            "database_type": database_type,
            "charset": charset,
        }

    def _profile_from_payload(self, payload: dict[str, Any]) -> tuple[ConnectionProfile, str | None]:
        data = self._normalize_profile_payload(payload, fallback_id=(payload.get("id") or None))
        return ConnectionProfile(**data), payload.get("password")

    def _profile_and_password_from_body(self, payload: dict[str, Any]) -> tuple[ConnectionProfile, str | None]:
        connection_id = self._require_body_value(payload, "connection_id")
        password = self._require_body_value(payload, "password")
        return self.store.get(connection_id), password

    def _require_body_value(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if value in (None, ""):
            raise AppError(f"缺少字段: {key}", 400)
        return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} local server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding to a non-loopback host. Use only on trusted networks.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path.home() / ".heidisql-lite"),
        help="Directory for local connection definitions.",
    )
    parser.add_argument(
        "--query-timeout-seconds",
        type=int,
        default=DEFAULT_QUERY_TIMEOUT_SECONDS,
        help="Server-side database read/write timeout in seconds.",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the browser after the local server starts.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    return parser


class LogScopeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def configure_windows_console() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(f"{APP_NAME} - close this window to stop")
    except Exception:
        return


def is_windows_frozen_app() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def should_open_browser(open_browser: bool, no_browser: bool, frozen_windows: bool | None = None) -> bool:
    if no_browser:
        return False
    if frozen_windows is None:
        frozen_windows = is_windows_frozen_app()
    return open_browser or frozen_windows


def browser_url_for(host: str, port: int) -> str:
    normalized = host.strip().lower()
    if normalized in {"0.0.0.0", "::"}:
        browser_host = DEFAULT_HOST
    else:
        browser_host = host
    return server_url_for(browser_host, port)


def server_url_for(host: str, port: int) -> str:
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        url_host = f"[{host}]"
    else:
        url_host = host
    return f"http://{url_host}:{port}"


def open_browser_after_start(url: str, delay_seconds: float = BROWSER_OPEN_DELAY_SECONDS) -> None:
    def open_url() -> None:
        time_module.sleep(delay_seconds)
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"Could not open browser automatically: {exc}")

    threading.Thread(target=open_url, name="browser-launcher", daemon=True).start()


def main() -> None:
    args = build_parser().parse_args()
    configure_windows_console()
    if not args.allow_remote and not is_loopback_host(args.host):
        print("安全限制: 默认只允许绑定 127.0.0.1/localhost。若确需开放局域网，请显式添加 --allow-remote。")
        sys.exit(2)
    app = Application(
        base_dir=Path(__file__).parent.resolve(),
        data_dir=Path(args.data_dir).resolve(),
        query_timeout_seconds=max(15, int(args.query_timeout_seconds)),
    )
    server = LogScopeHTTPServer((args.host, args.port), app.make_handler())
    listen_url = server_url_for(args.host, args.port)
    browser_url = browser_url_for(args.host, args.port)
    print(f"{APP_NAME} listening on {listen_url}")
    print(f"Data dir: {Path(args.data_dir).resolve()}")
    print(f"Query timeout: {app.query_timeout_seconds}s")
    if should_open_browser(args.open_browser, args.no_browser):
        print(f"Opening browser: {browser_url}")
        print("Close this window to stop LogScope.")
        open_browser_after_start(browser_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
