#!/usr/bin/env python3
"""Stand-in for `codex exec --json` emitting the event stream recorded from codex-cli 0.160.0."""
import json
import os
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("codex-cli 9.9.9")
    sys.exit(0)
prompt = sys.stdin.read()
out = args[args.index("-o") + 1]
with open(os.environ["FAKE_ARGV_LOG"], "w") as f:
    json.dump({"argv": args, "prompt": prompt, "env_key": os.environ.get("OPENAI_API_KEY")}, f)


def emit(event):
    print(json.dumps(event), flush=True)


emit({"type": "thread.started", "thread_id": "01a1-thread"})
emit({"type": "turn.started"})
if "HANG" in prompt:
    time.sleep(600)
if "AUTHFAIL" in prompt:
    emit({"type": "error", "message": "unexpected status 401 Unauthorized: missing bearer"})
    emit({"type": "turn.failed", "error": {"message": "unexpected status 401 Unauthorized: missing bearer"}})
    sys.exit(1)
if "OVERLOADED" in prompt:
    emit({"type": "turn.failed", "error": {"message": "stream disconnected before completion: server overloaded"}})
    sys.exit(1)
if "NOFINISH" in prompt:
    sys.exit(2)
envelope = {"schema_version": "1", "kind": "code-review", "verdict": "PASS", "summary": "ok", "findings": [], "payload": {}}
emit({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Reviewing."}})
emit({"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "cat a.py",
                                         "aggregated_output": "x", "exit_code": 0, "status": "completed"}})
text = "```json\n" + json.dumps(envelope) + "\n```"
emit({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": text}})
with open(out, "w") as f:
    f.write(text)
emit({"type": "turn.completed", "usage": {"input_tokens": 2000, "cached_input_tokens": 500, "output_tokens": 100}})
