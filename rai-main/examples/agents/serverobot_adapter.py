"""Manual welcome adapter. Defaults to serverobot dry-run; never used by LLM yet.

Reuses serverobot's JSON parser, interpolation, motor mapping and ROS publisher.
Process completion means estimated playback elapsed, NOT physical completion.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import os
import json


def command(root, python, gesture="welcome_gesture", execute=False, amplitude=0.2):
    if gesture != "welcome_gesture":
        raise ValueError("Only welcome is enabled for hardware verification")
    if not 0 < amplitude <= 0.25:
        raise ValueError("Amplitude must be within (0, 0.25]")
    root = Path(root).resolve(strict=True)
    script = root / "scripts" / "test_custom_actions.py"
    if not script.is_file():
        raise ValueError("serverobot test_custom_actions.py not found")
    args = [str(python), str(script), "--action", "welcome_gesture",
            "--amplitude", str(amplitude), "--skip-fingers", "--return-home", "2"]
    if execute:
        # Retain serverobot's interactive confirmation; no --yes or shell command.
        args.append("--execute")
    return root, args


def run_selected(gesture):
    """Synchronous single-action lab bridge; no physical completion claim."""
    if gesture == "none":
        return {"status": "no_action", "gesture": gesture, "hardware_executed": False}
    root = os.environ.get("RAI_SERVEROBOT_ROOT")
    if not root:
        raise ValueError("Set RAI_SERVEROBOT_ROOT")
    execute = os.environ.get("RAI_SERVEROBOT_EXECUTE") == "1"
    root, argv = command(root, os.environ.get("RAI_SERVEROBOT_PYTHON", sys.executable),
                         gesture, execute, float(os.environ.get("RAI_SERVEROBOT_AMPLITUDE", "0.2")))
    if execute:
        argv.append("--yes")  # Explicit operator opt-in via EXECUTE=1; not model-controlled.
    print("[SERVEROBOT]", "hardware" if execute else "dry-run", gesture, flush=True)
    result = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL)
    return {"status": ("estimated_playback_elapsed" if execute else "dry_run_completed")
            if result.returncode == 0 else "execution_failed",
            "gesture": gesture, "returncode": result.returncode,
            "hardware_command_requested": execute,
            "hardware_executed": None if execute else False,
            "physical_completion_confirmed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--amplitude", type=float, default=0.2)
    args = parser.parse_args()
    root, argv = command(args.root, args.python, execute=args.execute,
                         amplitude=args.amplitude)
    print("Mode:", "hardware with interactive confirmation" if args.execute else "dry-run",
          flush=True)
    return subprocess.call(argv, cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
