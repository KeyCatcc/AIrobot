"""Enable or disable one registered dance with a timestamped JSON backup."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from dance_catalog import DEFAULT_DANCE_CATALOG


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_DANCE_CATALOG)
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()

    path = args.catalog.resolve(strict=True)
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = document.get("dances")
    if not isinstance(rows, list):
        raise ValueError("dance catalog must contain a dances list")
    row = next((item for item in rows if isinstance(item, dict) and item.get("name") == args.name), None)
    if row is None:
        raise ValueError("dance is not registered: " + args.name)
    backup = path.with_name(path.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
    backup.write_bytes(path.read_bytes())
    row["enabled"] = not args.disable
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.name}: enabled={row['enabled']}")
    print("Backup:", backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
