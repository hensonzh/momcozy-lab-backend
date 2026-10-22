#!/usr/bin/env python3
"""Bind North America host operations to the local Unix Docker daemon, never a shell context."""

from __future__ import annotations

import os


LOCAL_SOCKET = "unix:///var/run/docker.sock"


def require_local_docker() -> None:
    if (os.environ.get("DOCKER_HOST", LOCAL_SOCKET) != LOCAL_SOCKET
            or os.environ.get("DOCKER_CONTEXT")
            or os.environ.get("DOCKER_CONFIG")):
        raise ValueError("North America Docker target must be the local Unix socket without context overrides")
    # Explicitly select the local daemon even if ~/.docker/config.json sets a
    # remote currentContext. No registry credentials are read from this helper.
    os.environ["DOCKER_HOST"] = LOCAL_SOCKET
