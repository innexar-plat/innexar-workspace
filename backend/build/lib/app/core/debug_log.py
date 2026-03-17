"""Debug session logging (NDJSON to file). Only writes when DEBUG_LOG_PATH is set."""
import json
import os
import time


def debug_log(location: str, message: str, data: dict, hypothesis_id: str, run_id: str = "run1") -> None:
    path = os.environ.get("DEBUG_LOG_PATH")
    if not path:
        return
    try:
        payload = {
            "sessionId": "712011",
            "id": f"log_{int(time.time() * 1000)}",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
        }
        with open(path, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
