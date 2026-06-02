from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
WEB_DATA_ROOT = ROOT / "web_data"
DEFAULT_DB_PATH = ROOT / "data" / "milk_management.db"
DEFAULT_FILE_ROOT = ROOT / "data" / "milk_process" / "configs"
SESSION_COOKIE = "web_data_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
CORE_TABLES = ["user_profile", "infant_profile", "feeding_log", "pumping_log", "milk_plan", "calendar"]
WS_TOKEN_ENV = "WEB_DATA_WS_TOKEN"


def create_app(db_path: str | Path | None = None, file_root: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Momcozy Web Data Admin")
    app.state.db_path = Path(db_path or DEFAULT_DB_PATH)
    app.state.file_root = Path(file_root or DEFAULT_FILE_ROOT)
    app.state.session_secret = _session_secret()
    app.state.ws_token = _websocket_token()
    app.state.ws_manager = WebDataConnectionManager()
    _ensure_database(app.state.db_path)
    app.state.file_root.mkdir(parents=True, exist_ok=True)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DATA_ROOT / "index.html", media_type="text/html; charset=utf-8")

    @app.post("/api/login")
    async def login(request: Request, response: Response) -> dict[str, Any]:
        payload = await request.json()
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        if username != DEFAULT_USERNAME or password != DEFAULT_PASSWORD:
            raise HTTPException(status_code=401, detail={"message": "invalid username or password"})
        response.set_cookie(
            SESSION_COOKIE,
            _sign_session({"username": username, "iat": int(time.time())}, request.app.state.session_secret),
            httponly=True,
            samesite="lax",
            max_age=SESSION_TTL_SECONDS,
        )
        return {"authenticated": True, "username": username}

    @app.post("/api/logout")
    async def logout(response: Response, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        response.delete_cookie(SESSION_COOKIE)
        return {"authenticated": False}

    @app.get("/api/session")
    async def session(request: Request) -> dict[str, Any]:
        user = optional_session(request)
        if user is None:
            return {"authenticated": False}
        return {"authenticated": True, "username": user["username"]}

    @app.post("/api/notifications/report")
    async def report_notification(request: Request) -> dict[str, Any]:
        if not _verify_request_token(request):
            raise HTTPException(status_code=401, detail={"message": "invalid websocket token"})

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail={"message": "notification payload must be a JSON object"})

        reminder_type = str(payload.get("reminder_type") or payload.get("type") or "").strip()
        if not reminder_type:
            raise HTTPException(status_code=400, detail={"message": "reminder_type is required"})
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise HTTPException(status_code=400, detail={"message": "user_id is required"})

        notification = {
            "user_id": user_id,
            "reminder_type": reminder_type,
            "title": str(payload.get("title", "")),
            "message": str(payload.get("message", "")),
            "data": payload.get("data", {}),
            "reported_at": int(time.time()),
        }
        delivered = await request.app.state.ws_manager.send_to_user(
            user_id,
            {"type": "notification.reported", "payload": notification}
        )
        return {"reported": True, "delivered": delivered, "notification": notification}

    @app.get("/api/db/tables")
    async def list_tables(request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        with _connect(request.app.state.db_path) as conn:
            rows = conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        tables = [str(row["name"]) for row in rows]
        return {"tables": tables, "core_tables": [table for table in CORE_TABLES if table in tables]}

    @app.get("/api/db/tables/{table}/schema")
    async def table_schema(table: str, request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        return _schema_response(request.app.state.db_path, table)

    @app.get("/api/db/tables/{table}/rows")
    async def list_rows(
        table: str,
        request: Request,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        field: str = "",
        _: dict[str, Any] = Depends(require_session),
    ) -> dict[str, Any]:
        page = max(1, int(page or 1))
        page_size = min(100, max(1, int(page_size or 20)))
        offset = (page - 1) * page_size
        with _connect(request.app.state.db_path) as conn:
            schema = _table_schema(conn, table)
            where_sql, params = _search_clause(schema["columns"], q, field)
            total = conn.execute(f"SELECT COUNT(*) AS count FROM {_quote_ident(table)}{where_sql}", params).fetchone()["count"]
            rows = conn.execute(
                f"SELECT * FROM {_quote_ident(table)}{where_sql} LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        return {
            "table": table,
            "page": page,
            "page_size": page_size,
            "total": int(total),
            "rows": [_row_to_dict(row) for row in rows],
            "schema": schema,
        }

    @app.post("/api/db/tables/{table}/rows")
    async def create_row(
        table: str,
        request: Request,
        _: dict[str, Any] = Depends(require_session),
    ) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(status_code=400, detail={"message": "row payload must be a non-empty object"})
        with _connect(request.app.state.db_path) as conn:
            schema = _table_schema(conn, table)
            columns = [column["name"] for column in schema["columns"]]
            values = {key: payload[key] for key in columns if key in payload}
            if not values:
                raise HTTPException(status_code=400, detail={"message": "payload does not contain table columns"})
            col_sql = ", ".join(_quote_ident(key) for key in values)
            placeholder_sql = ", ".join("?" for _ in values)
            cursor = conn.execute(
                f"INSERT INTO {_quote_ident(table)} ({col_sql}) VALUES ({placeholder_sql})",
                list(values.values()),
            )
            conn.commit()
            row = _fetch_inserted_row(conn, table, schema, values, cursor.lastrowid)
        await request.app.state.ws_manager.broadcast(
            {"type": "db.row.created", "payload": {"table": table, "row": row}}
        )
        return {"created": True, "row": row}

    @app.put("/api/db/tables/{table}/rows/{primary_key}")
    async def update_row(
        table: str,
        primary_key: str,
        request: Request,
        _: dict[str, Any] = Depends(require_session),
    ) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict) or not payload:
            raise HTTPException(status_code=400, detail={"message": "row payload must be a non-empty object"})
        with _connect(request.app.state.db_path) as conn:
            schema = _table_schema(conn, table)
            pk = _primary_key(schema)
            allowed = [column["name"] for column in schema["columns"] if column["name"] != pk]
            values = {key: payload[key] for key in allowed if key in payload}
            if not values:
                raise HTTPException(status_code=400, detail={"message": "payload does not contain editable columns"})
            assignments = ", ".join(f"{_quote_ident(key)} = ?" for key in values)
            cursor = conn.execute(
                f"UPDATE {_quote_ident(table)} SET {assignments} WHERE {_quote_ident(pk)} = ?",
                [*values.values(), primary_key],
            )
            if cursor.rowcount < 1:
                raise HTTPException(status_code=404, detail={"message": "row not found"})
            conn.commit()
            row = _fetch_row(conn, table, pk, primary_key)
        await request.app.state.ws_manager.broadcast(
            {"type": "db.row.updated", "payload": {"table": table, "primary_key": primary_key, "row": row}}
        )
        return {"updated": True, "row": row}

    @app.delete("/api/db/tables/{table}/rows/{primary_key}")
    async def delete_row(
        table: str,
        primary_key: str,
        request: Request,
        _: dict[str, Any] = Depends(require_session),
    ) -> dict[str, Any]:
        with _connect(request.app.state.db_path) as conn:
            schema = _table_schema(conn, table)
            pk = _primary_key(schema)
            cursor = conn.execute(f"DELETE FROM {_quote_ident(table)} WHERE {_quote_ident(pk)} = ?", (primary_key,))
            conn.commit()
        if cursor.rowcount < 1:
            raise HTTPException(status_code=404, detail={"message": "row not found"})
        await request.app.state.ws_manager.broadcast(
            {"type": "db.row.deleted", "payload": {"table": table, "primary_key": primary_key}}
        )
        return {"deleted": True}

    @app.get("/api/files")
    async def list_files(request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        root = request.app.state.file_root
        root.mkdir(parents=True, exist_ok=True)
        files: list[dict[str, Any]] = []
        for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() == ".json"):
            stat = path.stat()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": int(stat.st_mtime),
                    "editable": _is_text_file(path),
                }
            )
        return {"root": str(root), "files": files}

    @app.get("/api/files/content")
    async def file_content(path: str, request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        target = _safe_file_path(request.app.state.file_root, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"message": "file not found"})
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"path": path, "editable": False, "content": ""}
        return {"path": path, "editable": True, "content": content}

    @app.post("/api/files")
    async def create_file(request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        payload = await request.json()
        path = str(payload.get("path", ""))
        content = str(payload.get("content", ""))
        target = _safe_file_path(request.app.state.file_root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise HTTPException(status_code=409, detail={"message": "file already exists"})
        target.write_text(content, encoding="utf-8")
        relative_path = _relative_path(request.app.state.file_root, target)
        await request.app.state.ws_manager.broadcast(
            {"type": "file.created", "payload": {"path": relative_path}}
        )
        return {"created": True, "path": relative_path}

    @app.put("/api/files/content")
    async def update_file(request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        payload = await request.json()
        target = _safe_file_path(request.app.state.file_root, str(payload.get("path", "")))
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"message": "file not found"})
        if not _is_text_file(target):
            raise HTTPException(status_code=400, detail={"message": "binary file cannot be edited"})
        target.write_text(str(payload.get("content", "")), encoding="utf-8")
        relative_path = _relative_path(request.app.state.file_root, target)
        await request.app.state.ws_manager.broadcast(
            {"type": "file.updated", "payload": {"path": relative_path}}
        )
        return {"updated": True, "path": relative_path}

    @app.delete("/api/files")
    async def delete_file(path: str, request: Request, _: dict[str, Any] = Depends(require_session)) -> dict[str, Any]:
        target = _safe_file_path(request.app.state.file_root, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"message": "file not found"})
        relative_path = _relative_path(request.app.state.file_root, target)
        target.unlink()
        await request.app.state.ws_manager.broadcast(
            {"type": "file.deleted", "payload": {"path": relative_path}}
        )
        return {"deleted": True}

    @app.post("/api/files/upload")
    async def upload_file(
        request: Request,
        upload: UploadFile = File(...),
        directory: str = "",
        _: dict[str, Any] = Depends(require_session),
    ) -> dict[str, Any]:
        filename = Path(upload.filename or "").name
        if not filename:
            raise HTTPException(status_code=400, detail={"message": "filename is required"})
        target = _safe_file_path(request.app.state.file_root, f"{directory.rstrip('/')}/{filename}" if directory else filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        body = await upload.read()
        target.write_bytes(body)
        relative_path = _relative_path(request.app.state.file_root, target)
        await request.app.state.ws_manager.broadcast(
            {"type": "file.uploaded", "payload": {"path": relative_path, "size": len(body)}}
        )
        return {"uploaded": True, "path": relative_path, "size": len(body)}

    @app.get("/api/files/download")
    async def download_file(path: str, request: Request, _: dict[str, Any] = Depends(require_session)) -> FileResponse:
        target = _safe_file_path(request.app.state.file_root, path)
        if not target.is_file():
            raise HTTPException(status_code=404, detail={"message": "file not found"})
        return FileResponse(target, filename=target.name)

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        if not _verify_websocket_token(websocket):
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user_id = _websocket_user_id(websocket)
        manager = websocket.app.state.ws_manager
        await manager.connect(websocket, user_id=user_id)
        try:
            await websocket.send_json(
                {
                    "type": "connection.accepted",
                    "payload": {"token_required": bool(websocket.app.state.ws_token), "user_id": user_id},
                }
            )
            while True:
                raw_message = await websocket.receive_text()
                await _handle_websocket_message(websocket, raw_message)
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(websocket)

    app.mount("/static", StaticFiles(directory=str(WEB_DATA_ROOT)), name="web_data_static")
    return app


class WebDataConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._connection_user_ids: dict[WebSocket, str] = {}
        self._user_connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, *, user_id: str = "") -> None:
        await websocket.accept()
        self._connections.add(websocket)
        if user_id:
            self._connection_user_ids[websocket] = user_id
            self._user_connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        user_id = self._connection_user_ids.pop(websocket, "")
        if user_id:
            user_connections = self._user_connections.get(user_id)
            if user_connections is not None:
                user_connections.discard(websocket)
                if not user_connections:
                    self._user_connections.pop(user_id, None)

    async def broadcast(self, message: dict[str, Any]) -> None:
        stale_connections: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.disconnect(websocket)

    async def send_to_user(self, user_id: str, message: dict[str, Any]) -> int:
        delivered = 0
        stale_connections: list[WebSocket] = []
        for websocket in list(self._user_connections.get(user_id, set())):
            try:
                await websocket.send_json(message)
                delivered += 1
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.disconnect(websocket)
        return delivered


