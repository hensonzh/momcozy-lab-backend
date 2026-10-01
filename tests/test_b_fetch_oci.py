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
