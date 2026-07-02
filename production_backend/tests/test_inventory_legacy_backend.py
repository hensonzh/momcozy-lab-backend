from pathlib import Path

from production_backend.scripts.inventory_legacy_backend import (
    parse_data_store_tables,
    parse_route_file,
    render_markdown,
)


def test_parse_route_file_extracts_contract_risk_flags(tmp_path: Path) -> None:
    route_file = tmp_path / "routes.py"
    route_file.write_text(
        '''
from fastapi import APIRouter, Request

from ..services import data_store

router = APIRouter()


@router.post("/v1/things")
async def create_thing(request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    try:
        data_store.save_thing(user_id)
    except Exception as exc:
        raise RuntimeError(str(exc))
''',
        encoding="utf-8",
    )

    routes = parse_route_file(route_file, repo_root=tmp_path)

    assert len(routes) == 1
    assert routes[0].methods == ("POST",)
    assert routes[0].path == "/v1/things"
    assert routes[0].data_store_calls == ("save_thing",)
    assert "request_supplied_user_id" in routes[0].risk_flags
    assert "direct_data_store_call" in routes[0].risk_flags
    assert "raw_exception_message" in routes[0].risk_flags


def test_parse_data_store_tables_flags_missing_owner_scope(tmp_path: Path) -> None:
    data_store = tmp_path / "data_store.py"
    data_store.write_text(
        """
def init_db(conn):
    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS uploaded_file (
            file_id TEXT PRIMARY KEY,
            path TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS feeding_log (
            feeding_id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        '''
    )
""",
        encoding="utf-8",
    )

    tables = parse_data_store_tables(data_store, repo_root=tmp_path)
    notes_by_table = {table.name: table.notes for table in tables}

    assert "file ownership missing" in notes_by_table["uploaded_file"]
    assert "no owner scope column" in notes_by_table["uploaded_file"]
    assert "legacy user_id owner scope" in notes_by_table["feeding_log"]


def test_render_markdown_includes_summary(tmp_path: Path) -> None:
    route_file = tmp_path / "routes.py"
    route_file.write_text(
        '''
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}
''',
        encoding="utf-8",
    )

    inventory_routes = tuple(parse_route_file(route_file, repo_root=tmp_path))
    markdown = render_markdown(type("InventoryStub", (), {"routes": inventory_routes, "tables": (), "alter_table_lines": ()})())

    assert "# Legacy Backend Inventory" in markdown
    assert "- Routes: 1" in markdown
    assert "`/health`" in markdown
