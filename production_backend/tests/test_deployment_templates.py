from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_BACKEND = ROOT / "production_backend"
COMPOSE_LOCAL_ENV = PRODUCTION_BACKEND / "env" / "compose.local.env.example"
COMPOSE_TEST_ENV = PRODUCTION_BACKEND / "env" / "compose.test.env.example"
COMPOSE_PROD_ENV = PRODUCTION_BACKEND / "env" / "compose.prod.env.example"
LOCAL_COMPOSE = PRODUCTION_BACKEND / "docker-compose.local.yml"
TEST_COMPOSE = PRODUCTION_BACKEND / "docker-compose.test.yml"
PROD_COMPOSE = PRODUCTION_BACKEND / "docker-compose.prod.yml"
NGINX_CONFIG = PRODUCTION_BACKEND / "deploy" / "nginx" / "momcozy-api.conf"


def test_dockerfile_runs_isolated_production_backend() -> None:
    dockerfile = (PRODUCTION_BACKEND / "Dockerfile").read_text()

    assert "python:3.13-slim" in dockerfile
    assert "production_backend/requirements.txt" in dockerfile
    assert "production_backend.app.main:app" in dockerfile
    assert "momcozy" + "_agent" not in dockerfile


def test_nginx_proxy_keeps_api_private_and_supports_streaming_transports() -> None:
    config = NGINX_CONFIG.read_text()

    assert "server 127.0.0.1:8000;" in config
    assert "server_name lute-momcozylab.luteos.cloud;" in config
    assert "listen 8443 ssl default_server;" in config
    assert "listen 8443 ssl http2;" in config
    assert "listen 80" not in config
    assert "listen 443" not in config
    assert "return 444;" in config
    assert "client_max_body_size 16m;" in config
    assert "ssl_certificate /etc/nginx/tls/momcozy-api/fullchain.pem;" in config
    assert "ssl_certificate_key /etc/nginx/tls/momcozy-api/privkey.pem;" in config
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "ssl_session_tickets off;" in config
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in config
    assert "proxy_set_header X-Request-ID $request_id;" in config
    assert "location = /v1/realtime-voice-session" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' in config
    assert "proxy_buffering off;" in config
    assert "proxy_read_timeout 3600s;" in config


def test_nginx_serves_android_download_artifacts_without_api_proxying() -> None:
    config = NGINX_CONFIG.read_text()

    assert "location /app/" in config
    assert "alias /var/www/momcozy/android-apk/;" in config
    assert "index index.html;" in config
    assert "autoindex off;" in config
    assert "location ~ ^/app/releases/" in config
    assert "alias /var/www/momcozy/android-apk/releases/$apk_file;" in config
    assert "application/vnd.android.package-archive" in config
    assert "Content-Disposition" in config
    assert "attachment; filename=\"$apk_file\"" in config
    assert "X-Content-Type-Options nosniff always;" in config


def test_compose_uses_local_infra_service_names_not_localhost() -> None:
    compose = LOCAL_COMPOSE.read_text()
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.local.env}" in compose
    assert "postgresql+asyncpg://momcozy:momcozy@postgres:5432/momcozy" in env
    assert "redis://redis:6379/0" in env
    assert "localhost" not in env
    assert "127.0.0.1" not in env


def test_compose_env_keeps_object_storage_switchable_by_environment() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_LOCAL_ROOT=" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-local" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000" in env
    assert "OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin" in env
    assert "OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin" in env
    assert "PRODUCT_ASSET_MANIFEST_PATH=/workspace/production_backend/assets/product-assets.manifest.json" in env
    assert "PRODUCT_ASSET_LOCAL_ROOT=" in env


def test_compose_environment_profile_examples_exist() -> None:
    env_dir = PRODUCTION_BACKEND / "env"

    assert (env_dir / "compose.local.env.example").exists()
    assert (env_dir / "compose.test.env.example").exists()
    assert (env_dir / "compose.prod.env.example").exists()
    assert not (env_dir / "local.env.example").exists()
    assert not (env_dir / "staging.env.example").exists()
    assert not (env_dir / "production.env.example").exists()
    assert "APP_ENV=production" in (env_dir / "compose.prod.env.example").read_text()
    assert "OBJECT_STORAGE_PROVIDER=oss" in (env_dir / "compose.prod.env.example").read_text()


