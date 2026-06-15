from __future__ import annotations

import os
import runpy
import socket
import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CHAT_HOST = "127.0.0.1"
CHAT_PORT = 8768
WEB_DATA_HOST = "127.0.0.1"
WEB_DATA_PORT = 8081


def main() -> None:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(SRC))

    from momcozy_agent.config import load_project_env

    env_path = ROOT / ".env"
    load_project_env(env_path)
    from momcozy_agent.server import main as run_chat_server

    entry_api_key = _read_env_value(env_path, "ENTRY_API_KEY")
    if entry_api_key:
        os.environ["ENTRY_API_KEY"] = entry_api_key
    CHAT_HOST = os.getenv("CHAT_HOST", "127.0.0.1")
    CHAT_PORT = int(os.getenv("CHAT_PORT", "8768"))
    web_data_host = os.getenv("WEB_DATA_HOST", "127.0.0.1")
    web_data_port = int(os.getenv("WEB_DATA_PORT", "8081"))
    os.environ["MOMCOZY_CHAT_SSE_URL"] = f"http://{CHAT_HOST}:{CHAT_PORT}/api/ag-ui"

    host = os.getenv("ENTRY_HOST", "0.0.0.0")
    port = int(os.getenv("ENTRY_PORT", "8769"))
    if _is_port_open(_connect_host(host), port):
        raise RuntimeError(
            f"Unified API port {host}:{port} is already in use. "
            "Stop the existing service, or set ENTRY_PORT to another port in .env."
        )

    if not (os.getenv("ENTRY_API_KEY") or "").strip():
        print("Warning: ENTRY_API_KEY is not set; /api/ag-ui-ws will reject WebSocket clients.")

    if _auto_seed_status_demo_enabled():
        _seed_status_demo_data()
    _reset_non_status_demo_data_for_dev()

    if not _is_port_open(CHAT_HOST, CHAT_PORT):
        chat_thread = threading.Thread(target=run_chat_server, name="momcozy-chat-sse", daemon=True)
        chat_thread.start()
        _wait_for_port(CHAT_HOST, CHAT_PORT, timeout_seconds=30)

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("uvicorn is not installed. Install dependencies with: py -3.12 -m pip install -r requirements.txt") from exc

    if not _is_port_open(web_data_host, web_data_port):
        web_data_thread = threading.Thread(
            target=_run_web_data_server,
            args=(web_data_host, web_data_port),
            name="web-data-admin",
            daemon=True,
        )
        web_data_thread.start()
        _wait_for_port(web_data_host, web_data_port, timeout_seconds=30)

    print(f"Momcozy unified API: http://{host}:{port}")
    print(f"Momcozy App WebSocket: ws://{host}:{port}/api/ag-ui-ws")
    print(f"Momcozy chat SSE upstream: http://{CHAT_HOST}:{CHAT_PORT}/api/ag-ui")
    print(f"Web data admin: http://{web_data_host}:{web_data_port}")
    uvicorn.run("momcozy_agent.api_app:app", host=host, port=port, reload=False)


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in {"0.0.0.0", "::"} else host


def _read_env_value(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        raw_key, value = line.split("=", 1)
        if raw_key.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value
    return ""


def _run_web_data_server(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("web_data.app:app", host=host, port=port, reload=False)


def _auto_seed_status_demo_enabled() -> bool:
    raw = os.getenv("MOMCOZY_AUTO_SEED_STATUS_DEMO", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _seed_status_demo_data() -> None:
    seed_path = ROOT / "scripts" / "seed_status_demo_data.py"
    namespace = runpy.run_path(str(seed_path))
    seed_main = namespace.get("main")
    if not callable(seed_main):
        raise RuntimeError(f"Seed script {seed_path} does not expose main().")
    seed_main()


def _reset_non_status_demo_data_for_dev() -> None:
    from momcozy_agent.services import data_store

    cleared = data_store.reset_non_status_demo_data_for_dev()
    print(
        "Reset non-status demo data for local startup: "
        f"profile={cleared['profile_onboarding']}, "
        f"birth_prep={cleared['birth_prep_profile']}, "
        f"plans={cleared['generated_plans']}, "
        f"milk_plans={cleared['milk_plans']}, "
        f"pregnancy_diary={cleared['pregnancy_diary']}, "
        f"uploads={cleared['uploaded_files']}, "
        f"device_runtime={cleared['device_runtime']}."
    )


def _wait_for_port(host: str, port: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _is_port_open(host, port):
            return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for chat SSE server on {host}:{port}")


if __name__ == "__main__":
    main()
