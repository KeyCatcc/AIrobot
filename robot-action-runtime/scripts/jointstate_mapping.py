"""Translate legacy relative degrees into the existing bridge's relative radians."""

import math


def convert_frames(frames, names, motor_table, bridge_parameters):
    motors = {row["joint"]: row for row in motor_table["motors"]}
    mapping = {str(v): int(k) for k, v in bridge_parameters["joint_mapping"].items()}
    reverse = {int(k): v for k, v in bridge_parameters["joint_direction_reverse"].items()}
    offsets = {int(k): float(v) for k, v in bridge_parameters.get("joint_offsets", {}).items()}
    home = float(motor_table.get("home_deg", 180))
    coefficients = []
    for name in names:
        spec = motors[name]
        index = mapping[name]
        if int(spec["index"]) != index:
            raise ValueError("Motor index mismatch: " + name)
        if not isinstance(reverse[index], bool):
            raise ValueError("Invalid reverse flag: " + name)
        direction = -1 if reverse[index] else 1
        gain = float(spec.get("sign", 1)) * float(spec.get("scale", 1))
        bias = home - 180 - offsets.get(index, 0)
        if not all(math.isfinite(v) for v in (gain, bias)) or gain == 0:
            raise ValueError("Invalid motor mapping: " + name)
        # Current player starts at relative zero; do not introduce an untested
        # nonzero initial target to compensate differing neutral calibrations.
        if abs(bias) > 1e-9:
            raise ValueError("Neutral/offset mismatch requires calibration: " + name)
        coefficients.append((gain / direction, spec))
    output = []
    for frame in frames:
        if len(frame) != len(names):
            raise ValueError("Frame width mismatch")
        row = []
        for value, (gain, spec) in zip(frame, coefficients):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Nonfinite angle")
            target = home + value * float(spec.get("sign", 1)) * float(spec.get("scale", 1))
            if target < float(spec.get("min_deg", -math.inf)) or target > float(spec.get("max_deg", math.inf)):
                raise ValueError("Motor target outside configured limits: " + spec["joint"])
            row.append(math.radians(value * gain))
        output.append(row)
    return output, [gain for gain, _ in coefficients]