def test_compose_environment_profiles_use_current_openai_model_defaults() -> None:
    for env_path in (COMPOSE_LOCAL_ENV, COMPOSE_TEST_ENV, COMPOSE_PROD_ENV):
        env = env_path.read_text()

        assert "OPENAI_MODEL=gpt-5.6-terra" in env
        assert "AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano" in env
        assert "AGENT_FACT_EXTRACTION_MODEL=gpt-5.4-nano" in env
        assert "AGENT_FACT_EXTRACTION_VERSION=turn-fact-extractor-v2" in env
        assert "AGENT_FACT_WORKER_CONCURRENCY=2" in env
        assert "AGENT_FACT_WORKER_BATCH_LIMIT=10" in env
        assert "AGENT_FACT_WORKER_IDLE_SECONDS=0.5" in env
        assert "AGENT_FACT_WORKER_LEASE_SECONDS=30" in env
        assert "AGENT_FACT_WORKER_MAX_ATTEMPTS=3" in env


def test_makefile_infra_checks_use_project_python_environment() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "COMPOSE_ENV_FILE ?= production_backend/env/compose.local.env" in makefile
    assert "COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(COMPOSE_ENV_FILE))" in makefile
    assert "docker-compose.local.yml" in makefile
    assert "PYTHON ?= production_backend/.venv/bin/python" in makefile
    assert "BACKEND_BUILD_FLAGS ?=" in makefile
    assert "backend-build:" in makefile
    assert "$(COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker" in makefile
    assert "$(MAKE) backend-build COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)" in makefile
    assert "$(COMPOSE) up -d postgres redis minio minio-init" in makefile
    assert "$(COMPOSE) --profile tools run --rm migrate" in makefile
    assert "$(COMPOSE) --profile workers up -d --force-recreate api agent-worker outbox-worker memory-worker" in makefile
    assert "$(PYTHON) production_backend/scripts/check_database_profile.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_redis_runtime_controls.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_object_storage_profile.py" in makefile
    assert "$(PYTHON) production_backend/scripts/check_product_asset_storage.py" in makefile
    assert "backend-productization-status:" in makefile
    assert "$(PYTHON) production_backend/scripts/check_productization_status.py" in makefile
    assert "backend-smoke:" in makefile
    assert "backend-test-smoke:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)" in makefile
    assert "backend-prod-readiness:" in makefile
    assert "$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)" in makefile
    assert "backend-worker-backlog:" in makefile
    assert "$(PYTHON) production_backend/scripts/inspect_worker_backlog.py" in makefile
    assert "backend-agent-recover-stuck-runs:" in makefile
    assert "$(PYTHON) production_backend/scripts/recover_stuck_agent_runs.py" in makefile


def test_docker_context_excludes_product_asset_blobs_from_worker_images() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text()

    assert "production_backend/fixtures/product_assets" in dockerignore


def test_compose_env_declares_disabled_agent_worker_controls() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "AGENT_RUNTIME_WORKER_ENABLED=false" in env
    assert "AGENT_RUNTIME_WORKER_BATCH_LIMIT=10" in env
    assert "AGENT_RUNTIME_WORKER_IDLE_SECONDS=0.1" in env
    assert "AGENT_RUNTIME_INTERRUPT_RUNNING_OLDER_THAN_SECONDS=900" in env
    assert "OPENAI_API_KEY=" in env
    assert "OPENAI_MODEL=gpt-5.6-terra" in env
    assert "OPENAI_REASONING_EFFORT=low" in env
    assert "OPENAI_RESPONSES_STORE=false" in env
    assert "OPENAI_AGENT_USE_RESPONSES=true" in env
    assert "AGENT_QUICK_REPLY_MODEL=gpt-5.4-nano" in env
    assert "VOICE_PROVIDER=disabled" in env
    assert "VOICE_API_KEY=" in env
    assert "VOICE_BASE_URL=wss://openspeech.bytedance.com/api/v3/tts/bidirection" in env
    assert "VOICE_TRANSCRIBE_MODEL=" in env
    assert "VOICE_TTS_RESOURCE_ID=seed-tts-2.0" in env
    assert "VOICE_TTS_VOICE_TYPE=saturn_zh_female_qingyingduoduo_cs_tob" in env
    assert "VOICE_TTS_AUDIO_FORMAT=pcm" in env
    assert "VOICE_TTS_SAMPLE_RATE=24000" in env
    assert "VOICE_TTS_SPEED_RATIO=1.1" in env
    assert "VOICE_TTS_FIRST_CHUNK_TIMEOUT_SECONDS=20" in env
    assert "VOICE_REALTIME_MODEL=" in env
    assert "VOICE_REQUEST_TIMEOUT_SECONDS=30" in env
    assert "VISION_PROVIDER=disabled" in env


