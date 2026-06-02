from __future__ import annotations

import os
import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class WebDataAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env_patcher = patch.dict(os.environ, {"WEB_DATA_WS_TOKEN": ""})
        self.env_patcher.start()
        test_tmp = Path.cwd() / "data" / "test_tmp"
        test_tmp.mkdir(parents=True, exist_ok=True)
        self.root = test_tmp / f"momcozy-web-data-tests-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "milk_management.db"
        self.file_root = self.root / "milk_process" / "configs"

        from web_data.app import create_app

        self.client = TestClient(create_app(db_path=self.db_path, file_root=self.file_root))

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def login(self) -> None:
        response = self.client.post("/api/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["authenticated"], True)

    def test_rejects_unauthenticated_api_access(self) -> None:
        response = self.client.get("/api/db/tables")

        self.assertEqual(response.status_code, 401)

    def test_session_reports_false_before_login_without_401(self) -> None:
        response = self.client.get("/api/session")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"authenticated": False})

    def test_websocket_connects_without_login_when_token_is_not_configured(self) -> None:
        with self.client.websocket_connect("/api/ws?user_id=user-1") as websocket:
            accepted = websocket.receive_json()
            self.assertEqual(accepted["type"], "connection.accepted")
            self.assertEqual(accepted["payload"]["token_required"], False)
            self.assertEqual(accepted["payload"]["user_id"], "user-1")

            websocket.send_json({"type": "ping", "request_id": "ping-1"})
            pong = websocket.receive_json()

        self.assertEqual(pong["type"], "pong")
        self.assertEqual(pong["request_id"], "ping-1")
        self.assertIn("server_time", pong["payload"])

    def test_websocket_requires_token_when_configured(self) -> None:
        from web_data.app import create_app

        with patch.dict(os.environ, {"WEB_DATA_WS_TOKEN": "secret-token"}):
            client = TestClient(create_app(db_path=self.db_path, file_root=self.file_root))

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws"):
                pass

        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/api/ws?token=wrong"):
                pass

        with client.websocket_connect("/api/ws?token=secret-token") as websocket:
            accepted = websocket.receive_json()
            self.assertEqual(accepted["type"], "connection.accepted")
            self.assertEqual(accepted["payload"]["token_required"], True)

            websocket.send_json({"type": "echo", "request_id": "echo-1", "payload": {"ok": True}})
            echo = websocket.receive_json()

        self.assertEqual(echo, {"type": "echo", "request_id": "echo-1", "payload": {"ok": True}})

    def test_websocket_receives_db_change_events(self) -> None:
        self.login()
        self._create_test_table()

        with self.client.websocket_connect("/api/ws") as websocket:
            websocket.receive_json()
            create = self.client.post(
                "/api/db/tables/test_items/rows",
                json={"id": 1, "name": "first", "quantity": 2},
            )
            event = websocket.receive_json()

        self.assertEqual(create.status_code, 200)
        self.assertEqual(event["type"], "db.row.created")
        self.assertEqual(event["payload"]["table"], "test_items")
        self.assertEqual(event["payload"]["row"]["name"], "first")

    def test_notification_report_sends_to_target_user_websocket_without_login(self) -> None:
        with self.client.websocket_connect("/api/ws?user_id=user-1") as user_websocket:
            user_websocket.receive_json()
            with self.client.websocket_connect("/api/ws?user_id=other-user") as other_websocket:
                other_websocket.receive_json()
                response = self.client.post(
                    "/api/notifications/report",
                    json={
                        "user_id": "user-1",
                        "reminder_type": "feeding_due",
                        "title": "Feeding reminder",
                        "message": "Bottle is due",
                        "data": {"infant_id": "baby-1"},
                    },
                )
                event = user_websocket.receive_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reported"], True)
        self.assertEqual(response.json()["delivered"], 1)
        self.assertEqual(event["type"], "notification.reported")
        self.assertEqual(event["payload"]["user_id"], "user-1")
        self.assertEqual(event["payload"]["reminder_type"], "feeding_due")
        self.assertEqual(event["payload"]["data"], {"infant_id": "baby-1"})
        self.assertIn("reported_at", event["payload"])

    def test_notification_report_requires_reminder_type(self) -> None:
        response = self.client.post("/api/notifications/report", json={"user_id": "user-1", "message": "missing type"})

        self.assertEqual(response.status_code, 400)

    def test_notification_report_requires_user_id(self) -> None:
        response = self.client.post("/api/notifications/report", json={"reminder_type": "feeding_due"})

        self.assertEqual(response.status_code, 400)

    def test_notification_report_requires_token_when_configured(self) -> None:
        from web_data.app import create_app

        with patch.dict(os.environ, {"WEB_DATA_WS_TOKEN": "secret-token"}):
            client = TestClient(create_app(db_path=self.db_path, file_root=self.file_root))

        missing = client.post("/api/notifications/report", json={"user_id": "user-1", "reminder_type": "feeding_due"})
        self.assertEqual(missing.status_code, 401)

        wrong = client.post(
            "/api/notifications/report",
            headers={"Authorization": "Bearer wrong"},
            json={"user_id": "user-1", "reminder_type": "feeding_due"},
        )
        self.assertEqual(wrong.status_code, 401)

        good = client.post(
            "/api/notifications/report",
            headers={"Authorization": "Bearer secret-token"},
            json={"user_id": "user-1", "type": "feeding_due", "message": "ok"},
        )
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json()["notification"]["reminder_type"], "feeding_due")

    def test_login_accepts_default_credentials_and_rejects_bad_password(self) -> None:
        bad = self.client.post("/api/login", json={"username": "admin", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)

        good = self.client.post("/api/login", json={"username": "admin", "password": "admin123"})
        self.assertEqual(good.status_code, 200)
        self.assertEqual(good.json(), {"authenticated": True, "username": "admin"})

        session = self.client.get("/api/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {"authenticated": True, "username": "admin"})

    def test_initializes_milk_management_database_and_lists_core_tables(self) -> None:
        self.login()

        response = self.client.get("/api/db/tables")

        self.assertEqual(response.status_code, 200)
        tables = response.json()["tables"]
        self.assertIn("user_profile", tables)
        self.assertIn("infant_profile", tables)
        self.assertIn("calendar", tables)
        self.assertTrue(self.db_path.exists())

    def test_crud_rows_for_table_with_primary_key(self) -> None:
        self.login()
        self._create_test_table()

        create = self.client.post(
            "/api/db/tables/test_items/rows",
            json={"id": 1, "name": "first", "quantity": 2},
        )
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json()["row"]["name"], "first")

        update = self.client.put(
            "/api/db/tables/test_items/rows/1",
            json={"name": "updated", "quantity": 5},
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.json()["row"]["quantity"], 5)

        rows = self.client.get("/api/db/tables/test_items/rows")
        self.assertEqual(rows.status_code, 200)
        self.assertEqual(rows.json()["rows"][0]["name"], "updated")

        delete = self.client.delete("/api/db/tables/test_items/rows/1")
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json()["deleted"], True)

        rows_after_delete = self.client.get("/api/db/tables/test_items/rows")
        self.assertEqual(rows_after_delete.json()["total"], 0)

    def test_filters_rows_by_specific_field(self) -> None:
        self.login()
        self._create_test_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.executemany(
                "INSERT INTO test_items(id, name, quantity) VALUES (?, ?, ?)",
                [(1, "alpha", 2), (2, "beta", 2), (3, "alphabet", 9)],
            )

        by_name = self.client.get("/api/db/tables/test_items/rows", params={"field": "name", "q": "alpha"})
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(by_name.json()["total"], 2)
        self.assertEqual([row["id"] for row in by_name.json()["rows"]], [1, 3])

        by_quantity = self.client.get("/api/db/tables/test_items/rows", params={"field": "quantity", "q": "2"})
        self.assertEqual(by_quantity.status_code, 200)
        self.assertEqual(by_quantity.json()["total"], 2)
        self.assertEqual([row["id"] for row in by_quantity.json()["rows"]], [1, 2])

    def test_rejects_unknown_filter_field(self) -> None:
        self.login()
        self._create_test_table()

        response = self.client.get("/api/db/tables/test_items/rows", params={"field": "missing", "q": "alpha"})

        self.assertEqual(response.status_code, 400)

    def test_file_create_read_update_delete_under_config_root(self) -> None:
        self.login()

        create = self.client.post("/api/files", json={"path": "pump/a.json", "content": "{\"mode\":\"auto\"}"})
        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json()["path"], "pump/a.json")

        content = self.client.get("/api/files/content", params={"path": "pump/a.json"})
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.json()["content"], "{\"mode\":\"auto\"}")
        self.assertEqual(content.json()["editable"], True)

        update = self.client.put("/api/files/content", json={"path": "pump/a.json", "content": "{\"mode\":\"manual\"}"})
        self.assertEqual(update.status_code, 200)

        files = self.client.get("/api/files")
        self.assertEqual(files.status_code, 200)
        self.assertEqual(files.json()["files"][0]["path"], "pump/a.json")

        delete = self.client.delete("/api/files", params={"path": "pump/a.json"})
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(delete.json()["deleted"], True)

    def test_file_list_only_includes_json_files(self) -> None:
        self.login()
        (self.file_root / "pump").mkdir(parents=True, exist_ok=True)
        (self.file_root / "pump" / "a.json").write_text("{}", encoding="utf-8")
        (self.file_root / "pump" / "b.JSON").write_text("{}", encoding="utf-8")
        (self.file_root / "pump" / "notes.txt").write_text("hidden", encoding="utf-8")

        response = self.client.get("/api/files")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([file["path"] for file in response.json()["files"]], ["pump/a.json", "pump/b.JSON"])

    def test_file_paths_cannot_escape_config_root(self) -> None:
        self.login()

        response = self.client.get("/api/files/content", params={"path": "../secret.txt"})

        self.assertEqual(response.status_code, 400)

    def test_hidden_attribute_is_not_overridden_by_view_display_css(self) -> None:
        css = (Path.cwd() / "web_data" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)

    def test_refresh_button_syncs_search_input_before_loading_rows(self) -> None:
        app_js = (Path.cwd() / "web_data" / "app.js").read_text(encoding="utf-8", errors="replace")

        self.assertIn("syncSearchFromInput(kind);", app_js)
        self.assertIn('${prefix}-refresh`).addEventListener("click"', app_js)
        self.assertIn('${prefix}-add`).addEventListener("click"', app_js)

    def test_table_ui_supports_select_all_and_bulk_delete(self) -> None:
        app_js = (Path.cwd() / "web_data" / "app.js").read_text(encoding="utf-8", errors="replace")
        index_html = (Path.cwd() / "web_data" / "index.html").read_text(encoding="utf-8")

        self.assertIn("core-bulk-delete", index_html)
        self.assertIn("all-bulk-delete", index_html)
        self.assertIn("data-action=\"select-all\"", app_js)
        self.assertIn("data-action=\"select-row\"", app_js)
        self.assertIn("deleteSelectedRows(kind)", app_js)
        self.assertIn("updateSelectionToolbar(prefix, kind)", app_js)

    def test_notification_report_ui_is_available(self) -> None:
        app_js = (Path.cwd() / "web_data" / "app.js").read_text(encoding="utf-8", errors="replace")
        index_html = (Path.cwd() / "web_data" / "index.html").read_text(encoding="utf-8")

        self.assertIn("notifications-view", index_html)
        self.assertIn("notification-form", index_html)
        self.assertIn("notification-user-id", index_html)
        self.assertIn("task_reminder", index_html)
        self.assertIn("lactation_feeding_reminder", index_html)
        self.assertIn("daily_summary_reminder", index_html)
        self.assertIn("baby_growth_update_reminder", index_html)
        self.assertIn("reportNotification", app_js)
        self.assertIn("/api/notifications/report", app_js)
        self.assertIn("X-Web-Data-Ws-Token", app_js)

    def _create_test_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute(
                """
                CREATE TABLE test_items (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    quantity INTEGER NOT NULL
                )
                """
            )


if __name__ == "__main__":
    unittest.main()
