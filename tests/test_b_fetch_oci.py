"""B OCI delivery must enforce exact registry digest and private token input."""

from pathlib import Path

import pytest

from scripts import b_fetch_oci


def test_bad_digest_and_existing_output_fail_before_token_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b_fetch_oci, "fetch_layout", lambda *args: pytest.fail("must not fetch"))
    with pytest.raises(ValueError):
        b_fetch_oci.validate("momcozy-lab-backend", "latest", tmp_path / "new")
    present = tmp_path / "present"
    present.touch()
    with pytest.raises(ValueError):
        b_fetch_oci.validate("momcozy-lab-backend", "sha256:" + "a" * 64, present)


def test_wrong_repository_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unapproved"):
        b_fetch_oci.validate("momcozy-lab-other", "sha256:" + "a" * 64, tmp_path / "output")


def test_token_must_be_tmpfs_and_private(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("not-a-token")
    token.chmod(0o600)
    with pytest.raises(ValueError, match="private tmpfs"):
        b_fetch_oci.fetch_layout(type("Args", (), {
            "repo": "momcozy-lab-backend", "digest": "sha256:" + "a" * 64,
            "output": tmp_path / "output", "token_file": token,
        })())


def test_script_direct_invocation() -> None:
    import subprocess
    result = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1] / "scripts/b_fetch_oci.py"), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0


def test_registry_exchange_uses_basic_pat_then_scoped_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    import io
    import json
    import urllib.request

    seen: list[urllib.request.Request] = []
    class Response(io.BytesIO):
        def __enter__(self) -> "Response": return self
        def __exit__(self, *args: object) -> None: self.close()
    def open_request(req: urllib.request.Request, timeout: int) -> Response:
        seen.append(req)
        return Response(json.dumps({"token": "registry-bearer"}).encode())
    monkeypatch.setattr(b_fetch_oci.urllib.request, "urlopen", open_request)
    bearer = b_fetch_oci.registry_token("momcozy-lab-agent", "ghp-private", username="hensonzh")
    assert bearer == "registry-bearer"
    assert seen[0].full_url == "https://ghcr.io/token?service=ghcr.io&scope=repository%3Ahensonzh%2Fmomcozy-lab-agent%3Apull"
    assert seen[0].get_header("Authorization") == "Basic " + base64.b64encode(b"hensonzh:ghp-private").decode()
    assert len(seen) == 1


def test_registry_exchange_rejects_missing_token_or_wrong_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    monkeypatch.setattr(b_fetch_oci.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"{}"))
    with pytest.raises(ValueError, match="scoped registry token"):
        b_fetch_oci.registry_token("momcozy-lab-backend", "pat", username="hensonzh")
    with pytest.raises(ValueError, match="unapproved"):
        b_fetch_oci.registry_token("different-repo", "pat", username="hensonzh")


def test_preissued_registry_bearer_skips_pat_exchange(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib
    import io
    import json
    from types import SimpleNamespace

    manifest = json.dumps({"manifests": []}).encode()
    digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
    token_root = tmp_path / "momcozy-b-ghcr-transfer"
    token_root.mkdir(mode=0o700)
    token_file = token_root / "backend.registry-token"
    token_file.write_text("scoped-bearer")
    token_file.chmod(0o600)
    monkeypatch.setattr(b_fetch_oci, "PRIVATE_ROOT", token_root)
    monkeypatch.setattr(b_fetch_oci, "registry_token", lambda *a, **kw: pytest.fail("must not exchange PAT"))
    class Response(io.BytesIO):
        def __enter__(self) -> "Response": return self
        def __exit__(self, *args: object) -> None: self.close()
    def open_request(req: object, timeout: int) -> Response:
        assert req.get_header("Authorization") == "Bearer scoped-bearer"
        return Response(manifest)
    monkeypatch.setattr(b_fetch_oci.urllib.request, "urlopen", open_request)
    with pytest.raises(ValueError, match="amd64"):
        b_fetch_oci.fetch_layout(SimpleNamespace(repo="momcozy-lab-backend", digest=digest,
            output=tmp_path / "image.tar", token_file=None, registry_token_file=token_file))
    assert not (tmp_path / "image.tar").exists()


def test_both_token_sources_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        b_fetch_oci.main(["--repo", "momcozy-lab-backend", "--digest", "sha256:"+"a"*64,
                          "--output", "/tmp/b.tar", "--token-file", "/tmp/pat",
                          "--registry-token-file", "/tmp/bearer"])
