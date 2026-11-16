"""Semantic selection with remote execution; operator owns endpoint and token."""
import json
import os
from typing import Literal, Type
import urllib.request
import urllib.error
import uuid
import random
from contextvars import ContextVar

ACTION_REQUEST_ID = ContextVar("action_request_id", default=None)

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, create_model
from gesture_decision import validate_decision


class RemoteGestureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gesture: Literal["挥手/招手", "none"]
    source: Literal["user_request", "reply_accompaniment", "none"] = "reply_accompaniment"


class RemoteGestureTool(BaseTool):
    dances: list = []
    dance_catalog_version: str = ""

    def execution_state(self):
        url = os.environ.get("ROBOT_ACTION_URL", "http://127.0.0.1:8766").rstrip("/")
        req = urllib.request.Request(url + "/execution", headers={
            "Authorization": "Bearer " + os.environ["ROBOT_ACTION_TOKEN"]})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2) as response:
            return json.load(response)

    def run_behavior(self, decision):
        kind, target = decision["behavior"], decision["target"]
        if kind in ("none", "gesture"):
            return self._run("none" if kind == "none" else target, decision["source"])
        url = os.environ.get("ROBOT_ACTION_URL", "http://127.0.0.1:8766").rstrip("/")
        if kind == "dance":
            names = [row["name"] for row in self.dances]
            target = random.choice(names) if target == "random" else target
            if target not in names:
                raise ValueError("Dance not enabled")
            path = "/dances"
            payload = {"request_id": ACTION_REQUEST_ID.get() or uuid.uuid4().hex,
                       "dance": target, "catalog_version": self.dance_catalog_version}
        else:
            path, payload = "/cancel", {}
        req = urllib.request.Request(url + path, data=json.dumps(payload).encode(), headers={
            "Authorization": "Bearer " + os.environ["ROBOT_ACTION_TOKEN"], "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            result = {"status": "remote_error", "http_status": exc.code,
                      "error_body": exc.read(4096).decode("utf-8", errors="replace")}
        result.update(source="user_request", hardware_executed=None, physical_completion_confirmed=False)
        return json.dumps(result, ensure_ascii=False)
    catalog_version: str = ""
    action_hints: str = ""
    name: str = "perform_gesture"
    description: str = (
        "Select by the meaning of the ROBOT reply prefix. 挥手/招手 is a waving "
        "gesture accompanying an actual greeting, welcome or farewell addressed "
        "to someone, or explicit waving to someone. A movement command is not required. "
        "Choose none for merely discussing greetings, facts, negations, quotations, "
        "uncertainty or unavailable gestures. Only library name 挥手/招手 is enabled."
    )
    args_schema: Type[BaseModel] = RemoteGestureInput

    def _run(self, gesture: str, source: str = "reply_accompaniment") -> str:
        allowed = self.args_schema.model_json_schema()["properties"]["gesture"]["enum"]
        validate_decision({"gesture": gesture, "source": source}, allowed)
        if gesture == "none":
            return json.dumps({"status": "no_action", "source": "none", "hardware_executed": False})
        token = os.environ["ROBOT_ACTION_TOKEN"]
        url = os.environ.get("ROBOT_ACTION_URL", "http://172.16.20.64:8766").rstrip("/")
        key = ACTION_REQUEST_ID.get() or uuid.uuid4().hex
        request = urllib.request.Request(
            url + "/actions", data=json.dumps({"request_id": key, "action": gesture,
                                               "catalog_version": self.catalog_version}).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Preserve diagnostic evidence; never retry a possibly accepted action.
            result = {"status": "remote_result_unknown", "request_id": key,
                      "error_type": "HTTPError", "http_status": exc.code,
                      "remote_error_body": exc.read(1024).decode("utf-8", errors="replace"),
                      "retry_performed": False}
        except Exception as exc:
            # A timeout does not prove that the remote side did not execute.
            result = {"status": "remote_result_unknown", "request_id": key,
                      "error_type": type(exc).__name__, "retry_performed": False}
        result["hardware_executed"] = None
        result["physical_completion_confirmed"] = False
        result["source"] = source
        return json.dumps(result, ensure_ascii=False)


def create_remote_tool():
    """Fetch the robot's authoritative enabled list once at startup; fail closed."""
    url = os.environ.get("ROBOT_ACTION_URL", "http://172.16.20.64:8766").rstrip("/")
    req = urllib.request.Request(url + "/catalog", headers={
        "Authorization": "Bearer " + os.environ["ROBOT_ACTION_TOKEN"]})
    with urllib.request.urlopen(req, timeout=5) as response:
        catalog = json.loads(response.read())
    rows = catalog["actions"]
    names = [row["name"] for row in rows]
    if not names or len(set(names)) != len(names) or "none" in names:
        raise ValueError("Invalid robot catalog")
    schema = create_model(
        "CatalogGestureInput", __config__=ConfigDict(extra="forbid"),
        gesture=(Literal[tuple(names + ["none"])], ...),
        source=(Literal["user_request", "reply_accompaniment", "none"], ...))
    description = "Choose by meaning, not keywords. Allowed library names and meanings: " + json.dumps(
        [{"name": row["name"], "description": row["description"]} for row in rows], ensure_ascii=False)
    description += (
        "; Select a gesture when its purpose reasonably matches the interaction context. "
        "A direct movement command is not required. Respect action prohibitions; "
        "quoted movements are not commands. Choose none when no gesture reasonably matches, "
        "not merely because several choices could fit. Never invent a spatial direction."
    )
    # The deployment path uses this compact variant inside a single Ollama
    # prompt.  The full description remains available to the legacy staged
    # decision path, but is unnecessarily expensive on a CPU-only robot.
    hints = []
    for row in rows:
        summary = " ".join(row["description"].split())[:48]
        hints.append(f'{row["name"]}:{summary}')
    return RemoteGestureTool(args_schema=schema, description=description,
                             dances=[row for row in catalog.get("dances", []) if row.get("enabled") and row.get("rai_exposed")],
                             dance_catalog_version=catalog.get("dance_catalog_version", ""),
                             catalog_version=catalog["catalog_version"],
                             action_hints="；".join(hints))
