"""Episodes -> the files a trainer and an eval harness read.

Only episodes in the training split are rendered into training rows: rendering every episode
while holding some out for evaluation measures memorisation.
"""
import json

from . import labels, prompts
from .state import PARAMS, summary


def _previous(steps, index):
    return [{"op": s["op"], "args": s["args"]} for s in steps[max(0, index - 8):index]]


def _drop(dropped, kind, ident, index, code, reason):
    """Record a skipped row when the caller asked for the reasons, never silently."""
    if dropped is not None:
        dropped.append({"kind": kind, "id": ident, "index": index, "code": code,
                        "reason": reason})


def sft_rows(episodes, train_ids, dropped=None):
    """One row per usable step in the training split.

    Only a mutating op is a build step: the prompt defines those and nothing else. A target over
    `prompts.MAX_TARGET` is dropped rather than handed to a trainer that would truncate it.
    """
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
            if step["op"] not in prompts.BUILD_OPS:
                _drop(dropped, "sft", episode["id"], step["index"], "non_build_op",
                      f"op {step['op']!r} is not a build op")
                continue
            target = prompts.render_assistant(step["op"], step["args"])
            if len(target) > prompts.MAX_TARGET:
                _drop(dropped, "sft", episode["id"], step["index"], "target_too_long",
                      f"target is {len(target)} chars, over prompts.MAX_TARGET")
                continue
            rows.append({"messages": [
                {"role": "system", "content": prompts.SYSTEM},
                {"role": "user", "content": prompts.render_request(
                    episode["request"], summary(step["state_before"]),
                    _previous(episode["steps"], step["index"]))},
                {"role": "assistant", "content": target}],
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


def case_rows(episodes, split, dropped=None):
    """Held-out briefs for replay: done build ops only, and only cases that can measure something.

    A reference that names no object gives a do-nothing policy a perfect ceiling, and a query op is
    not a step the worker would ever run as a build. The case keeps the episode's start state, so
    the harness knows the precondition the request does not spell out.
    """
    test = set(split["test"])
    rows = []
    for episode in episodes:
        if episode["id"] not in test:
            continue
        reference = [{"op": s["op"], "args": s["args"]} for s in episode["steps"]
                     if s.get("status") == "done" and s["op"] in prompts.BUILD_OPS]
        if not any(name != PARAMS for name in (episode["final"].get("objects") or {})):
            _drop(dropped, "case", episode["id"], None, "no_object", "reference has no object")
            continue
        if not reference:
            _drop(dropped, "case", episode["id"], None, "no_build_op", "no done build op")
            continue
        start = (episode["steps"] or [{}])[0].get("state_before") or {"objects": {}, "params": {}}
        rows.append({"id": episode["id"], "request": episode["request"], "state": start,
                     "reference": reference, "final": episode["final"],
                     "stl_sha256": episode["final"]["stl_sha256"]})
    return rows


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
    warnings = [(name, warning) for name, info in manifest["raw"].items()
                for warning in info.get("warnings") or []]
    if warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {name}: {warning}" for name, warning in warnings]
    if manifest.get("drops"):
        drops = manifest["drops"]
        lines += ["", "## Dropped", "",
                  "Rows and cases left out rather than rendered as if they measured something.", ""]
        lines += [f"- sft rows dropped ({code}): {count}"
                  for code, count in sorted((drops.get("sft") or {}).items())]
        for kind in ("episodes", "cases"):
            lines += [f"- {kind[:-1]} {item['id']}: {item['reason']}"
                      for item in drops.get(kind) or []]
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
