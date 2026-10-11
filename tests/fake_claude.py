"""Stand-in for the `claude` CLI used by tests. Behaviour is chosen by FAKE_CLAUDE_SCENARIO.

It mimics `claude -p --output-format stream-json`: reads the prompt from stdin and
prints JSON lines. No network, no cost.
"""

import json
import os
import sys
import time


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main():
    if "--version" in sys.argv:
        print("9.9.9 (Claude Code)")
        return 0
    if "--help" in sys.argv:
        print("Usage: claude ... --permission-prompts <target> ...")
        return 0

    scenario = os.environ.get("FAKE_CLAUDE_SCENARIO", "success")
    prompt = sys.stdin.read()
    record = os.environ.get("FAKE_CLAUDE_RECORD")
    if record:
        with open(record, "w", encoding="utf-8") as fh:
            json.dump({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
                       "has_api_key": "ANTHROPIC_API_KEY" in os.environ}, fh, ensure_ascii=False)

    session = "sess-123"
    emit({"type": "system", "subtype": "init", "session_id": session, "model": "fake-model"})

    if scenario == "auth":
        print("Invalid API key · Please run /login", file=sys.stderr)
        return 1
    if scenario == "offline":
        for attempt in range(1, 11):
            emit({"type": "system", "subtype": "api_retry", "attempt": attempt, "max_retries": 10,
                  "error_status": None, "error": "unknown"})
            time.sleep(0.05)
        time.sleep(60)
        return 1
    if scenario == "hang":
        time.sleep(60)
        return 0
    if scenario == "loop":
        for i in range(100):
            emit({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "echo hi"}}]}})
            time.sleep(0.01)
        time.sleep(30)
        return 0
    if scenario == "rate_limit":
        emit({"type": "result", "subtype": "error", "is_error": True, "session_id": session,
              "result": "Claude usage limit reached. Your limit will reset at 5pm.", "permission_denials": []})
        return 1

    emit({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status"}}]}})
    emit({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "clean", "is_error": False}]}})

    denials = []
    if scenario == "denial":
        denials = [{"tool_name": "Bash", "tool_use_id": "t2", "tool_input": {"command": "npm install left-pad"}},
                   {"tool_name": "Bash", "tool_use_id": "t3", "tool_input": {"command": "Set-MpPreference -DisableRealtimeMonitoring $true"}}]
    text = "Готово: рабочее дерево чистое." if scenario != "echo" else prompt[-200:]
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session, "result": text,
          "permission_denials": denials, "num_turns": 2})
    return 0


if __name__ == "__main__":
    sys.exit(main())
