"""Separate user-intent interpretation from reply accompaniment. No execution here."""
import json


TASK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"kind": {"type": "string", "enum": ["forbid_all", "conversation", "body_request_or_restriction"]}},
    "required": ["kind"],
}


def parse_task(text):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"kind"} or data["kind"] not in TASK_SCHEMA["properties"]["kind"]["enum"]:
        raise ValueError("Invalid task classification")
    return data["kind"]


def intent_schema(names):
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "intent": {"type": "string", "enum": ["forbid_all", "unavailable", "no_request"] +
                       ["request:" + n for n in names if n != "none"]},
            "forbidden_actions": {"type": "array", "items": {"type": "string", "enum": [n for n in names if n != "none"]}},
        }, "required": ["intent", "forbidden_actions"],
    }


def parse_intent(text, names):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"intent", "forbidden_actions"}:
        raise ValueError("Invalid user intent fields")
    value = data["intent"]
    modes = ["forbid_all", "unavailable", "no_request"]
    requests = {"request:" + n: n for n in names if n != "none"}
    if not isinstance(value, str) or value not in modes + list(requests):
        raise ValueError("Invalid user intent")
    forbidden = data["forbidden_actions"]
    if not isinstance(forbidden, list) or any(n == "none" or n not in names for n in forbidden):
        raise ValueError("Invalid forbidden actions")
    return {"mode": "request" if value in requests else value,
            "action": requests.get(value, "none"), "forbidden_actions": forbidden}


def priority_decision(intent):
    if intent["mode"] in ("forbid_all", "unavailable"):
        return {"gesture": "none", "source": "none"}
    if intent["mode"] == "request":
        if intent["action"] in intent["forbidden_actions"]:
            return {"gesture": "none", "source": "none"}
        return {"gesture": intent["action"], "source": "user_request"}
    return None
