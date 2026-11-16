"""Add a disabled head-shake sequence. Existing assets are never overwritten."""
import argparse
from datetime import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(library, catalog):
    library = json.loads(json.dumps(library))
    catalog = json.loads(json.dumps(catalog))
    poses = {
        "head_yaw_positive": [10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "head_yaw_negative": [-10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        "head_yaw_neutral": [0] * 12,
    }
    sequence = [
        {"gesture": "head_yaw_positive", "duration": 0.8},
        {"gesture": "head_yaw_negative", "duration": 1.2},
        {"gesture": "head_yaw_neutral", "duration": 0.8},
    ]
    for name, pose in poses.items():
        if name in library.get("action_sequences", {}):
            raise ValueError("Name used by sequence: " + name)
        base = library.setdefault("base_gestures", {})
        if name in base and base[name] != pose:
            raise ValueError("Existing pose differs: " + name)
        base[name] = pose
    if "head_shake" in library["base_gestures"]:
        raise ValueError("head_shake already names a pose")
    sequences = library.setdefault("action_sequences", {})
    if "head_shake" in sequences and sequences["head_shake"] != sequence:
        raise ValueError("Existing head_shake sequence differs")
    sequences["head_shake"] = sequence
    existing = [r for r in catalog["actions"] if r["name"] == "head_shake"]
    if len(existing) > 1:
        raise ValueError("Duplicate catalog entry")
    if existing:
        if existing[0].get("enabled"):
            raise ValueError("head_shake already enabled; review manually")
    else:
        catalog["actions"].append({
            "name": "head_shake", "kind": "sequence", "enabled": False,
            "description": "左右摇头后回中；仅明确摇头要求或明确的人际否定，待真机验证。",
            "amplitude": 0.2,
            "amplitude_status": "candidate_unverified"})
    return library, catalog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    paths = [ROOT / "configs/gestures/custom_gestures.json", ROOT / "configs/action_catalog.json"]
    originals = [path.read_bytes() for path in paths]
    results = prepare(*(json.loads(raw.decode("utf-8-sig")) for raw in originals))
    print("head_shake: +10deg (0.8s) -> -10deg (1.2s) -> 0deg (0.8s)")
    print("At amplitude 0.2: +/-2deg. Player appends 2s zero return; total 4.8s.")
    print("All other joint targets are zero, not 'hold current'. No hardware commands.")
    if not args.apply:
        print("PREVIEW only. Use --apply to save disabled assets.")
        return
    if any(path.read_bytes() != raw for path, raw in zip(paths, originals)):
        raise RuntimeError("Files changed; retry")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for path, raw in zip(paths, originals):
        path.with_name(path.name + "." + stamp + ".bak").write_bytes(raw)
    for path, result in zip(paths, results):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    print("Saved; head_shake remains disabled. Both original files backed up.")


if __name__ == "__main__":
    main()