async def _handle_websocket_message(websocket: WebSocket, raw_message: str) -> None:
    try:
        message = json.loads(raw_message or "{}")
    except json.JSONDecodeError:
        await websocket.send_json(
            {"type": "error", "payload": {"code": "INVALID_JSON", "message": "message must be valid JSON"}}
        )
        return
    if not isinstance(message, dict):
        await websocket.send_json(
            {"type": "error", "payload": {"code": "INVALID_MESSAGE", "message": "message must be a JSON object"}}
        )
        return

    message_type = str(message.get("type", "")).strip()
    request_id = message.get("request_id")
    if message_type == "ping":
        await websocket.send_json(
            {
                "type": "pong",
                "request_id": request_id,
                "payload": {"server_time": int(time.time())},
            }
        )
        return
    if message_type == "echo":
        await websocket.send_json(
            {"type": "echo", "request_id": request_id, "payload": message.get("payload", {})}
        )
        return
    if message_type in {"subscribe", "unsubscribe"}:
        await websocket.send_json(
            {"type": f"{message_type}.ack", "request_id": request_id, "payload": message.get("payload", {})}
        )
        return

    await websocket.send_json(
        {
            "type": "error",
            "request_id": request_id,
            "payload": {"code": "UNKNOWN_MESSAGE_TYPE", "message": f"unsupported message type: {message_type}"},
        }
    )


