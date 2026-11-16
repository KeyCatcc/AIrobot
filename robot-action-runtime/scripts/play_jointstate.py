"""Selected body gesture test for the existing MoveIt bridge; dry-run by default.

Requires a verified neutral starting posture and an external stop mechanism.
Ending publication does not stop the bridge or prove physical completion.
"""

import argparse
import math
import time
import os
from pathlib import Path

import numpy as np
from test_custom_actions import (
    DEFAULT_ACTIONS, DEFAULT_GESTURES, GestureLibrary,
    LEGACY_JOINT_NAMES, build_relative_frames,
    DEFAULT_MOTOR_MAP, ROOT, load_yaml,
)
from jointstate_mapping import convert_frames
from action_catalog import load_catalog, DEFAULT_CATALOG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--http-confirmed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--action", default="挥手/招手")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--test-disabled", action="store_true",
                        help="Manual testing only: allow a registered but disabled action")
    parser.add_argument("--amplitude", type=float, default=0.2)
    parser.add_argument("--allow-larger-amplitude", action="store_true",
                        help="Explicitly allow amplitude above 0.25, up to 1.0")
    parser.add_argument("--motor-map", type=Path, default=DEFAULT_MOTOR_MAP)
    parser.add_argument("--bridge-map", type=Path,
                        default=ROOT / "configs/moveit_to_motor_mapping.reference.yaml")
    args = parser.parse_args()
    if args.test_disabled and args.http_confirmed:
        parser.error("Disabled actions require manual testing, not HTTP execution")
    catalog, _ = load_catalog(args.catalog, include_disabled=args.test_disabled)
    if args.action not in catalog:
        parser.error("Action not enabled; manual tests of registered actions need --test-disabled")
    if args.test_disabled and not catalog[args.action]["enabled"]:
        print("MANUAL TEST: disabled action; robot HTTP and RAI remain unchanged")
    if not math.isfinite(args.amplitude) or not 0 < args.amplitude <= 1.0:
        parser.error("amplitude must be within (0, 1.0]")
    if args.amplitude > 0.25 and not args.allow_larger_amplitude:
        parser.error("amplitude above 0.25 requires --allow-larger-amplitude")
    library = GestureLibrary(DEFAULT_ACTIONS, DEFAULT_GESTURES)
    name, _ = library.resolve(args.action)
    duration = library.duration_for(name, name, 1.5)
    steps = library.steps(name, duration)
    relative = build_relative_frames(
        steps, fps=30, amplitude=args.amplitude,
        return_home_sec=2.0)
    if args.execute and args.bridge_map.resolve() == (ROOT / "configs/moveit_to_motor_mapping.reference.yaml").resolve():
        parser.error("For execution, pass --bridge-map with the actual bridge config_file path")
    parameters = load_yaml(args.bridge_map)["moveit_to_motor_mapping"]["ros__parameters"]
    converted, gains = convert_frames(relative, LEGACY_JOINT_NAMES,
                                     load_yaml(args.motor_map), parameters)
    frames = np.asarray(converted)
    print("Motor mapping:", args.motor_map)
    print("Bridge mapping:", args.bridge_map)
    print(f"{name}: {len(frames)} frames, {len(frames)/30:.2f}s, relative radians")
    elapsed = 0.0
    for index, (step_name, angles, seconds) in enumerate(steps, 1):
        print(f"  {index}. {step_name}: {seconds:.2f}s transition, t={elapsed:.2f}s")
        elapsed += seconds
    print("  Additional return to relative zero: 2.00s; no extra pose holds")
    print("  Body joints only; no finger/wrist commands are added")
    for index, joint in enumerate(LEGACY_JOINT_NAMES):
        values = np.degrees(frames[:, index])
        print(f"  {joint}: gain={gains[index]:g}, bridge input "
              f"{values.min():.2f} .. {values.max():.2f} deg")
    print("Joint names:", LEGACY_JOINT_NAMES)
    print("Final frame:", frames[-1].tolist())
    if not args.execute:
        print("DRY-RUN: no ROS messages published")
        return

    import rclpy
    from sensor_msgs.msg import JointState

    rclpy.init()
    node = rclpy.create_node("gesture_jointstate_test")
    pub = node.create_publisher(JointState, "/joint_states", 10)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.1)
            if pub.get_subscription_count():
                break
        if not pub.get_subscription_count():
            raise RuntimeError("No /joint_states subscriber; nothing published")
        if node.count_publishers("/joint_states") != 1:
            raise RuntimeError("Other /joint_states publishers detected; nothing published")
        print("Bridge must already be configured. Its cached waist targets may be 180 degrees.")
        print("Confirm neutral starting posture (including waist), clear workspace and external stop.")
        confirmed = args.http_confirmed and len(os.environ.get("ROBOT_ACTION_TOKEN", "")) >= 24
        if not confirmed and input(f"Type EXECUTE to publish {name!r} once: ").strip() != "EXECUTE":
            print("Skipped")
            return
        start = time.monotonic()
        for index, frame in enumerate(frames):
            if not rclpy.ok():
                raise RuntimeError("ROS stopped; physical stop not confirmed")
            if node.count_publishers("/joint_states") != 1:
                raise RuntimeError("Concurrent publisher detected; publication stopped, robot state unknown")
            time.sleep(max(0, start + (index + 1) / 30 - time.monotonic()))
            msg = JointState()
            msg.header.stamp = node.get_clock().now().to_msg()
            msg.name = list(LEGACY_JOINT_NAMES)
            msg.position = frame.tolist()
            pub.publish(msg)
            rclpy.spin_once(node, timeout_sec=0)
        # Keep publisher alive briefly so the final command can be delivered.
        rclpy.spin_once(node, timeout_sec=0.2)
        print("Publication finished; physical completion is NOT confirmed")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
