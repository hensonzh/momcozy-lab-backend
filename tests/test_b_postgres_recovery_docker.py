"""Real Docker drill with synthetic B-shaped data; never touches a deployed stack."""

import os
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts.b_postgres_recovery import DATABASES, _drill, _dump

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.getenv("RUN_B_PG_RECOVERY") != "1", reason="explicit Docker integration opt-in")
def test_two_database_dump_and_isolated_restore() -> None:
    name = f"momcozy-b-pg-ci-{os.getpid()}"
    command = [
        "docker", "run", "-d", "--rm", "--name", name, "--network", "none",
        "-e", "POSTGRES_USER=synthetic_admin", "-e", "POSTGRES_PASSWORD=synthetic_admin_password",
        "-e", "POSTGRES_DB=postgres",
        "-e", "MOMCOZY_PRODUCT_POSTGRES_DB=momcozy_lab_backend_uat",
        "-e", "MOMCOZY_PRODUCT_POSTGRES_USER=momcozy_lab_backend_uat",
        "-e", "MOMCOZY_PRODUCT_POSTGRES_PASSWORD=synthetic_product_password",
        "-e", "MOMCOZY_AGENT_POSTGRES_DB=momcozy_lab_agent_uat",
        "-e", "MOMCOZY_AGENT_POSTGRES_USER=momcozy_lab_agent_uat",
        "-e", "MOMCOZY_AGENT_POSTGRES_PASSWORD=synthetic_agent_password",
        "-v", f"{ROOT / 'deploy/us-east-uat/init-postgres.sh'}:/docker-entrypoint-initdb.d/10-init-momcozy-databases.sh:ro",
        "postgres:16@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54",
    ]
    subprocess.run(command, capture_output=True, check=True)
    try:
        for _ in range(60):
            result = subprocess.run(
                ["docker", "exec", name, "psql", "-U", "synthetic_admin", "-d", "postgres", "-Atqc",
                 "SELECT count(*) FROM pg_database WHERE datname IN ('momcozy_lab_backend_uat','momcozy_lab_agent_uat')"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip() == "2":
                break
            time.sleep(1)
        else:
            pytest.fail("synthetic B databases were not initialized")
        with TemporaryDirectory(prefix="momcozy-b-pg-recovery-") as directory:
            for database in DATABASES:
                subprocess.run(
                    ["docker", "exec", name, "psql", "-U", "synthetic_admin", "-d", database,
                     "-v", "ON_ERROR_STOP=1", "-c",
                     "CREATE TABLE alembic_version (version_num varchar(32) NOT NULL); "
                     "INSERT INTO alembic_version VALUES ('synthetic_rev'); "
                     "CREATE TABLE recovery_canary (id int NOT NULL); INSERT INTO recovery_canary VALUES (42);"],
                    capture_output=True, check=True,
                )
                dump = Path(directory) / f"{database}.dump"
                _dump(name, database, dump)
                _drill(database, dump, "synthetic_rev")

    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, check=False)
