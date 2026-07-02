from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "production_backend" / "docs" / "backend-refactor-inventory.generated.md"

ROUTE_FILES = (
    "src/momcozy_agent/server.py",
    "src/momcozy_agent/api_app.py",
    "src/momcozy_agent/api/routes.py",
    "src/momcozy_agent/api/chat_ws_bridge.py",
    "src/momcozy_agent/api/vision_stream.py",
)
DATA_STORE_FILE = "src/momcozy_agent/services/data_store.py"

HTTP_METHOD_BY_DECORATOR = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "patch": "PATCH",
    "delete": "DELETE",
    "head": "HEAD",
    "options": "OPTIONS",
}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE)
DATA_STORE_CALL_RE = re.compile(r"\bdata_store\.([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class RouteRecord:
    methods: tuple[str, ...]
    path: str
    handler: str
    source: str
    auth_signals: tuple[str, ...]
    data_store_calls: tuple[str, ...]
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class TableRecord:
    name: str
    source: str
    columns: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Inventory:
    routes: tuple[RouteRecord, ...]
    tables: tuple[TableRecord, ...]
    alter_table_lines: tuple[str, ...]


def build_inventory(repo_root: Path = REPO_ROOT) -> Inventory:
    routes: list[RouteRecord] = []
    for relative_path in ROUTE_FILES:
        path = repo_root / relative_path
        if path.exists():
            routes.extend(parse_route_file(path, repo_root=repo_root))

    data_store_path = repo_root / DATA_STORE_FILE
    tables: tuple[TableRecord, ...] = ()
    alter_table_lines: tuple[str, ...] = ()
    if data_store_path.exists():
        tables = tuple(parse_data_store_tables(data_store_path, repo_root=repo_root))
        alter_table_lines = tuple(find_alter_table_lines(data_store_path, repo_root=repo_root))

    return Inventory(routes=tuple(routes), tables=tables, alter_table_lines=alter_table_lines)


def parse_route_file(path: Path, *, repo_root: Path) -> list[RouteRecord]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text, filename=str(path))
    records: list[RouteRecord] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue

        function_source = _source_for_node(lines, node)
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue
            methods, route_path = route
            records.append(
                RouteRecord(
                    methods=tuple(methods),
                    path=route_path,
                    handler=node.name,
                    source=_relative_source(path, repo_root, node.lineno),
                    auth_signals=tuple(_auth_signals(function_source)),
                    data_store_calls=tuple(sorted(set(DATA_STORE_CALL_RE.findall(function_source)))),
                    risk_flags=tuple(_risk_flags(methods, function_source)),
                )
            )

    return sorted(records, key=lambda item: (item.path, item.methods, item.source))


def parse_data_store_tables(path: Path, *, repo_root: Path) -> list[TableRecord]:
    text = path.read_text(encoding="utf-8")
    tables: list[TableRecord] = []

    for match in CREATE_TABLE_RE.finditer(text):
        table_name = match.group(1)
        block = _extract_parenthesized_block(text, match.end() - 1)
        columns = tuple(_extract_column_names(block))
        line_number = text[: match.start()].count("\n") + 1
        tables.append(
            TableRecord(
                name=table_name,
                source=_relative_source(path, repo_root, line_number),
                columns=columns,
                notes=tuple(_table_notes(table_name, columns)),
            )
        )

    return sorted(tables, key=lambda item: item.name)


