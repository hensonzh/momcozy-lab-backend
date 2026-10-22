"""Fail-closed host driver admission; no SSH, Docker or live server needed."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from scripts import north_america_manual_delivery as driver


def test_checked_source_rejects_unstaged_and_dirty_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    sha = 'a' * 40
    with pytest.raises(ValueError, match='not staged'):
        driver.checked_source('backend', sha)
    source = tmp_path / 'releases/backend' / sha
    source.mkdir(parents=True)
    def git_run(command, **kwargs):
        name = command[-1]
        value = {'HEAD': sha, '--show-current': 'main', '--untracked-files=all': '?? unsafe.env'}[name]
        return subprocess.CompletedProcess(command, 0, value)
    monkeypatch.setattr(driver.subprocess, 'run', git_run)
    with pytest.raises(ValueError, match='clean main'):
        driver.checked_source('backend', sha)


def test_image_pull_rejects_unpinned_input_before_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(driver.subprocess, 'run', lambda *a, **kw: pytest.fail('docker called'))
    with pytest.raises(ValueError, match='immutable'):
        driver.pull_image('backend', 'a' * 40, 'latest', 'secret')
    with pytest.raises(ValueError, match='immutable'):
        driver.pull_image('agent', 'a' * 40, 'sha256:' + 'b' * 64, '')


def test_rollback_requires_explicit_compatibility_before_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'require_local_docker', lambda: None)
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'north-america-staging-release.lock').write_text('')
    (shared / 'north-america-staging-release.lock').chmod(0o600)
    args = argparse.Namespace(operation='rollback', service='agent', confirm_client_compatible=False,
                              backend_commit='a' * 40, agent_commit='b' * 40)
    monkeypatch.setattr(driver, '_slot', lambda *a: pytest.fail('manifest read before compatibility'))
    monkeypatch.setattr(driver.subprocess, 'run', lambda *a, **kw: pytest.fail('runner called'))
    with pytest.raises(ValueError, match='compatibility'):
        driver.run(args, '')


def test_apply_runs_admission_before_mutating_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'require_local_docker', lambda: None)
    shared = tmp_path / 'shared'
    shared.mkdir()
    lock = shared / 'north-america-staging-release.lock'
    lock.touch(mode=0o600)
    monkeypatch.setattr(driver, 'checked_source', lambda service, commit, **kw: tmp_path / service)
    monkeypatch.setattr(driver, 'pull_image', lambda service, commit, digest, token: ('ref', 'tag', 'sha256:' + 'a'*64))
    calls: list[list[str]] = []
    monkeypatch.setattr(driver.subprocess, 'run', lambda command, **kw: calls.append(command))
    args = argparse.Namespace(operation='first-release', service='backend', apply=True,
                              backend_commit='a'*40, agent_commit='b'*40,
                              backend_digest='sha256:'+'a'*64, agent_digest='sha256:'+'b'*64)
    driver.run(args, 'token')
    assert len(calls) == 2
    assert '--preflight' in calls[0] and '--apply' not in calls[0]
    assert '--apply' in calls[1] and '--preflight' not in calls[1]


def test_rollback_rejects_mismatched_manifest_pair_before_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'require_local_docker', lambda: None)
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'north-america-staging-release.lock').touch(mode=0o600)
    monkeypatch.setattr(driver, '_slot', lambda service, slot: (tmp_path / service,
        {'commit': 'c'*40, 'image_ref': 'ghcr.io/other/image@sha256:' + 'f'*64}))
    monkeypatch.setattr(driver.subprocess, 'run', lambda *a, **kw: pytest.fail('runner called'))
    args = argparse.Namespace(operation='rollback', service='agent', apply=True,
                              confirm_client_compatible=True, backend_commit='a'*40,
                              agent_commit='b'*40, backend_digest='sha256:'+'a'*64,
                              agent_digest='sha256:'+'b'*64)
    with pytest.raises(ValueError, match='approved source/image'):
        driver.run(args, '')


def test_update_rejects_unapproved_active_pair_before_pull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(driver, 'ROOT', tmp_path)
    monkeypatch.setattr(driver, 'require_local_docker', lambda: None)
    shared = tmp_path / 'shared'
    shared.mkdir()
    (shared / 'north-america-staging-release.lock').touch(mode=0o600)
    monkeypatch.setattr(driver, 'checked_source', lambda service, commit, **kw: tmp_path / service)
    monkeypatch.setattr(driver, '_slot', lambda service, slot: (tmp_path / service,
        {'commit': 'c'*40, 'image_ref': 'ghcr.io/other/image@sha256:' + 'f'*64}))
    monkeypatch.setattr(driver, 'pull_image', lambda *a: pytest.fail('image pulled'))
    args = argparse.Namespace(operation='update', service='backend', apply=True,
                              backend_commit='a'*40, agent_commit='b'*40,
                              backend_digest='sha256:'+'a'*64, agent_digest='sha256:'+'b'*64)
    with pytest.raises(ValueError, match='paired active service'):
        driver.run(args, 'token')
