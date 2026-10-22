#!/usr/bin/env python3
"""Host-local, manually approved North America release driver.

The operator must have independently staged clean main checkouts at the fixed
release paths. Never create a checkout or choose first-release from host state.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.north_america_docker_context import require_local_docker  # noqa: E402
from scripts.north_america_migrate_first import verify_image  # noqa: E402
from scripts.north_america_update_release import _slot  # noqa: E402

ROOT = Path('/opt/momcozy-lab-us-east-uat')
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


def checked_source(service: str, commit: str, *, allow_current: bool = False) -> Path:
    if service not in ('backend', 'agent') or not SHA.fullmatch(commit):
        raise ValueError('invalid release source')
    source = ROOT / 'releases' / service / commit
    if source.is_symlink() or not source.is_dir() or source.resolve() != source:
        raise ValueError('release source not staged')
    def git(*args: str) -> str:
        return subprocess.run(['git', '-C', str(source), *args], capture_output=True, text=True, check=True).stdout.strip()
    dirty = git('status', '--porcelain', '--untracked-files=all')
    current = ROOT / 'current' / service
    activated = (allow_current and current.is_symlink() and current.resolve() == source
                 and dirty == '?? release-manifest.json')
    if (git('rev-parse', 'HEAD') != commit or git('branch', '--show-current') != 'main'
            or (dirty and not activated)):
        raise ValueError('source is not a clean main checkout or verified active source')
    return source


def pull_image(service: str, commit: str, digest: str, token: str) -> tuple[str, str, str]:
    if service not in ('backend', 'agent') or not SHA.fullmatch(commit) or not DIGEST.fullmatch(digest) or not token:
        raise ValueError('invalid immutable image')
    ref = f'ghcr.io/hensonzh/momcozy-lab-{service}@{digest}'
    tag = f'ghcr.io/hensonzh/momcozy-lab-{service}:north-america-oci-{commit}'
    with tempfile.TemporaryDirectory(prefix='momcozy-ghcr-', dir='/dev/shm') as config:
        env = {**os.environ, 'DOCKER_CONFIG': config}
        subprocess.run(['docker', 'login', 'ghcr.io', '-u', 'hensonzh', '--password-stdin'],
                       input=token, text=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', ref], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    inspected = json.loads(subprocess.run(['docker', 'image', 'inspect', ref, '--format', '{{json .}}'],
                                          capture_output=True, text=True, check=True).stdout)
    if (inspected.get('Os') != 'linux' or inspected.get('Architecture') != 'amd64'
            or ref not in inspected.get('RepoDigests', [])):
        raise ValueError('pulled digest/platform does not match')
    image_id = inspected['Id']
    if not DIGEST.fullmatch(image_id):
        raise ValueError('invalid image ID')
    labels = (inspected.get('Config') or {}).get('Labels') or {}
    if (labels.get('org.opencontainers.image.revision') != commit
            or labels.get('org.opencontainers.image.source') != f'https://github.com/hensonzh/momcozy-lab-{service}'
            or labels.get('org.momcozy.release-target') != 'north-america-staging'):
        raise ValueError('pulled image labels differ from approved source')
    subprocess.run(['docker', 'tag', ref, tag], capture_output=True, check=True)
    verify_image(tag, commit, service, image_id)
    return ref, tag, image_id


@contextmanager
def manual_lock() -> Iterator[None]:
    shared = ROOT / 'shared'
    if shared.is_symlink() or not shared.is_dir() or stat.S_IMODE(shared.stat().st_mode) != 0o700:
        raise ValueError('private delivery lock directory missing')
    path = shared / 'north-america-staging-manual-delivery.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise ValueError('delivery lock is not private')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def run(args: argparse.Namespace, token: str) -> None:
    require_local_docker()
    lock = ROOT / 'shared/north-america-staging-release.lock'
    if (lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) != 0o600
            or lock.stat().st_uid != os.geteuid()):
        raise ValueError('private host release lock is missing')
    if args.operation == 'rollback':
        if not args.confirm_client_compatible or args.service not in ('backend', 'agent'):
            raise ValueError('rollback requires explicit client compatibility confirmation')
        # Target selected for rollback must be exactly the CI-proven previous slot;
        # the other service must match its current slot. Never silently roll back
        # a different pair than the operator approved.
        for service in ('backend', 'agent'):
            slot = 'previous' if service == args.service else 'current'
            _, manifest = _slot(service, slot)
            expected_commit = getattr(args, service + '_commit')
            expected_ref = f'ghcr.io/hensonzh/momcozy-lab-{service}@{getattr(args, service + "_digest")}'
            if manifest['commit'] != expected_commit or manifest['image_ref'] != expected_ref:
                raise ValueError('rollback target differs from the approved source/image pair')
        backend_current, backend_manifest = _slot('backend', 'current')
        source = checked_source('backend', backend_manifest['commit'], allow_current=True)
        if source != backend_current:
            raise ValueError('rollback tooling is not the current Backend source')
        script = source / 'scripts/north_america_update_release.py'
        command = [sys.executable, str(script), '--operation', 'rollback', '--service', args.service,
                   '--target', str(ROOT / f'shared/{args.service}/north-america-staging.json'),
                   '--env-file', str(ROOT / f'shared/{args.service}/north-america-staging.env'),
                   '--agent-env', str(ROOT / 'shared/agent/north-america-staging.env'), '--confirm-client-compatible']
    else:
        sources = {service: checked_source(service, getattr(args, service + '_commit'),
                                           allow_current=args.operation == 'update' and service != args.service)
                   for service in ('backend', 'agent')}
        if args.operation == 'update':
            other = 'agent' if args.service == 'backend' else 'backend'
            _, manifest = _slot(other, 'current')
            expected_ref = f'ghcr.io/hensonzh/momcozy-lab-{other}@{getattr(args, other + "_digest")}'
            if manifest['commit'] != getattr(args, other + '_commit') or manifest['image_ref'] != expected_ref:
                raise ValueError('paired active service differs from approved main image')
        images = {service: pull_image(service, getattr(args, service + '_commit'),
                                     getattr(args, service + '_digest'), token)
                  for service in (('backend', 'agent') if args.operation == 'first-release' else (args.service,))}
        if args.operation == 'first-release':
            script = sources['backend'] / 'scripts/north_america_first_release.py'
            command = [sys.executable, str(script)]
            for service in ('backend', 'agent'):
                ref, tag, image_id = images[service]
                command += [f'--{service}-source', str(sources[service]),
                            f'--{service}-env', str(ROOT / f'shared/{service}/north-america-staging.env'),
                            f'--{service}-target', str(ROOT / f'shared/{service}/north-america-staging.json'),
                            f'--{service}-commit', getattr(args, service + '_commit'),
                            f'--{service}-image', ref, f'--{service}-local-image', tag,
                            f'--{service}-image-id', image_id]
            command.append('--apply' if args.apply else '--preflight')
        else:
            ref, tag, image_id = images[args.service]
            script = sources['backend'] / 'scripts/north_america_update_release.py'
            command = [sys.executable, str(script), '--operation', 'update', '--service', args.service,
                       '--target', str(ROOT / f'shared/{args.service}/north-america-staging.json'),
                       '--env-file', str(ROOT / f'shared/{args.service}/north-america-staging.env'),
                       '--agent-env', str(ROOT / 'shared/agent/north-america-staging.env'),
                       '--source', str(sources[args.service]), '--commit', getattr(args, args.service + '_commit'),
                       '--image', ref, '--local-image', tag, '--image-id', image_id]
            if args.apply:
                command.append('--apply')
    if args.apply:
        preflight = [value for value in command if value != "--apply"]
        if args.operation == "first-release":
            preflight.append("--preflight")
        subprocess.run(preflight, check=True)
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operation', choices=('first-release', 'update', 'rollback'), required=True)
    parser.add_argument('--service', choices=('backend', 'agent'), required=True)
    for name in ('backend-commit', 'agent-commit', 'backend-digest', 'agent-digest'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-client-compatible', action='store_true')
    args = parser.parse_args()
    try:
        # GHCR credential travels only in the SSH stdin channel; never arguments, logs or disk.
        token = sys.stdin.readline().strip() if args.operation != 'rollback' else ''
        with manual_lock():
            run(args, token)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('FAIL North America manual delivery: inspect host admission; no automatic retry', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
