from pathlib import Path

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.infrastructure.object_storage import StoredObject
from production_backend.app.modules.assets.models import ProductAsset
from production_backend.app.modules.assets.router import get_product_asset_service


def test_assets_list_exposes_packaged_asset_ids_without_local_paths() -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService()

    response = TestClient(app).get("/v1/assets?limit=10")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item == {
        "id": "asset_test",
        "label": "Air1 guide",
        "domain": "device_guidance",
        "content_type": "image/png",
        "size_bytes": 7,
    }
    assert "path" not in item
    assert "skill" not in item["id"]


def test_asset_detail_serves_allowlisted_asset_by_id(tmp_path: Path) -> None:
    asset_path = tmp_path / "air1.png"
    asset_path.write_bytes(b"pngdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(path=asset_path)

    response = TestClient(app).get("/v1/assets/asset_test")

    assert response.status_code == 200
    assert response.content == b"pngdata"
    assert response.headers["content-type"].startswith("image/png")


def test_asset_detail_serves_local_file_byte_range(tmp_path: Path) -> None:
    asset_path = tmp_path / "air1.png"
    asset_path.write_bytes(b"pngdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(path=asset_path)

    response = TestClient(app).get(
        "/v1/assets/asset_test",
        headers={"Range": "bytes=1-3"},
    )

    assert response.status_code == 206
    assert response.content == b"ngd"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 1-3/7"


def test_asset_detail_serves_manifest_asset_from_object_storage() -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(
        object_key="product-assets/device-guidance/assets/air1/guide.pdf",
    )
    app.state.object_storage = FakeObjectStorage(body=b"pdfdata")

    response = TestClient(app).get("/v1/assets/asset_test")

    assert response.status_code == 200
    assert response.content == b"pdfdata"
    assert response.headers["content-type"].startswith("application/pdf")


def test_asset_head_for_manifest_asset_does_not_read_object_body() -> None:
    storage = FakeObjectStorage(body=b"pdfdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(
        object_key="product-assets/device-guidance/assets/air1/guide.pdf",
    )
    app.state.object_storage = storage

    response = TestClient(app).head("/v1/assets/asset_test")

    assert response.status_code == 200
    assert response.headers["content-length"] == "7"
    assert response.headers["accept-ranges"] == "bytes"
    assert storage.read_keys == []


def test_asset_detail_serves_object_storage_byte_range() -> None:
    storage = FakeObjectStorage(body=b"pdfdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(
        object_key="product-assets/device-guidance/assets/air1/guide.pdf",
    )
    app.state.object_storage = storage

    response = TestClient(app).get(
        "/v1/assets/asset_test",
        headers={"Range": "bytes=1-3"},
    )

    assert response.status_code == 206
    assert response.content == b"dfd"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"] == "bytes 1-3/7"
    assert response.headers["content-length"] == "3"
    assert storage.read_keys == []
    assert storage.read_ranges == [
        ("product-assets/device-guidance/assets/air1/guide.pdf", 1, 3),
    ]


def test_asset_detail_supports_open_and_suffix_byte_ranges() -> None:
    storage = FakeObjectStorage(body=b"pdfdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(
        object_key="product-assets/device-guidance/assets/air1/guide.pdf",
    )
    app.state.object_storage = storage
    client = TestClient(app)

    open_ended = client.get(
        "/v1/assets/asset_test",
        headers={"Range": "bytes=4-"},
    )
    suffix = client.get(
        "/v1/assets/asset_test",
        headers={"Range": "bytes=-2"},
    )

    assert open_ended.status_code == 206
    assert open_ended.content == b"ata"
    assert open_ended.headers["content-range"] == "bytes 4-6/7"
    assert suffix.status_code == 206
    assert suffix.content == b"ta"
    assert suffix.headers["content-range"] == "bytes 5-6/7"
    assert storage.read_ranges == [
        ("product-assets/device-guidance/assets/air1/guide.pdf", 4, 6),
        ("product-assets/device-guidance/assets/air1/guide.pdf", 5, 6),
    ]


def test_asset_detail_rejects_unsatisfiable_range_without_storage_read() -> None:
    storage = FakeObjectStorage(body=b"pdfdata")
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService(
        object_key="product-assets/device-guidance/assets/air1/guide.pdf",
    )
    app.state.object_storage = storage

    response = TestClient(app).get(
        "/v1/assets/asset_test",
        headers={"Range": "bytes=99-100"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */7"
    assert storage.read_keys == []
    assert storage.read_ranges == []


def test_asset_detail_rejects_unknown_asset_id() -> None:
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: FakeProductAssetService()

    response = TestClient(app).get("/v1/assets/../../skills/device-guidance/assets/air1/image.png")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


class FakeProductAssetService:
    def __init__(self, *, path: Path | None = None, object_key: str | None = None) -> None:
        self.asset = ProductAsset(
            id="asset_test",
            label="Air1 guide",
            domain="device_guidance",
            content_type="application/pdf" if object_key else "image/png",
            size_bytes=7,
            path=path,
            object_key=object_key,
        )

    def list_assets(self, *, limit: int = 100):
        return [self.asset][:limit]

    def get_asset(self, *, asset_id: str):
        if asset_id == self.asset.id:
            return self.asset
        from production_backend.app.core.errors import ApiError

        raise ApiError(code="not_found", message="Asset not found.", status=404)


class FakeObjectStorage:
    def __init__(self, *, body: bytes) -> None:
        self.body = body
        self.read_keys: list[str] = []
        self.read_ranges: list[tuple[str, int, int]] = []

    async def put_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        return StoredObject(key=key, uri=f"memory://{key}", size_bytes=len(body), content_type=content_type)

    async def get_bytes(self, *, key: str) -> bytes:
        self.read_keys.append(key)
        return self.body

    async def get_byte_range(self, *, key: str, start: int, end: int) -> bytes:
        self.read_ranges.append((key, start, end))
        return self.body[start : end + 1]

    async def delete(self, *, key: str) -> None:
        return None
