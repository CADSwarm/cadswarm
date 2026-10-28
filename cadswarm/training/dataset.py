"""Build one content-addressed dataset directory from an export directory.

The id is a hash of the files, so the same inputs always produce the same directory name and a
training run can name exactly what it saw. The card and the manifest are the last two files
written; neither is part of the id.
"""
import hashlib
import json
import os
import shutil
import subprocess

from ..client import save_json
from . import SCHEMA_VERSION, render, split as split_mod
from . import episodes as episodes_mod
from . import state

TABLES = ("member", "cad_object", "param", "op", "task", "brief", "review", "team_event",
          "style_events")


def _read_raw(raw_dir, project_dir):
    out = {}
    for name in TABLES:
        path = os.path.join(raw_dir, project_dir, name + ".jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[name] = [json.loads(line) for line in f if line.strip()]
    with open(os.path.join(raw_dir, project_dir, "source.json"), encoding="utf-8") as f:
        out["source"] = json.load(f)
    return out


def _hash_tree(root):
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            digest.update(os.path.relpath(path, root).replace("\\", "/").encode())
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def build(raw_dir, out_root):
    """Read every exported project, build episodes, render, and write the dataset."""
    projects, all_episodes, conformance = {}, [], {}
    names = sorted(d for d in os.listdir(raw_dir)
                   if os.path.isdir(os.path.join(raw_dir, d)))
    for directory in names:
        raw = _read_raw(raw_dir, directory)
        source = raw.get("source") or {}
        if source.get("status") != "ok":
            continue
        project = source.get("project") or directory
        found = episodes_mod.build_episodes(raw, project)
        if not found:
            continue
        all_episodes += found
        projects[project] = {"episodes": len(found),
                             "steps": sum(len(e["steps"]) for e in found),
                             "source": source}
        _, final, _ = episodes_mod.replay_project(raw)
        conformance[project] = state.conformance(final, raw.get("cad_object") or [],
                                                 raw.get("param") or [])

    assignment = split_mod.assign(all_episodes)
    sft = render.sft_rows(all_episodes)
    pairs = render.pair_rows(all_episodes)
    cases = render.case_rows(all_episodes, assignment)
    biases = []
    if all_episodes:
        top = max(projects.items(), key=lambda kv: kv[1]["steps"])
        share = top[1]["steps"] / max(sum(p["steps"] for p in projects.values()), 1)
        biases.append(f"{top[0]} is {share:.0%} of all steps")
        actors = [s["actor"]["kind"] for e in all_episodes for s in e["steps"]]
        biases.append(f"{actors.count('agent')} agent steps vs {actors.count('human')} human steps")
    if assignment["mode"] == "temporal":
        biases.append("temporal split: steps from one project appear in train and test")

    # The id covers the content, so it is computed from a staging copy before the manifest exists.
    staging = os.path.join(out_root, ".staging")
    os.makedirs(staging, exist_ok=True)
    os.makedirs(os.path.join(staging, "episodes"), exist_ok=True)
    os.makedirs(os.path.join(staging, "eval"), exist_ok=True)
    for episode in all_episodes:
        save_json(os.path.join(staging, "episodes", episode["id"].replace("/", "__") + ".json"),
                  episode)
    render.write_jsonl(os.path.join(staging, "sft.jsonl"), sft)
    render.write_jsonl(os.path.join(staging, "pairs.jsonl"), pairs)
    render.write_jsonl(os.path.join(staging, "eval", "cases.jsonl"), cases)
    save_json(os.path.join(staging, "split.json"), assignment)
    save_json(os.path.join(staging, "conformance.json"), conformance)
    dataset_id = _hash_tree(staging)[:12]

    final_dir = os.path.join(out_root, dataset_id)
    os.makedirs(final_dir, exist_ok=True)
    for name in ("episodes", "eval"):
        os.makedirs(os.path.join(final_dir, name), exist_ok=True)
    for entry in sorted(os.listdir(staging)):
        src = os.path.join(staging, entry)
        dst = os.path.join(final_dir, entry)
        if os.path.isdir(src):
            for inner in sorted(os.listdir(src)):
                os.replace(os.path.join(src, inner), os.path.join(dst, inner))
        else:
            os.replace(src, dst)
    shutil.rmtree(staging, ignore_errors=True)

    manifest = {"dataset_id": dataset_id, "schema_version": SCHEMA_VERSION,
                "git_commit": _git_commit(), "projects": {k: {"episodes": v["episodes"],
                                                              "steps": v["steps"]}
                                                          for k, v in projects.items()},
                "counts": {"episodes": len(all_episodes), "sft_rows": len(sft),
                           "pairs": len(pairs), "eval_cases": len(cases)},
                "split": assignment, "conformance": conformance, "biases": biases}
    with open(os.path.join(final_dir, "dataset-card.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render.card(manifest))
    save_json(os.path.join(final_dir, "manifest.json"), manifest)
    manifest["path"] = final_dir
    return manifest
