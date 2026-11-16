"""Register body assets without enabling them. Preview by default, --apply to save."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merge_catalog(catalog, library):
    if catalog.get("version") != 1 or not isinstance(catalog.get("actions"), list):
        raise ValueError("Invalid catalog")
    kinds = {}
    for section, kind in (("base_gestures", "pose"), ("action_sequences", "sequence")):
        rows = library.get(section, {})
        if not isinstance(rows, dict):
            raise ValueError("Invalid library section: " + section)
        for name in rows:
            kinds.setdefault(name, []).append(kind)
    conflicts = {name: values for name, values in kinds.items() if len(values) != 1}
    result = json.loads(json.dumps(catalog))
    existing = {}
    for row in result["actions"]:
        name = row["name"]
        if name in existing:
            raise ValueError("Duplicate catalog name: " + name)
        existing[name] = row
    added = []
    for name, values in kinds.items():
        if name in conflicts:
            if name in existing and existing[name].get("enabled"):
                raise ValueError("Enabled action has ambiguous asset kind: " + name)
            continue
        kind = values[0]
        if name in existing:
            row = existing[name]
            if row.get("kind", kind) != kind:
                raise ValueError("Existing kind conflicts with library: " + name)
            row.setdefault("kind", kind)
            # Do not infer physical validation from enabled or asset existence.
            continue
        row = {"name": name, "kind": kind, "enabled": False,
               "description": "待人工确认语义，不向RAI开放。",
               "amplitude": 0.2,
               "amplitude_status": "candidate_unverified"}
        result["actions"].append(row)
        added.append({"name": name, "kind": kind})
    missing = [name for name in existing if name not in kinds]
    return result, {"added": added, "existing_preserved": len(existing),
                    "conflicts_skipped": conflicts,
                    "missing_from_body_library": missing,
                    "counts": dict(Counter(row.get("kind", "unknown") for row in result["actions"])),
                    "enabled": [row["name"] for row in result["actions"] if row["enabled"]]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--catalog", type=Path, default=ROOT / "configs/action_catalog.json")
    p.add_argument("--library", type=Path, default=ROOT / "configs/gestures/custom_gestures.json")
    args = p.parse_args()
    original = args.catalog.read_bytes()
    catalog = json.loads(original.decode("utf-8-sig"))
    library = json.loads(args.library.read_text(encoding="utf-8-sig"))
    result, report = merge_catalog(catalog, library)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.apply:
        print("PREVIEW: no files changed. Use --apply to register; new entries stay disabled.")
        return
    if args.catalog.read_bytes() != original:
        raise RuntimeError("Catalog changed during preview; retry")
    backup = args.catalog.with_name(args.catalog.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
    backup.write_bytes(original)
    temporary = args.catalog.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.catalog)
    print("Registered. Backup:", backup)
    print("Finger-only entries are excluded. No actions executed; no new entries enabled.")


if __name__ == "__main__":
    main()
