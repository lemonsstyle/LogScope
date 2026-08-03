import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import (
    Application,
    AppError,
    ConnectionProfile,
    ConnectionStore,
    DatabaseGateway,
    GatewayResolver,
    MAX_SQL_ROWS,
    MySQLGateway,
    OracleGateway,
    PostgreSQLGateway,
    SQLServerGateway,
    XuguGateway,
    browser_url_for,
    escape_like_literal,
    is_loopback_host,
    json_default,
    normalize_keyword_terms,
    normalize_time_input,
    normalize_time_point,
    parse_sql_statement,
    should_open_browser,
)
from datetime import datetime


class SqlStatementTests(unittest.TestCase):
    def test_accepts_read_and_write_statements(self):
        statements = (
            "SELECT * FROM logs",
            "INSERT INTO logs(id) VALUES (1)",
            "UPDATE logs SET status = 'done' WHERE id = 1",
            "DELETE FROM logs WHERE id = 1",
            "CREATE TABLE logs_copy(id INT)",
            "ALTER TABLE logs_copy ADD status VARCHAR(20)",
            "TRUNCATE TABLE logs_copy",
            "DROP TABLE logs_copy",
            "GRANT SELECT ON logs TO analyst",
            "CALL refresh_logs()",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(parse_sql_statement(statement), statement)

    def test_allows_one_trailing_semicolon(self):
        self.assertEqual(parse_sql_statement("UPDATE logs SET status = 'done';"), "UPDATE logs SET status = 'done'")

    def test_rejects_multi_statement(self):
        with self.assertRaisesRegex(Exception, "只允许执行单条 SQL 语句"):
            parse_sql_statement("SELECT 1; DELETE FROM logs")

    def test_rejects_select_into_outfile(self):
        with self.assertRaisesRegex(Exception, "服务器文件"):
            parse_sql_statement("SELECT * FROM logs INTO OUTFILE '/tmp/logs.txt'")

    def test_rejects_sleep_function(self):
        with self.assertRaisesRegex(Exception, "阻塞表达式"):
            parse_sql_statement("SELECT SLEEP(10)")

    def test_rejects_postgresql_sleep_function(self):
        with self.assertRaisesRegex(Exception, "阻塞表达式"):
            parse_sql_statement("SELECT pg_sleep(10)")

    def test_rejects_server_system_command(self):
        with self.assertRaisesRegex(Exception, "系统命令"):
            parse_sql_statement("EXEC xp_cmdshell 'dir'")


class SearchHelperTests(unittest.TestCase):
    def test_escape_like_literal(self):
        self.assertEqual(escape_like_literal("_LIDAR_%"), "!_LIDAR!_!%")

    def test_normalize_time_input_day(self):
        self.assertEqual(normalize_time_input("20260606", "start"), "2026-06-06 00:00:00")
        self.assertEqual(normalize_time_input("20260606", "end"), "2026-06-06 23:59:59")

    def test_normalize_time_point_day(self):
        start, end = normalize_time_point("20260606")
        self.assertEqual(start, "2026-06-06 00:00:00")
        self.assertEqual(end, "2026-06-06 23:59:59")

    def test_rejects_time_with_seconds(self):
        with self.assertRaisesRegex(Exception, "支持 20260606"):
            normalize_time_input("20260603160133", "start")

    def test_json_default_for_datetime(self):
        self.assertEqual(json_default(datetime(2026, 6, 6, 15, 1, 2)), "2026-06-06 15:01:02")

    def test_normalize_keyword_terms(self):
        self.assertEqual(normalize_keyword_terms([" _LIDAR_ ", "", "53982 "]), ["_LIDAR_", "53982"])


class ConnectionStoreTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConnectionStore(Path(tmp))
            profile = ConnectionProfile(
                id="demo",
                name="demo",
                host="127.0.0.1",
                port=3306,
                username="root",
                database="logs",
            )
            store.save(profile)
            loaded = store.get("demo")
            self.assertEqual(loaded.host, "127.0.0.1")
            self.assertEqual(loaded.database_type, "mysql")
            self.assertEqual(len(store.list_connections()), 1)

    def test_legacy_connection_defaults_to_mysql(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "connections.json"
            file_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy",
                            "name": "legacy",
                            "host": "127.0.0.1",
                            "port": 3306,
                            "username": "root",
                            "database": "logs",
                            "charset": "utf8mb4",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            store = ConnectionStore(Path(tmp))
            self.assertEqual(store.get("legacy").database_type, "mysql")

    def test_save_database_type_without_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application.__new__(Application)
            store = ConnectionStore(Path(tmp))
            data = app._normalize_profile_payload(
                {
                    "name": "pg",
                    "host": "127.0.0.1",
                    "port": 5432,
                    "username": "postgres",
                    "database": "logs",
                    "database_type": "postgresql",
                    "password": "secret",
                }
            )
            store.save(ConnectionProfile(**data))
            raw_items = json.loads((Path(tmp) / "connections.json").read_text(encoding="utf-8"))
            self.assertEqual(raw_items[0]["database_type"], "postgresql")
            self.assertNotIn("password", raw_items[0])


class ApplicationTests(unittest.TestCase):
    def test_invalid_profile_payload(self):
        app = Application.__new__(Application)
        with self.assertRaisesRegex(Exception, "连接名称、主机、用户名不能为空"):
            app._normalize_profile_payload({})

    def test_connections_file_initializes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConnectionStore(Path(tmp))
            file_path = Path(tmp) / "connections.json"
            self.assertTrue(file_path.exists())
            self.assertEqual(json.loads(file_path.read_text(encoding="utf-8")), [])

    def test_loopback_host_detection(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertFalse(is_loopback_host("0.0.0.0"))

    def test_packaged_windows_exe_opens_browser_by_default(self):
        self.assertTrue(should_open_browser(False, False, frozen_windows=True))
        self.assertFalse(should_open_browser(False, False, frozen_windows=False))
        self.assertTrue(should_open_browser(True, False, frozen_windows=False))
        self.assertFalse(should_open_browser(True, True, frozen_windows=True))

    def test_browser_url_uses_reachable_loopback_host(self):
        self.assertEqual(browser_url_for("127.0.0.1", 8765), "http://127.0.0.1:8765")
        self.assertEqual(browser_url_for("0.0.0.0", 8765), "http://127.0.0.1:8765")
        self.assertEqual(browser_url_for("::1", 8765), "http://[::1]:8765")

    def test_database_error_redacts_password(self):
        gateway = MySQLGateway.__new__(MySQLGateway)

        def fake_connect(_profile, _password):
            raise RuntimeError("driver error includes secret")

        gateway.connect = fake_connect
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
        )
        with self.assertRaises(AppError) as context:
            gateway.test_connection(profile, "secret")
        self.assertNotIn("secret", context.exception.details)
        self.assertIn("******", context.exception.details)

    def test_new_database_types_use_default_ports(self):
        app = Application.__new__(Application)
        cases = (("oracle", 1521), ("xugu", 5138))
        for database_type, expected_port in cases:
            with self.subTest(database_type=database_type):
                data = app._normalize_profile_payload(
                    {
                        "name": database_type,
                        "host": "127.0.0.1",
                        "username": "tester",
                        "database": "logs",
                        "database_type": database_type,
                    }
                )
                self.assertEqual(data["port"], expected_port)


class FakeGateway:
    def __init__(self, database_type, should_fail=False):
        self.database_type = database_type
        self.should_fail = should_fail
        self.calls = 0

    def test_connection(self, _profile, password):
        self.calls += 1
        if self.should_fail:
            raise AppError("连接失败。", 400, f"{self.database_type} failed with {password}")
        return {"ok": True, "database": f"{self.database_type}_db", "version": "1.0"}


class GatewayResolverTests(unittest.TestCase):
    def test_auto_uses_standard_port(self):
        gateways = {
            "mysql": FakeGateway("mysql"),
            "postgresql": FakeGateway("postgresql"),
            "sqlserver": FakeGateway("sqlserver"),
        }
        resolver = GatewayResolver(gateways=gateways)
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=5432,
            username="postgres",
            database="logs",
            database_type="auto",
        )
        resolved_type, gateway = resolver.resolve_gateway(profile, "secret")
        self.assertEqual(resolved_type, "postgresql")
        self.assertIs(gateway, gateways["postgresql"])
        self.assertEqual(gateways["postgresql"].calls, 0)

    def test_auto_nonstandard_port_probes_and_caches_success(self):
        gateways = {
            "mysql": FakeGateway("mysql", should_fail=True),
            "postgresql": FakeGateway("postgresql"),
            "sqlserver": FakeGateway("sqlserver"),
        }
        resolver = GatewayResolver(gateways=gateways)
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=15432,
            username="postgres",
            database="logs",
            database_type="auto",
        )
        result = resolver.test_connection(profile, "secret")
        self.assertEqual(result["resolved_database_type"], "postgresql")
        self.assertEqual(gateways["mysql"].calls, 1)
        self.assertEqual(gateways["postgresql"].calls, 1)

        resolved_type, gateway = resolver.resolve_gateway(profile)
        self.assertEqual(resolved_type, "postgresql")
        self.assertIs(gateway, gateways["postgresql"])
        self.assertEqual(gateways["postgresql"].calls, 1)

    def test_auto_failure_redacts_password_in_summary(self):
        gateways = {
            "mysql": FakeGateway("mysql", should_fail=True),
            "postgresql": FakeGateway("postgresql", should_fail=True),
            "sqlserver": FakeGateway("sqlserver", should_fail=True),
        }
        resolver = GatewayResolver(gateways=gateways)
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=15432,
            username="root",
            database="logs",
            database_type="auto",
        )
        with self.assertRaises(AppError) as context:
            resolver.test_connection(profile, "secret")
        self.assertIn("MySQL", context.exception.details)
        self.assertIn("PostgreSQL", context.exception.details)
        self.assertIn("SQL Server", context.exception.details)
        self.assertNotIn("secret", context.exception.details)

    def test_auto_recognizes_oracle_and_xugu_standard_ports(self):
        cases = (("oracle", 1521), ("xugu", 5138))
        for database_type, port in cases:
            with self.subTest(database_type=database_type):
                gateway = FakeGateway(database_type)
                resolver = GatewayResolver(gateways={database_type: gateway})
                profile = ConnectionProfile(
                    id=database_type,
                    name=database_type,
                    host="127.0.0.1",
                    port=port,
                    username="tester",
                    database="logs",
                    database_type="auto",
                )
                resolved_type, resolved_gateway = resolver.resolve_gateway(profile, "secret")
                self.assertEqual(resolved_type, database_type)
                self.assertIs(resolved_gateway, gateway)
                self.assertEqual(gateway.calls, 0)


