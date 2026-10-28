"""Build one content-addressed dataset directory from an export directory.

The id is a hash of the files, so the same inputs always produce the same directory name and a
training run can name exactly what it saw. A raw project that cannot be read, is skipped, or is a
leftover `*.new`/`*.old` directory from an interrupted export is recorded in `skipped`, never
fatal.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

from . import SCHEMA_VERSION, render, split as split_mod
from . import episodes as episodes_mod
from . import state

TABLES = ("member", "cad_object", "param", "op", "task", "brief", "review", "team_event",
          "style_events")
SKIP_SUFFIXES = (".new", ".old")


def _read_source(raw_dir, directory):
    with open(os.path.join(raw_dir, directory, "source.json"), encoding="utf-8") as f:
        return json.load(f)


def _read_tables(raw_dir, directory, source):
    out = {"source": source}
    for name in TABLES:
        path = os.path.join(raw_dir, directory, name + ".jsonl")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[name] = [json.loads(line) for line in f if line.strip()]
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
    os.makedirs(out_root, exist_ok=True)
    projects, all_episodes, conformance, skipped, raw_info = {}, [], {}, [], {}
    names = sorted(d for d in os.listdir(raw_dir)
                   if os.path.isdir(os.path.join(raw_dir, d)))
    usable = []
    for name in names:
        if name.startswith(".") or name.endswith(SKIP_SUFFIXES):
            skipped.append({"project": name, "reason": "leftover or hidden directory"})
            continue
        usable.append(name)
    names = usable
    for directory in names:
        try:
            source = _read_source(raw_dir, directory)
        except (OSError, ValueError) as e:
            skipped.append({"project": directory, "reason": f"no readable source.json: {e}"})
            continue
        if not isinstance(source, dict):
            skipped.append({"project": directory, "reason": "source.json is not an object"})
            continue
        if source.get("status") != "ok":
            skipped.append({"project": source.get("project") or directory,
                            "reason": str((source or {}).get("status") or "no status")})
            continue
        try:
            raw = _read_tables(raw_dir, directory, source)
        except (OSError, ValueError) as e:
            skipped.append({"project": source.get("project") or directory,
                            "reason": f"unreadable table: {e}"})
            continue
        project = source.get("project") or directory
        found = episodes_mod.build_episodes(raw, project)
        if not found:
            skipped.append({"project": project, "reason": "no episodes"})
            continue
        all_episodes += found
        projects[project] = {"episodes": len(found),
                             "steps": sum(len(e["steps"]) for e in found)}
        raw_info[project] = {"schema_sha256": source.get("schema_sha256", ""),
                             "tables": source.get("tables", {})}
        _, final, _ = episodes_mod.replay_project(raw)
        conformance[project] = state.conformance(final, raw.get("cad_object") or [],
                                                 raw.get("param") or [])

    assignment = split_mod.assign(all_episodes)
    sft = render.sft_rows(all_episodes, assignment["train"])
    pairs = render.pair_rows(all_episodes, assignment["train"])
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

    staging = tempfile.mkdtemp(dir=out_root, prefix=".build-")
    try:
        os.makedirs(os.path.join(staging, "episodes"), exist_ok=True)
        os.makedirs(os.path.join(staging, "eval"), exist_ok=True)
        for episode in all_episodes:
            render.write_json(os.path.join(staging, "episodes",
                                           episode["id"].replace("/", "__") + ".json"), episode)
        render.write_jsonl(os.path.join(staging, "sft.jsonl"), sft)
        render.write_jsonl(os.path.join(staging, "pairs.jsonl"), pairs)
        render.write_jsonl(os.path.join(staging, "eval", "cases.jsonl"), cases)
        render.write_json(os.path.join(staging, "split.json"), assignment)
        render.write_json(os.path.join(staging, "conformance.json"), conformance)
        dataset_id = _hash_tree(staging)[:12]

        final_dir = os.path.join(out_root, dataset_id)
        os.makedirs(final_dir, exist_ok=True)
        for entry in sorted(os.listdir(staging)):
            src = os.path.join(staging, entry)
            dst = os.path.join(final_dir, entry)
            if os.path.isdir(src):
                os.makedirs(dst, exist_ok=True)
                for inner in sorted(os.listdir(src)):
                    os.replace(os.path.join(src, inner), os.path.join(dst, inner))
            else:
                os.replace(src, dst)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    manifest = {"dataset_id": dataset_id, "schema_version": SCHEMA_VERSION,
                "git_commit": _git_commit(), "projects": projects, "raw": raw_info,
                "counts": {"episodes": len(all_episodes), "sft_rows": len(sft),
                           "pairs": len(pairs), "eval_cases": len(cases)},
                "split": assignment, "conformance": conformance, "biases": biases,
                "skipped": skipped}
    with open(os.path.join(final_dir, "dataset-card.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(render.card(manifest))
    render.write_json(os.path.join(final_dir, "manifest.json"), manifest)
    manifest["path"] = final_dir
    return manifest