def _verify_websocket_token(websocket: WebSocket) -> bool:
    expected = websocket.app.state.ws_token
    if not expected:
        return True

    query_token = (websocket.query_params.get("token") or "").strip()
    if query_token and hmac.compare_digest(query_token, expected):
        return True

    auth_header = websocket.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
        if header_token and hmac.compare_digest(header_token, expected):
            return True

    protocol_header = websocket.headers.get("sec-websocket-protocol") or ""
    for fragment in protocol_header.split(","):
        protocol_token = fragment.strip()
        if protocol_token and hmac.compare_digest(protocol_token, expected):
            return True

    return False


def _websocket_user_id(websocket: WebSocket) -> str:
    query_user_id = (websocket.query_params.get("user_id") or "").strip()
    if query_user_id:
        return query_user_id

    header_user_id = (websocket.headers.get("x-web-data-user-id") or "").strip()
    if header_user_id:
        return header_user_id

    return ""


def _verify_request_token(request: Request) -> bool:
    expected = request.app.state.ws_token
    if not expected:
        return True

    query_token = (request.query_params.get("token") or "").strip()
    if query_token and hmac.compare_digest(query_token, expected):
        return True

    auth_header = request.headers.get("authorization") or ""
    if auth_header.startswith("Bearer "):
        header_token = auth_header[7:].strip()
        if header_token and hmac.compare_digest(header_token, expected):
            return True

    explicit_header = request.headers.get("x-web-data-ws-token") or ""
    if explicit_header.strip() and hmac.compare_digest(explicit_header.strip(), expected):
        return True

    return False


