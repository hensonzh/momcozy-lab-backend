#!/usr/bin/env python3
"""Fetch a B GHCR image by immutable digest into a checked linux/amd64 OCI archive.

Read a repository-scoped pull token from a private file. This never stores
registry credentials in Docker's persistent configuration or command arguments.
The archive can be imported only after its source digest and blobs are checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ACCEPT = ",".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
REPOSITORIES = ("momcozy-lab-backend", "momcozy-lab-agent")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def validate(repo: str, digest: str, output: Path) -> None:
    if repo not in REPOSITORIES or not DIGEST.fullmatch(digest):
        raise ValueError("unapproved B repository or image digest")
    if output.exists() or output.is_symlink():
        raise ValueError("OCI output already exists")


def fetch_layout(args: argparse.Namespace) -> None:
    validate(args.repo, args.digest, args.output)
    token_file = args.token_file
    if (token_file.is_symlink() or not token_file.is_file()
            or stat.S_IMODE(token_file.stat().st_mode) != 0o600
            or not str(token_file).startswith("/dev/shm/momcozy-b-ghcr-transfer/")):
        raise ValueError("B GHCR token must be a private tmpfs file")
    token = token_file.read_text().strip()
    if not token:
        raise ValueError("B GHCR token is empty")
    base = f"https://ghcr.io/v2/hensonzh/{args.repo}"

    def fetch(kind: str, digest: str) -> bytes:
        if not DIGEST.fullmatch(digest):
            raise ValueError("registry descriptor is not a SHA256 digest")
        req = urllib.request.Request(
            f"{base}/{kind}/{digest}",
            headers={"Authorization": "Bearer " + token, "Accept": ACCEPT},
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            content = response.read()
        if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("registry content digest mismatch")
        return content

    with tempfile.TemporaryDirectory(prefix="momcozy-b-oci-") as temporary:
        folder = Path(temporary)
        blobs = folder / "blobs/sha256"
        blobs.mkdir(parents=True)
        (folder / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')

        def put(content: bytes) -> None:
            (blobs / hashlib.sha256(content).hexdigest()).write_bytes(content)

        index = json.loads(fetch("manifests", args.digest))
        children = [
            descriptor for descriptor in index.get("manifests", [])
            if descriptor.get("platform") == {"os": "linux", "architecture": "amd64"}
        ]
        if len(children) != 1:
            raise ValueError("expected exactly one linux/amd64 child")
        child = children[0]
        manifest_raw = fetch("manifests", child["digest"])
        if len(manifest_raw) != child["size"]:
            raise ValueError("child manifest size mismatch")
        manifest = json.loads(manifest_raw)
        for descriptor in (manifest["config"], *manifest["layers"]):
            content = fetch("blobs", descriptor["digest"])
            if len(content) != descriptor["size"]:
                raise ValueError("image blob size mismatch")
            put(content)
        put(manifest_raw)
        (folder / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [child]}))
        # Only digest-named known files enter the archive; no arbitrary paths.
        with tarfile.open(args.output, "w") as archive:
            for filename in ("oci-layout", "index.json"):
                archive.add(folder / filename, arcname=filename, recursive=False)
            for file in blobs.iterdir():
                archive.add(file, arcname="blobs/sha256/" + file.name, recursive=False)
    print(f"B {args.repo} verified OCI index {args.digest}; archive size {args.output.stat().st_size}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=REPOSITORIES, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        fetch_layout(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print("FAIL B OCI digest delivery; no image accepted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
