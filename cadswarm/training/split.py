"""Split by project, never by step: one document per project means steps leak across a step split."""

TEST_SHARE = 0.2
VAL_SHARE = 0.1


def assign(episodes):
    by_project = {}
    for episode in episodes:
        by_project.setdefault(episode["project"], []).append(episode["id"])
    projects = sorted(by_project)
    if len(projects) >= 3:
        val, test = projects[-2], projects[-1]
        return {"mode": "project", "projects": {"val": val, "test": test},
                "train": [i for p in projects if p not in (val, test) for i in by_project[p]],
                "val": by_project[val], "test": by_project[test]}
    ordered = sorted(episodes, key=lambda e: (e["first_ms"], e["id"]))
    n = len(ordered)
    cut_test = max(n - max(1, round(n * TEST_SHARE)), 0)
    cut_val = max(cut_test - max(1, round(n * VAL_SHARE)), 0)
    return {"mode": "temporal", "projects": {},
            "train": [e["id"] for e in ordered[:cut_val]],
            "val": [e["id"] for e in ordered[cut_val:cut_test]],
            "test": [e["id"] for e in ordered[cut_test:]]}