def require_session(request: Request) -> dict[str, Any]:
    data = optional_session(request)
    if data is None:
        raise HTTPException(status_code=401, detail={"message": "login required"})
    return data


def optional_session(request: Request) -> dict[str, Any] | None:
    value = request.cookies.get(SESSION_COOKIE)
    if not value:
        return None
    data = _verify_session(value, request.app.state.session_secret)
    if data is None:
        return None
    return data


def _ensure_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    from momcozy_agent.services import data_store

    data_store.DB_PATH = db_path
    original_connect = data_store._connect  # type: ignore[attr-defined]

    def connect_with_memory_journal() -> sqlite3.Connection:
        conn = sqlite3.connect(data_store.DB_PATH, factory=data_store._ClosingConnection)  # type: ignore[attr-defined]
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=MEMORY")
        return conn

    data_store._connect = connect_with_memory_journal  # type: ignore[attr-defined]
    try:
        data_store.init_db()
    finally:
        data_store._connect = original_connect  # type: ignore[attr-defined]


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=MEMORY")
    return conn


def _schema_response(db_path: Path, table: str) -> dict[str, Any]:
    with _connect(db_path) as conn:
        return _table_schema(conn, table)


def _table_schema(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not _table_exists(conn, table):
        raise HTTPException(status_code=404, detail={"message": "table not found"})
    columns = []
    for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall():
        columns.append(
            {
                "cid": row["cid"],
                "name": row["name"],
                "type": row["type"],
                "notnull": bool(row["notnull"]),
                "default": row["dflt_value"],
                "pk": bool(row["pk"]),
            }
        )
    return {"table": table, "columns": columns, "primary_key": _primary_key({"columns": columns})}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _primary_key(schema: dict[str, Any]) -> str:
    for column in schema["columns"]:
        if column["pk"]:
            return str(column["name"])
    raise HTTPException(status_code=400, detail={"message": "table has no single primary key"})


def _search_clause(columns: list[dict[str, Any]], q: str, field: str = "") -> tuple[str, list[Any]]:
    query = str(q or "").strip()
    if not query:
        return "", []
    field_name = str(field or "").strip()
    if field_name:
        column_names = {str(column["name"]) for column in columns}
        if field_name not in column_names:
            raise HTTPException(status_code=400, detail={"message": "filter field does not exist"})
        return f" WHERE CAST({_quote_ident(field_name)} AS TEXT) LIKE ?", [f"%{query}%"]
    clauses = [f"CAST({_quote_ident(str(column['name']))} AS TEXT) LIKE ?" for column in columns]
    return f" WHERE {' OR '.join(clauses)}", [f"%{query}%"] * len(clauses)


def _fetch_inserted_row(
    conn: sqlite3.Connection,
    table: str,
    schema: dict[str, Any],
    values: dict[str, Any],
    lastrowid: int,
) -> dict[str, Any]:
    pk = _primary_key(schema)
    pk_value = values.get(pk, lastrowid)
    return _fetch_row(conn, table, pk, pk_value)


def _fetch_row(conn: sqlite3.Connection, table: str, pk: str, pk_value: Any) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT * FROM {_quote_ident(table)} WHERE {_quote_ident(pk)} = ?",
        (pk_value,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "row not found"})
    return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _quote_ident(identifier: str) -> str:
    if "\x00" in identifier:
        raise HTTPException(status_code=400, detail={"message": "invalid identifier"})
    return '"' + identifier.replace('"', '""') + '"'


def _safe_file_path(root: Path, raw_path: str) -> Path:
    path_text = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if not path_text:
        raise HTTPException(status_code=400, detail={"message": "path is required"})
    root_resolved = root.resolve()
    target = (root / path_text).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise HTTPException(status_code=400, detail={"message": "path escapes config root"})
    return target


def _relative_path(root: Path, target: Path) -> str:
    return target.resolve().relative_to(root.resolve()).as_posix()


def _is_text_file(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _session_secret() -> bytes:
    configured = os.getenv("WEB_DATA_SESSION_SECRET", "").strip()
    return (configured or "momcozy-web-data-local-secret").encode("utf-8")


def _websocket_token() -> str:
    return os.getenv(WS_TOKEN_ENV, "").strip()


def _sign_session(payload: dict[str, Any], secret: bytes) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    sig = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_session(value: str, secret: bytes) -> dict[str, Any] | None:
    try:
        body, sig = value.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("username") != DEFAULT_USERNAME:
        return None
    if int(time.time()) - int(payload.get("iat", 0)) > SESSION_TTL_SECONDS:
        return None
    return {"username": DEFAULT_USERNAME}


app = create_app()
