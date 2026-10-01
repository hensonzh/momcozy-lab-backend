#!/bin/sh
# Certbot deploy hook for the B-only Nginx ingress.
set -eu
if systemctl is-active --quiet nginx; then
    nginx -t >/dev/null 2>&1
    systemctl reload nginx
fi