def test_production_compose_env_declares_doubao_voice_provider() -> None:
    env = COMPOSE_PROD_ENV.read_text()

    assert "VOICE_PROVIDER=doubao" in env
    assert "VOICE_API_KEY=${VOICE_API_KEY}" in env
    assert "VOICE_BASE_URL=wss://openspeech.bytedance.com/api/v3/tts/bidirection" in env


def test_compose_env_declares_disabled_outbox_worker_controls() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "OUTBOX_WORKER_ENABLED=false" in env
    assert "OUTBOX_WORKER_IDLE_SECONDS=2" in env
    assert "OUTBOX_WORKER_LEASE_SECONDS=60" in env


def test_compose_env_declares_active_session_auth_gate() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "AUTH_REQUIRE_ACTIVE_SESSION=false" in env


def test_compose_exposes_minio_as_default_local_object_storage() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "minio:" in compose
    minio_section = compose.split("  minio:", maxsplit=1)[1].split("\n  minio-init:", maxsplit=1)[0]
    assert "profiles:" not in minio_section
    assert "server /data --address" in compose
    assert "9000:9000" in compose
    assert "minio-init:" in compose
    assert "mc mb --ignore-existing local/momcozy-local" in compose


def test_compose_exposes_agent_worker_as_optional_worker_profile() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "agent-worker:" in compose
    assert "python -m production_backend.scripts.run_agent_worker" in compose
    assert "workers" in compose


def test_compose_exposes_outbox_worker_as_optional_worker_profile() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "outbox-worker:" in compose
    assert "python -m production_backend.scripts.run_outbox_worker" in compose
    assert "workers" in compose


def test_compose_exposes_memory_worker_as_optional_worker_profile() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "memory-worker:" in compose
    assert "python -m production_backend.scripts.run_memory_consolidation" in compose
    memory_worker_section = compose.split("memory-worker:", maxsplit=1)[1].split("\n  postgres:", maxsplit=1)[0]
    assert "AGENT_MEMORY_CONSOLIDATION_ENABLED: \"true\"" in memory_worker_section
    assert "redis:" not in memory_worker_section


def test_generic_outbox_worker_has_no_agent_stream_redis_dependency() -> None:
    compose = LOCAL_COMPOSE.read_text()
    outbox_worker_section = compose.split("outbox-worker:", maxsplit=1)[1].split("\n  postgres:", maxsplit=1)[0]

    assert "redis:" not in outbox_worker_section
    assert "postgres:" in outbox_worker_section
    assert "minio-init:" in outbox_worker_section


def test_production_compose_only_starts_application_processes() -> None:
    compose = PROD_COMPOSE.read_text()

    assert "api:" in compose
    assert "migrate:" in compose
    assert "agent-worker:" in compose
    assert "outbox-worker:" in compose
    assert "memory-worker:" in compose
    assert "\n  postgres:" not in compose
    assert "\n  redis:" not in compose
    assert "\n  minio:" not in compose
    assert "\n  minio-init:" not in compose
    assert "postgres:16" not in compose
    assert "redis:7" not in compose
    assert "minio/minio" not in compose


