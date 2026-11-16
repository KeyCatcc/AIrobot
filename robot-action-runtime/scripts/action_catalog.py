"""Operator-owned catalog; no keyword routing or model-owned execution settings."""
import hashlib
import json
import math
from pathlib import Path


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "configs/action_catalog.json"


def load_catalog(path=DEFAULT_CATALOG, include_disabled=False):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_catalog(data, include_disabled)


def validate_catalog(data, include_disabled=False):
    if data.get("version") != 1 or not isinstance(data.get("actions"), list):
        raise ValueError("Invalid catalog version or actions")
    names = set()
    enabled = {}
    for row in data["actions"]:
        name = row["name"]
        if not isinstance(name, str) or not name.strip() or name == "none" or name in names:
            raise ValueError("Invalid or duplicate action name")
        names.add(name)
        amplitude = row["amplitude"]
        if isinstance(amplitude, bool) or not isinstance(amplitude, (int, float)) or not math.isfinite(amplitude) or not 0 < amplitude <= 1:
            raise ValueError("Invalid amplitude")
        if not isinstance(row["enabled"], bool) or not isinstance(row["description"], str) or not row["description"].strip():
            raise ValueError("Invalid action metadata")
        if row["enabled"] or include_disabled:
            enabled[name] = dict(row)
    if not enabled:
        raise ValueError("No enabled actions")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return enabled, digest
