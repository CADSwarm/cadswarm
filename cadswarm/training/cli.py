"""python -m cadswarm.training export|build|eval

export  one or more project folders -> raw JSONL
build   a raw directory -> a content-addressed dataset
eval    a dataset's held-out cases -> a scorecard
"""
import argparse
import json
import os
import sys

from . import dataset, eval_replay, export

SUBCOMMANDS = ("export", "build", "eval")


def _export(args):
    infos = [export.export_project(path, args.out) for path in args.project]
    for info in infos:
        print(f"{info['project']}: {info['status']} {info['tables']}")
    return 0 if any(i["status"] == "ok" for i in infos) else 1


def _build(args):
    manifest = dataset.build(args.raw, args.out)
    print(json.dumps({k: manifest[k] for k in ("dataset_id", "counts", "split")}, indent=2))
    return 0


def _dataset_meta(root):
    """The dataset and split a scorecard measured, from the manifest beside the cases."""
    try:
        with open(os.path.join(root, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return {"dataset_id": os.path.basename(os.path.abspath(root)), "split_mode": None}
    return {"dataset_id": manifest.get("dataset_id", ""),
            "split_mode": (manifest.get("split") or {}).get("mode")}


def _eval(args):
    root = os.path.join(args.dataset, "eval", "cases.jsonl")
    with open(root, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    policies = {"reference": lambda c: eval_replay.ScriptedPolicy(c["reference"]),
                "random": lambda c: eval_replay.RandomPolicy(seed=1)}
    policy = policies[args.policy]
    rows = [eval_replay.replay(case, policy(case), python=args.python, script=args.script)
            for case in cases]
    report = eval_replay.report(rows, policy=args.policy, meta=_dataset_meta(args.dataset))
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m cadswarm.training")
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export", help="project folders -> raw JSONL")
    p_export.add_argument("--project", action="append", required=True)
    p_export.add_argument("--out", required=True)
    p_export.set_defaults(run=_export)
    p_build = sub.add_parser("build", help="raw JSONL -> a dataset")
    p_build.add_argument("--raw", required=True)
    p_build.add_argument("--out", required=True)
    p_build.set_defaults(run=_build)
    p_eval = sub.add_parser("eval", help="replay a dataset's held-out cases")
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--policy", choices=("reference", "random"), default="random")
    p_eval.add_argument("--python", default=None)
    p_eval.add_argument("--script", default=None)
    p_eval.add_argument("--out", default=None)
    p_eval.set_defaults(run=_eval)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
