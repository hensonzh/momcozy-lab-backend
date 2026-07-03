from pathlib import Path

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.assets.router import get_product_asset_service
from production_backend.app.modules.assets.service import ProductAssetService


def test_packaged_product_asset_main_flow_lists_serves_heads_and_hides_local_paths(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    (asset_root / "air1" / "images").mkdir(parents=True)
    public_asset = asset_root / "air1" / "images" / "guide.png"
    public_asset.write_bytes(b"pngdata")
    (asset_root / "air1" / "images" / "notes.txt").write_text("private notes")
    service = ProductAssetService(root=asset_root)
    app = create_app(Settings(app_env="test"))
    app.dependency_overrides[get_product_asset_service] = lambda: service
    client = TestClient(app)

    list_response = client.get("/v1/assets?limit=10")
    item = list_response.json()["items"][0]
    get_response = client.get(f"/v1/assets/{item['id']}")
    head_response = client.head(f"/v1/assets/{item['id']}")
    private_response = client.get("/v1/assets/notes.txt")

    assert list_response.status_code == 200
    assert item["id"].startswith("asset_")
    assert item["content_type"] == "image/png"
    assert item["size_bytes"] == len(b"pngdata")
    assert "path" not in item
    assert "air1" not in item["id"]
    assert get_response.status_code == 200
    assert get_response.content == b"pngdata"
    assert get_response.headers["content-type"].startswith("image/png")
    assert head_response.status_code == 200
    assert head_response.headers["content-type"].startswith("image/png")
    assert head_response.content == b""
    assert private_response.status_code == 404
    assert private_response.json()["error"]["code"] == "not_found"