class DialectTests(unittest.TestCase):
    def test_dialect_fragments(self):
        self.assertEqual(MySQLGateway().table_reference("logs", "events"), "`logs`.`events`")
        self.assertEqual(PostgreSQLGateway().table_reference("public", "events"), '"public"."events"')
        self.assertEqual(SQLServerGateway().table_reference("dbo", "events"), "[dbo].[events]")
        self.assertEqual(OracleGateway().table_reference("APP", "EVENTS"), '"APP"."EVENTS"')
        self.assertEqual(XuguGateway().table_reference("APP", "EVENTS"), '"APP"."EVENTS"')
        self.assertEqual(MySQLGateway().text_cast("`id`"), "CAST(`id` AS CHAR)")
        self.assertEqual(PostgreSQLGateway().text_cast('"id"'), 'CAST("id" AS TEXT)')
        self.assertEqual(SQLServerGateway().text_cast("[id]"), "CAST([id] AS NVARCHAR(MAX))")
        self.assertEqual(OracleGateway().text_cast('"id"'), 'CAST("id" AS VARCHAR2(4000))')
        self.assertEqual(XuguGateway().text_cast('"id"'), 'CAST("id" AS VARCHAR)')
        self.assertEqual(MySQLGateway().limit_clause(), " LIMIT %s")
        self.assertEqual(PostgreSQLGateway().limit_clause(), " LIMIT %s")
        self.assertEqual(SQLServerGateway().limit_clause(), " OFFSET 0 ROWS FETCH NEXT %s ROWS ONLY")
        self.assertEqual(OracleGateway().bind_placeholder(3), ":3")
        self.assertEqual(XuguGateway().bind_placeholder(3), "?")

    def test_oracle_and_xugu_rownum_limits(self):
        oracle_sql, oracle_params = OracleGateway().apply_limit("SELECT * FROM events", ("term",), 50)
        self.assertEqual(oracle_sql, "SELECT * FROM (SELECT * FROM events) WHERE ROWNUM <= :2")
        self.assertEqual(oracle_params, ("term", 50))

        xugu_sql, xugu_params = XuguGateway().apply_limit("SELECT * FROM events", ("term",), 50)
        self.assertEqual(xugu_sql, "SELECT * FROM (SELECT * FROM events) WHERE ROWNUM <= ?")
        self.assertEqual(xugu_params, ("term", 50))

    def test_tuple_rows_use_cursor_description(self):
        rows = OracleGateway()._rows_to_dicts([(1, "ok")], [("ID",), ("MESSAGE",)])
        self.assertEqual(rows, [{"ID": 1, "MESSAGE": "ok"}])

    def test_oracle_metadata_queries(self):
        gateway = OracleGateway()
        table_sql, table_params = gateway.list_tables_query("app")
        column_sql, column_params = gateway.list_columns_query("app", "events")
        self.assertIn("all_tables", table_sql)
        self.assertIn("all_views", table_sql)
        self.assertIn("owner = :1", table_sql)
        self.assertEqual(table_params, ("APP", "APP"))
        self.assertIn("all_tab_columns", column_sql)
        self.assertEqual(column_params, ("APP", "EVENTS"))

    def test_xugu_metadata_queries(self):
        gateway = XuguGateway()
        schema_sql, schema_params = gateway.list_schemas_query()
        table_sql, table_params = gateway.list_tables_query("app")
        column_sql, column_params = gateway.list_columns_query("app", "events")
        self.assertIn("all_schemas", schema_sql)
        self.assertEqual(schema_params, ())
        self.assertIn("all_tables", table_sql)
        self.assertEqual(table_params, ("APP",))
        self.assertIn("all_columns", column_sql)
        self.assertEqual(column_params, ("APP", "EVENTS"))

    def test_postgresql_metadata_queries(self):
        gateway = PostgreSQLGateway()
        captured = {}

        def fake_fetch_all(_profile, _password, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [
                {
                    "column_name": "created_at",
                    "data_type": "timestamp without time zone",
                    "column_type": "timestamp without time zone",
                    "is_nullable": "YES",
                }
            ], 0.01

        gateway._fetch_all = fake_fetch_all
        profile = ConnectionProfile(
            id="pg",
            name="pg",
            host="127.0.0.1",
            port=5432,
            username="postgres",
            database="logs",
            database_type="postgresql",
        )
        columns = gateway.list_columns(profile, "secret", "public", "events")
        self.assertIn("information_schema.columns", captured["sql"])
        self.assertEqual(captured["params"], ("public", "events"))
        self.assertTrue(columns[0]["is_time_like"])

    def test_sqlserver_metadata_queries(self):
        gateway = SQLServerGateway()
        captured = {}

        def fake_fetch_all(_profile, _password, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [{"table_name": "events", "table_type": "BASE TABLE"}], 0.01

        gateway._fetch_all = fake_fetch_all
        profile = ConnectionProfile(
            id="mssql",
            name="mssql",
            host="127.0.0.1",
            port=1433,
            username="sa",
            database="logs",
            database_type="sqlserver",
        )
        tables = gateway.list_tables(profile, "secret", "dbo")
        self.assertIn("information_schema.tables", captured["sql"])
        self.assertEqual(captured["params"], ("dbo",))
        self.assertEqual(tables[0]["table_name"], "events")


class NewDriverConnectionTests(unittest.TestCase):
    def test_oracle_connect_uses_service_name_and_timeout(self):
        class FakeConnection:
            call_timeout = 0
            autocommit = False

        class FakeDriver:
            def __init__(self):
                self.dsn_args = None
                self.connect_kwargs = None

            def makedsn(self, host, port, service_name):
                self.dsn_args = (host, port, service_name)
                return "oracle-dsn"

            def connect(self, **kwargs):
                self.connect_kwargs = kwargs
                return FakeConnection()

        fake_driver = FakeDriver()
        profile = ConnectionProfile(
            id="oracle",
            name="oracle",
            host="db.example.com",
            port=1521,
            username="app",
            database="ORCLPDB1",
            database_type="oracle",
        )
        with patch("app.ORACLE_DRIVER", fake_driver):
            conn = OracleGateway(query_timeout_seconds=12).connect(profile, "secret")
        self.assertEqual(fake_driver.dsn_args, ("db.example.com", 1521, "ORCLPDB1"))
        self.assertEqual(
            fake_driver.connect_kwargs,
            {"user": "app", "password": "secret", "dsn": "oracle-dsn"},
        )
        self.assertEqual(conn.call_timeout, 12000)
        self.assertTrue(conn.autocommit)

    def test_xugu_connect_uses_vendor_driver_arguments(self):
        class FakeConnection:
            def __init__(self):
                self.autocommit_value = None

            def autocommit(self, value):
                self.autocommit_value = value

        class FakeDriver:
            def __init__(self):
                self.connect_kwargs = None

            def connect(self, **kwargs):
                self.connect_kwargs = kwargs
                return FakeConnection()

        fake_driver = FakeDriver()
        profile = ConnectionProfile(
            id="xugu",
            name="xugu",
            host="db.example.com",
            port=5138,
            username="SYSDBA",
            database="SYSTEM",
            database_type="xugu",
        )
        with patch("app.XUGU_DRIVER", fake_driver):
            conn = XuguGateway().connect(profile, "secret")
        self.assertEqual(
            fake_driver.connect_kwargs,
            {
                "host": "db.example.com",
                "port": 5138,
                "database": "SYSTEM",
                "user": "SYSDBA",
                "password": "secret",
                "charset": "UTF8",
            },
        )
        self.assertTrue(conn.autocommit_value)


class SearchSortTests(unittest.TestCase):
    def _run_search_for_gateway(self, gateway, profile):
        gateway.list_columns = lambda *_args, **_kwargs: [
            {
                "column_name": "id",
                "data_type": "bigint",
                "column_type": "bigint",
                "is_nullable": "NO",
                "is_time_like": False,
                "is_text_searchable": False,
            },
            {
                "column_name": "message",
                "data_type": "text",
                "column_type": "text",
                "is_nullable": "YES",
                "is_time_like": False,
                "is_text_searchable": True,
            },
            {
                "column_name": "created_at",
                "data_type": "datetime",
                "column_type": "datetime",
                "is_nullable": "NO",
                "is_time_like": True,
                "is_text_searchable": False,
            },
        ]
        captured = {}

        def fake_fetch_all(_profile, _password, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [{"id": 1, "message": "ok"}], 0.01

        gateway._fetch_all = fake_fetch_all
        result = gateway.search_rows(
            profile=profile,
            password="secret",
            schema="dbo" if profile.database_type == "sqlserver" else "logs",
            table="events",
            keyword_terms=["_LIDAR_"],
            keyword_columns=["id", "message"],
            keyword_field_terms=None,
            time_column="created_at",
            time_mode="range",
            time_point=None,
            time_from="20260606",
            time_to=None,
            limit=75,
            sort_by="created_at",
            sort_order="desc",
            visible_columns=["id", "message"],
        )
        return captured, result

    def test_mysql_search_sql(self):
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
            database_type="mysql",
        )
        captured, result = self._run_search_for_gateway(MySQLGateway(), profile)
        self.assertIn("FROM `logs`.`events`", captured["sql"])
        self.assertIn("CAST(`id` AS CHAR) LIKE %s", captured["sql"])
        self.assertIn("ORDER BY `created_at` DESC LIMIT %s", captured["sql"])
        self.assertEqual(captured["params"][0], "2026-06-06 00:00:00")
        self.assertEqual(captured["params"][-1], 75)
        self.assertEqual(result["applied_sort"], {"column": "created_at", "order": "desc"})

    def test_keyword_field_terms_use_field_specific_groups(self):
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
            database_type="mysql",
        )
        gateway = MySQLGateway()
        gateway.list_columns = lambda *_args, **_kwargs: [
            {"column_name": "id", "data_type": "int", "is_time_like": False, "is_text_searchable": False},
            {"column_name": "filename", "data_type": "varchar", "is_time_like": False, "is_text_searchable": True},
            {"column_name": "sender", "data_type": "varchar", "is_time_like": False, "is_text_searchable": True},
            {"column_name": "created_at", "data_type": "datetime", "is_time_like": True, "is_text_searchable": False},
        ]
        captured = {}

        def fake_fetch_all(_profile, _password, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [{"filename": "report.csv", "sender": "alice"}], 0.01

        gateway._fetch_all = fake_fetch_all
        result = gateway.search_rows(
            profile=profile,
            password="secret",
            schema="logs",
            table="events",
            keyword_terms=["ignored"],
            keyword_columns=["id"],
            keyword_field_terms=[
                {"column": "filename", "terms": ["report", "summary"]},
                {"column": "sender", "terms": ["alice"]},
            ],
            time_column=None,
            time_mode="range",
            time_point=None,
            time_from=None,
            time_to=None,
            limit=50,
            sort_by="created_at",
            sort_order="desc",
            visible_columns=["filename", "sender"],
        )

        self.assertIn("(`filename` LIKE %s ESCAPE '!' OR `filename` LIKE %s ESCAPE '!')", captured["sql"])
        self.assertIn("AND (`sender` LIKE %s ESCAPE '!')", captured["sql"])
        self.assertNotIn("CAST(`id` AS CHAR)", captured["sql"])
        self.assertEqual(list(captured["params"][:3]), ["%report%", "%summary%", "%alice%"])
        self.assertEqual(result["keyword_field_terms"][0]["column"], "filename")

    def test_postgresql_search_sql(self):
        profile = ConnectionProfile(
            id="pg",
            name="pg",
            host="127.0.0.1",
            port=5432,
            username="postgres",
            database="logs",
            database_type="postgresql",
        )
        captured, _result = self._run_search_for_gateway(PostgreSQLGateway(), profile)
        self.assertIn('FROM "logs"."events"', captured["sql"])
        self.assertIn('CAST("id" AS TEXT) LIKE %s', captured["sql"])
        self.assertIn('ORDER BY "created_at" DESC LIMIT %s', captured["sql"])

    def test_sqlserver_search_sql(self):
        profile = ConnectionProfile(
            id="mssql",
            name="mssql",
            host="127.0.0.1",
            port=1433,
            username="sa",
            database="logs",
            database_type="sqlserver",
        )
        captured, _result = self._run_search_for_gateway(SQLServerGateway(), profile)
        self.assertIn("FROM [dbo].[events]", captured["sql"])
        self.assertIn("CAST([id] AS NVARCHAR(MAX)) LIKE %s", captured["sql"])
        self.assertIn("ORDER BY [created_at] DESC OFFSET 0 ROWS FETCH NEXT %s ROWS ONLY", captured["sql"])

    def test_oracle_search_sql(self):
        profile = ConnectionProfile(
            id="oracle",
            name="oracle",
            host="127.0.0.1",
            port=1521,
            username="app",
            database="ORCLPDB1",
            database_type="oracle",
        )
        captured, _result = self._run_search_for_gateway(OracleGateway(), profile)
        self.assertIn('FROM "logs"."events"', captured["sql"])
        self.assertIn('"created_at" >= :1', captured["sql"])
        self.assertIn('CAST("id" AS VARCHAR2(4000)) LIKE :2', captured["sql"])
        self.assertIn('"message" LIKE :3', captured["sql"])
        self.assertTrue(captured["sql"].startswith("SELECT * FROM ("))
        self.assertIn(") WHERE ROWNUM <= :4", captured["sql"])

    def test_xugu_search_sql(self):
        profile = ConnectionProfile(
            id="xugu",
            name="xugu",
            host="127.0.0.1",
            port=5138,
            username="SYSDBA",
            database="SYSTEM",
            database_type="xugu",
        )
        captured, _result = self._run_search_for_gateway(XuguGateway(), profile)
        self.assertIn('FROM "logs"."events"', captured["sql"])
        self.assertIn('CAST("id" AS VARCHAR) LIKE ?', captured["sql"])
        self.assertIn('ORDER BY "created_at" DESC', captured["sql"])
        self.assertTrue(captured["sql"].startswith("SELECT * FROM ("))
        self.assertIn(") WHERE ROWNUM <= ?", captured["sql"])

    def test_defaults_to_time_column_desc_sort(self):
        gateway = MySQLGateway.__new__(MySQLGateway)
        gateway.list_columns = lambda *_args, **_kwargs: [
            {
                "column_name": "id",
                "data_type": "bigint",
                "column_type": "bigint",
                "is_nullable": "NO",
                "is_time_like": False,
                "is_text_searchable": False,
            },
            {
                "column_name": "created_at",
                "data_type": "datetime",
                "column_type": "datetime",
                "is_nullable": "NO",
                "is_time_like": True,
                "is_text_searchable": False,
            },
        ]
        captured = {}

        def fake_fetch_all(_profile, _password, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return [{"id": 1, "created_at": "2026-06-06 10:00:00"}], 0.01

        gateway._fetch_all = fake_fetch_all
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
        )

        result = MySQLGateway.search_rows(
            gateway,
            profile=profile,
            password="secret",
            schema="logs",
            table="events",
            keyword_terms=None,
            keyword_columns=None,
            keyword_field_terms=None,
            time_column=None,
            time_mode="range",
            time_point=None,
            time_from=None,
            time_to=None,
            limit=100,
            sort_by=None,
            sort_order="desc",
            visible_columns=["id", "created_at"],
        )

        self.assertIn("ORDER BY `created_at` DESC", captured["sql"])
        self.assertEqual(captured["params"][-1], 100)
        self.assertEqual(result["applied_sort"], {"column": "created_at", "order": "desc"})

    def test_advanced_select_uses_limited_fetch(self):
        class FakeCursor:
            description = [("id",)]
            rowcount = 2

            def __init__(self):
                self.sql = None
                self.fetch_size = None

            def execute(self, sql):
                self.sql = sql

            def fetchmany(self, size):
                self.fetch_size = size
                return [(1,), (2,)]

            def close(self):
                pass

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                pass

        gateway = DatabaseGateway()
        connection = FakeConnection()
        gateway.connect = lambda _profile, _password: connection
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
        )

        result = gateway.execute_sql(profile, "secret", "SELECT * FROM logs")

        self.assertEqual(connection.cursor_instance.sql, "SELECT * FROM logs")
        self.assertEqual(connection.cursor_instance.fetch_size, MAX_SQL_ROWS + 1)
        self.assertTrue(result["has_result_set"])
        self.assertEqual(result["statement_type"], "SELECT")
        self.assertEqual(result["columns"], ["id"])
        self.assertEqual(result["rows"], [{"id": 1}, {"id": 2}])
        self.assertFalse(result["truncated"])

    def test_advanced_update_returns_affected_rows(self):
        class FakeCursor:
            description = None
            rowcount = 3

            def __init__(self):
                self.sql = None

            def execute(self, sql):
                self.sql = sql

            def close(self):
                pass

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                pass

        gateway = DatabaseGateway()
        connection = FakeConnection()
        gateway.connect = lambda _profile, _password: connection
        profile = ConnectionProfile(
            id="demo",
            name="demo",
            host="127.0.0.1",
            port=3306,
            username="root",
            database="logs",
        )

        result = gateway.execute_sql(profile, "secret", "UPDATE logs SET status = 'done'")

        self.assertEqual(connection.cursor_instance.sql, "UPDATE logs SET status = 'done'")
        self.assertFalse(result["has_result_set"])
        self.assertEqual(result["statement_type"], "UPDATE")
        self.assertEqual(result["affected_rows"], 3)
        self.assertEqual(result["columns"], [])
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
