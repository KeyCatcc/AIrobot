"""Strict decision parsing, with no repair, retry or execution side effects."""
import json


def behavior_schema(gestures, dances):
    return {"type": "object", "additionalProperties": False,
            "properties": {
                "behavior": {"type": "string", "enum": ["none", "gesture", "dance", "cancel_dance"]},
                "target": {"type": "string", "enum": list(dict.fromkeys(["none", "random", "current"] + list(gestures) + list(dances)))},
                "source": {"type": "string", "enum": ["none", "user_request", "reply_accompaniment"]}},
            "required": ["behavior", "target", "source"]}


def parse_behavior(text, gestures, dances):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"behavior", "target", "source"}:
        raise ValueError("Expected behavior, target and source")
    kind, target, source = data["behavior"], data["target"], data["source"]
    valid = ((kind == "none" and target == "none" and source == "none") or
             (kind == "gesture" and target in gestures and target != "none" and source in ("user_request", "reply_accompaniment")) or
             (kind == "dance" and target in list(dances) + ["random"] and bool(dances) and source == "user_request") or
             (kind == "cancel_dance" and target == "current" and source == "user_request"))
    if not valid:
        raise ValueError("Invalid behavior/target/source combination")
    return data


def validate_decision(data, allowed):
    if not isinstance(data, dict) or set(data) != {"gesture", "source"}:
        raise ValueError("Expected exactly gesture and source")
    gesture, source = data["gesture"], data["source"]
    if gesture not in allowed or source not in ("none", "user_request", "reply_accompaniment"):
        raise ValueError("Unknown gesture or source")
    if (gesture == "none") != (source == "none"):
        raise ValueError("Gesture and source contradict each other")
    return data


def parse_decision(text, schema):
    # Parse the entire schema-constrained response, never strip brtc/code fences.
    data = schema.model_validate_json(text).model_dump()
    return validate_decision(data, schema.model_json_schema()["properties"]["gesture"]["enum"])


def gesture_schema(tool_schema):
    """Model selects a gesture only; execution provenance belongs to the program."""
    return {"type": "object", "additionalProperties": False,
            "properties": {"gesture": tool_schema.model_json_schema()["properties"]["gesture"]},
            "required": ["gesture"]}


def combined_decision_schema(tool_schema):
    """Schema for the one-pass robot deployment decision.

    The model returns both the selected gesture and why it was selected.  Keeping
    provenance in the same constrained response avoids the former three-stage
    task/intent/accompaniment pipeline on CPU-only robots.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "gesture": tool_schema.model_json_schema()["properties"]["gesture"],
            "source": {
                "type": "string",
                "enum": ["user_request", "reply_accompaniment", "none"],
            },
        },
        "required": ["gesture", "source"],
    }


def parse_gesture(text, schema):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"gesture"}:
        raise ValueError("Expected only gesture")
    allowed = schema.model_json_schema()["properties"]["gesture"]["enum"]
    if data["gesture"] not in allowed:
        raise ValueError("Unknown gesture")
    return {"gesture": data["gesture"],
            "source": "none" if data["gesture"] == "none" else "reply_accompaniment"}
