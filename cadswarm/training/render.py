"""Episodes -> the files a trainer and an eval harness read.

Only episodes in the training split are rendered into training rows: rendering every episode
while holding some out for evaluation measures memorisation.
"""
import json

from . import labels, prompts
from .state import summary


def _previous(steps, index):
    return [{"op": s["op"], "args": s["args"]} for s in steps[max(0, index - 8):index]]


def sft_rows(episodes, train_ids):
    """One row per usable step in the training split."""
    train = set(train_ids)
    rows = []
    for episode in episodes:
        if episode["id"] not in train:
            continue
        step_labels = episode["labels"]["step_labels"]
        for step in episode["steps"]:
            if step_labels.get(str(step["index"])) not in ("good", "neutral"):
                continue
            if step["state_confidence"] != "full":
                continue
            rows.append({"messages": [
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.render_request(
                    episode["request"], summary(step["state_before"]),
                    _previous(episode["steps"], step["index"]))},
                {"role": "assistant", "content": prompts.render_assistant(step["op"], step["args"])}],
                "meta": {"episode": episode["id"], "index": step["index"],
                         "actor_kind": step["actor"]["kind"],
                         "quality": episode["labels"]["quality"]}})
    return rows


def pair_rows(episodes, train_ids):
    """Correction pairs and accepted-vs-failed episode pairs, training split only."""
    train = set(train_ids)
    rows = [pair for episode in episodes if episode["id"] in train
            for pair in labels.pairs(episode)]
    accepted = [e for e in episodes if e["id"] in train and e["labels"]["outcome"] == "accepted"]
    failed = [e for e in episodes if e["id"] in train
              and e["labels"]["outcome"] in ("failed", "blocked")]
    for winner, loser in zip(accepted, failed):
        rows.append({"signal": "outcome", "episode": winner["id"],
                     "chosen": {"objects": winner["final"]["objects"]},
                     "rejected": {"objects": loser["final"]["objects"]}})
    return rows


def case_rows(episodes, split):
    """Held-out briefs for replay: the reference sequence is the ceiling, the initial state is empty."""
    test = set(split["test"])
    return [{"id": e["id"], "request": e["request"],
             "state": {"objects": {}, "params": {}},
             "reference": [{"op": s["op"], "args": s["args"]} for s in e["steps"]],
             "final": e["final"], "stl_sha256": e["final"]["stl_sha256"]}
            for e in episodes if e["id"] in test]


def card(manifest):
    """The dataset card: counts, provenance, and the biases a reader needs to distrust nothing."""
    lines = [f"# Dataset {manifest['dataset_id']}", "",
             f"Schema {manifest['schema_version']} · built from {len(manifest['projects'])} project(s).", ""]
    lines += ["| Project | Episodes | Steps |", "|---|---|---|"]
    for name, counts in manifest["projects"].items():
        lines.append(f"| {name} | {counts['episodes']} | {counts['steps']} |")
    lines += ["", "## Split", "", f"Mode: {manifest['split']['mode']}. "
              f"Train {len(manifest['split']['train'])}, val {len(manifest['split']['val'])}, "
              f"test {len(manifest['split']['test'])} episodes.", "", "## Counts", ""]
    lines += [f"- {name}: {value}" for name, value in manifest["counts"].items()]
    lines += ["", "## Biases", ""]
    lines += [f"- {item}" for item in manifest["biases"]]
    if manifest["skipped"]:
        lines += ["", "## Skipped", ""]
        lines += [f"- {item['project']}: {item['reason']}" for item in manifest["skipped"]]
    lines += ["", "## Conformance", "", "How closely the replayed state matches the live model table.", ""]
    lines += [f"- {name}: {report['fraction']}" for name, report in manifest["conformance"].items()]
    return "\n".join(lines) + "\n"


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path, value):
    """Sorted keys, so a hashed file does not depend on dict construction order."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
