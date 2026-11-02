import json
from pathlib import Path


def read_commands(run):
    ids = [step["id"] for step in run["definition"]["steps"]]
    result = {"runId": run["id"], "steps": {key: [] for key in ids}, "preparation": []}
    def collect(event):
        if event.get("type") != "command":
            return
        command = {"at": event.get("at"), "text": event["text"]}
        target = event.get("stage")
        if target in result["steps"]:
            result["steps"][target].append(command)
        elif target is None:
            result["preparation"].append(command)

    journal = Path(run["directory"]) / "events.jsonl"
    if journal.is_file():
        with journal.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                collect(event)
    else:
        for event in run.get("events", []):
            collect(event)
    return result