def find_alter_table_lines(path: Path, *, repo_root: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    results: list[str] = []
    for index, line in enumerate(lines, start=1):
        if "ALTER TABLE" in line.upper():
            results.append(f"{_relative_source(path, repo_root, index)}: {line.strip()}")
    return results


def render_markdown(inventory: Inventory) -> str:
    route_risk_count = sum(1 for route in inventory.routes if route.risk_flags)
    routes_with_data_store = sum(1 for route in inventory.routes if route.data_store_calls)
    tables_without_owner = sum(1 for table in inventory.tables if "no owner scope column" in table.notes)

    lines = [
        "# Legacy Backend Inventory",
        "",
        "Generated by `production_backend/scripts/inventory_legacy_backend.py`.",
        "",
        "## Summary",
        "",
        f"- Routes: {len(inventory.routes)}",
        f"- Routes with direct `data_store` calls: {routes_with_data_store}",
        f"- Routes with migration risk flags: {route_risk_count}",
        f"- SQLite tables: {len(inventory.tables)}",
        f"- Tables without an owner-scope column: {tables_without_owner}",
        f"- Inline `ALTER TABLE` statements: {len(inventory.alter_table_lines)}",
        "",
        "## Route Surface",
        "",
        "| Methods | Path | Handler | Source | Auth | Data Store Calls | Risk Flags |",
        "|---|---|---|---|---|---|---|",
    ]

    for route in inventory.routes:
        lines.append(
            "| {methods} | `{path}` | `{handler}` | `{source}` | {auth} | {calls} | {risks} |".format(
                methods=", ".join(route.methods),
                path=_escape_pipe(route.path),
                handler=route.handler,
                source=route.source,
                auth=_join_or_dash(route.auth_signals),
                calls=_join_or_dash(f"`{item}`" for item in route.data_store_calls),
                risks=_join_or_dash(route.risk_flags),
            )
        )

    lines.extend(
        [
            "",
            "## SQLite Tables",
            "",
            "| Table | Source | Owner Scope Columns | Notes |",
            "|---|---|---|---|",
        ]
    )

    for table in inventory.tables:
        owner_columns = [column for column in table.columns if column in {"user_id", "owner_user_id", "tenant_id"}]
        lines.append(
            "| `{name}` | `{source}` | {owners} | {notes} |".format(
                name=table.name,
                source=table.source,
                owners=_join_or_dash(f"`{item}`" for item in owner_columns),
                notes=_join_or_dash(table.notes),
            )
        )

    lines.extend(["", "## Inline Schema Mutation", ""])
    if inventory.alter_table_lines:
        lines.extend(f"- `{line}`" for line in inventory.alter_table_lines)
    else:
        lines.append("- None detected.")

    lines.extend(
        [
            "",
            "## Migration Use",
            "",
            "- Treat this file as a generated inventory, not as the target contract.",
            "- Convert each route into a typed operation contract before moving behavior.",
            "- Replace request-supplied `user_id` with `current_user` owner scope.",
            "- Replace SQLite schema mutation with PostgreSQL + Alembic migrations.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_inventory(output_path: Path, *, repo_root: Path = REPO_ROOT) -> None:
    inventory = build_inventory(repo_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_markdown(inventory), encoding="utf-8")


def _route_from_decorator(decorator: ast.expr) -> tuple[list[str], str] | None:
    if not isinstance(decorator, ast.Call):
        return None

    dotted_name = _dotted_name(decorator.func)
    decorator_name = dotted_name.rsplit(".", 1)[-1]

    if decorator_name == "api_route":
        route_path = _string_literal(decorator.args[0]) if decorator.args else "<dynamic>"
        methods = _methods_from_keywords(decorator.keywords) or ["GET"]
        return sorted(methods), route_path

    if decorator_name == "websocket":
        route_path = _string_literal(decorator.args[0]) if decorator.args else "<dynamic>"
        return ["WEBSOCKET"], route_path

    if decorator_name in HTTP_METHOD_BY_DECORATOR:
        route_path = _string_literal(decorator.args[0]) if decorator.args else "<dynamic>"
        return [HTTP_METHOD_BY_DECORATOR[decorator_name]], route_path

    return None


def _methods_from_keywords(keywords: Iterable[ast.keyword]) -> list[str]:
    for keyword in keywords:
        if keyword.arg == "methods":
            return [item.upper() for item in _string_list(keyword.value)]
    return []


def _auth_signals(source: str) -> list[str]:
    signals: list[str] = []
    if "verify_api_key(" in source or "Depends(verify_api_key" in source:
        signals.append("static_bearer_api_key")
    if "_verify_websocket_api_key(" in source or "_verify_token(" in source:
        signals.append("websocket_static_token")
    if "query_params.get(\"token\"" in source or "query_params.get('token'" in source:
        signals.append("query_token")
    return signals


def _risk_flags(methods: Iterable[str], source: str) -> list[str]:
    flags: list[str] = []
    method_set = set(methods)
    if method_set & WRITE_METHODS:
        flags.append("write_or_side_effect_candidate")
    if "user_id" in source and any(marker in source for marker in ("query_params", "body.get", "payload.get", "request.json")):
        flags.append("request_supplied_user_id")
    if "data_store." in source:
        flags.append("direct_data_store_call")
    if "str(exc)" in source or "f\"" in source and "{exc}" in source:
        flags.append("raw_exception_message")
    if "query_params.get(\"token\"" in source or "query_params.get('token'" in source:
        flags.append("query_token_auth")
    if "UploadFile" in source or "File(" in source:
        flags.append("file_upload")
    return flags


def _extract_parenthesized_block(text: str, open_paren_index: int) -> str:
    depth = 0
    for index in range(open_paren_index, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_index + 1 : index]
    return ""


def _extract_column_names(block: str) -> list[str]:
    columns: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        first_token = line.split(maxsplit=1)[0].strip('"`[]')
        if first_token.upper() in {"PRIMARY", "FOREIGN", "CONSTRAINT", "UNIQUE", "CHECK"}:
            continue
        columns.append(first_token)
    return columns


def _table_notes(table_name: str, columns: tuple[str, ...]) -> list[str]:
    column_set = set(columns)
    notes: list[str] = []
    if not column_set & {"user_id", "owner_user_id", "tenant_id"}:
        notes.append("no owner scope column")
    elif "user_id" in column_set and "owner_user_id" not in column_set:
        notes.append("legacy user_id owner scope")
    if table_name == "uploaded_file" and "owner_user_id" not in column_set:
        notes.append("file ownership missing")
    if "created_at" not in column_set:
        notes.append("missing created_at")
    return notes


def _source_for_node(lines: list[str], node: ast.AST) -> str:
    lineno = int(getattr(node, "lineno", 1))
    end_lineno = int(getattr(node, "end_lineno", lineno))
    return "\n".join(lines[lineno - 1 : end_lineno])


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _string_literal(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "<dynamic>"


def _string_list(node: ast.AST) -> list[str]:
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return [_string_literal(item) for item in node.elts if _string_literal(item) != "<dynamic>"]
    return []


def _relative_source(path: Path, repo_root: Path, line_number: int) -> str:
    try:
        relative_path = path.relative_to(repo_root)
    except ValueError:
        relative_path = path
    return f"{relative_path}:{line_number}"


def _join_or_dash(values: Iterable[str]) -> str:
    materialized = list(values)
    return ", ".join(materialized) if materialized else "-"


def _escape_pipe(value: str) -> str:
    return value.replace("|", "\\|")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory the legacy MomCozy backend before production refactor.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--write", type=Path, nargs="?", const=DEFAULT_OUTPUT)
    args = parser.parse_args()

    inventory = build_inventory(args.repo_root)
    markdown = render_markdown(inventory)

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.write}")
    else:
        print(markdown, end="")


if __name__ == "__main__":
    main()
