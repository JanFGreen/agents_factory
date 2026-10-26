#!/usr/bin/env python3
"""Stand-in for the `claude` CLI that follows the documented stream-json contract."""
import json
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("9.9.9 (Fake Claude)")
    sys.exit(0)
if "--help" in args:
    print("--resume --session-id --json-schema --max-budget-usd --effort --output-format stream-json")
    sys.exit(0)
prompt = args[args.index("-p") + 1]
session = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
model = args[args.index("--model") + 1]
print(json.dumps({"type": "system", "subtype": "init", "session_id": session, "model": model}), flush=True)
if "HANG" in prompt:
    time.sleep(600)
if "CRASH" in prompt:
    sys.exit(3)
envelope = {"schema_version": "1", "kind": "code-review", "verdict": "PASS", "summary": "ok", "findings": [], "payload": {}}
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": "done",
                  "structured_output": envelope, "total_cost_usd": 0.0123,
                  "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2}}), flush=True)
