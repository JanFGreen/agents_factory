#!/usr/bin/env python3
"""Stand-in for `opencode run --format json` emitting the event stream recorded from opencode 1.19.0."""
import json
import os
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("9.9.9")
    sys.exit(0)
prompt = args[-1]
config = json.load(open(os.environ["OPENCODE_CONFIG"]))
with open(os.environ["FAKE_ARGV_LOG"], "w") as f:
    json.dump({"argv": args, "config": config, "env": {k: os.environ.get(k) for k in (
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "GOOGLE_GENERATIVE_AI_API_KEY", "FACTORY_ENDPOINT_KEY", "GH_TOKEN")}}, f)
session = "ses_fake"


def emit(kind, part):
    print(json.dumps({"type": kind, "timestamp": 1, "sessionID": session, "part": part}), flush=True)


def finish(reason, cost):
    emit("step_finish", {"type": "step-finish", "reason": reason, "cost": cost,
                         "tokens": {"total": 1300, "input": 1000, "output": 200, "reasoning": 0, "cache": {"write": 0, "read": 100}}})


emit("step_start", {"type": "step-start"})
if "HANG" in prompt:
    time.sleep(600)
if "QUOTA" in prompt:
    print(json.dumps({"type": "error", "timestamp": 1, "sessionID": session, "error": {"name": "APIError", "data": {
        "message": "Your prepayment credits are depleted.", "statusCode": 402, "isRetryable": False}}}), flush=True)
    sys.exit(1)
emit("tool_use", {"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "ls"}, "output": "a.py"}})
finish("tool-calls", 0.001)
emit("step_start", {"type": "step-start"})
envelope = {"schema_version": "1", "kind": "code-review", "verdict": "PASS", "summary": "ok", "findings": [], "payload": {}}
emit("text", {"type": "text", "text": "Done.\n```json\n" + json.dumps(envelope) + "\n```"})
finish("stop", 0.002)
