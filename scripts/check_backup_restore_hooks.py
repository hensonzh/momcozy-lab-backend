from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from app.core.backup_restore import build_backup_restore_manifest
    from app.core.settings import Settings

    parser = argparse.ArgumentParser(description="Check backup and restore automation hook configuration.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when required hooks are missing.")
    parser.add_argument("--show-values", action="store_true", help="Include hook references in the JSON output.")
    args = parser.parse_args()

    manifest = build_backup_restore_manifest(Settings.from_env())
    print(json.dumps(manifest.as_dict(show_values=args.show_values), indent=2, sort_keys=True))

    if args.strict and manifest.missing_required:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