def test_production_compose_uses_production_env_and_safe_api_bind() -> None:
    compose = PROD_COMPOSE.read_text()
    env = COMPOSE_PROD_ENV.read_text()

    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.prod.env}" in compose
    assert "${MOMCOZY_BACKEND_IMAGE:-momcozy-production-backend:latest}" in compose
    assert "${MOMCOZY_API_BIND:-127.0.0.1:8000}:8000" in compose
    assert "python -m alembic -c production_backend/alembic.ini upgrade head" in compose
    assert "python -m production_backend.scripts.run_agent_worker" in compose
    assert "python -m production_backend.scripts.run_outbox_worker" in compose
    assert "python -m production_backend.scripts.run_memory_consolidation" in compose
    assert "restart: unless-stopped" in compose
    assert "stop_grace_period: 60s" in compose
    assert "APP_ENV=production" in env
    assert "OBJECT_STORAGE_PROVIDER=oss" in env
    assert "TRUSTED_HOSTS=lute-momcozylab.luteos.cloud" in env
    assert "OUTBOX_WORKER_ENABLED=true" in env


def test_server_test_compose_starts_containerized_infrastructure_without_publishing_it() -> None:
    compose = TEST_COMPOSE.read_text()

    assert "postgres:" in compose
    assert "redis:" in compose
    assert "minio:" in compose
    assert "minio-init:" in compose
    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "postgres_test_data:" in compose
    assert "redis_test_data:" in compose
    assert "minio_test_data:" in compose
    assert "5432:5432" not in compose
    assert "6379:6379" not in compose
    assert "9000:9000" not in compose
    assert "  postgres:\n    image: postgres:16\n    restart: unless-stopped" in compose
    assert "  redis:\n    image: redis:7\n    restart: unless-stopped" in compose
    assert "  minio:\n    image: minio/minio:latest\n    restart: unless-stopped" in compose


def test_server_test_compose_uses_test_env_and_safe_api_bind() -> None:
    compose = TEST_COMPOSE.read_text()
    env = COMPOSE_TEST_ENV.read_text()

    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.test.env}" in compose
    assert "${MOMCOZY_TEST_API_BIND:-127.0.0.1:8001}:8000" in compose
    assert "mc mb --ignore-existing test/momcozy-test" in compose
    assert "APP_ENV=test" in env
    assert "postgresql+asyncpg://momcozy_test:momcozy_test@postgres:5432/momcozy_test" in env
    assert "REDIS_URL=redis://redis:6379/0" in env
    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-test" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000" in env
    assert "OUTBOX_WORKER_ENABLED=true" in env


def test_makefile_exposes_production_compose_release_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "PROD_COMPOSE_ENV_FILE ?= production_backend/env/compose.prod.env" in makefile
    assert "docker-compose.prod.yml" in makefile
    assert "backend-prod-build:" in makefile
    assert "$(PROD_COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker" in makefile
    assert "backend-prod-migrate:" in makefile
    assert "$(PROD_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate" in makefile
    assert "$(PROD_COMPOSE) --profile tools run --rm migrate" in makefile
    assert "backend-prod-up:" in makefile
    assert "$(PROD_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker" in makefile
    assert "backend-prod-services:" in makefile
    assert "backend-prod-logs:" in makefile


def test_makefile_exposes_server_test_compose_targets() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "TEST_COMPOSE_ENV_FILE ?= production_backend/env/compose.test.env" in makefile
    assert "docker-compose.test.yml" in makefile
    assert "backend-test-build:" in makefile
    assert "$(TEST_COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker" in makefile
    assert "backend-test-migrate:" in makefile
    assert "$(TEST_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate" in makefile
    assert "$(TEST_COMPOSE) --profile tools run --rm migrate" in makefile
    assert "backend-test-up:" in makefile
    assert "$(MAKE) backend-test-build TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)" in makefile
    assert "$(TEST_COMPOSE) up -d postgres redis minio minio-init" in makefile
    assert "$(TEST_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker" in makefile
    assert "backend-test-services:" in makefile
    assert "backend-test-logs:" in makefile
