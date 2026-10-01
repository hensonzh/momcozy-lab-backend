#!/bin/sh
# Certbot deploy hook for the B-only Nginx ingress.
set -eu
if systemctl is-active --quiet nginx; then
    nginx -t
    systemctl reload nginx
fi
