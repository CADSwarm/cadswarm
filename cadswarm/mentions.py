"""@mentions in the FreeCAD chat box: "@lid-bot make the lid 20 mm" gives that AI helper a task, "@team ..." hands a
whole request to the team, "@all ..." tells every helper. Stdlib only, so it is tested without FreeCAD."""
import re

MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9_-]+)")


def parse_mentions(text, helper_names):
    """(helpers named with @, {"team", "all"} asked for, other @names) in the order they appear."""
    by_lower = {n.lower(): n for n in helper_names}
    helpers, special, unknown = [], set(), []
    for m in MENTION.finditer(text):
        word = m.group(1).lower()
        if word in by_lower:
            if by_lower[word] not in helpers:
                helpers.append(by_lower[word])
        elif word == "team":
            special.add("team")
        elif word in ("all", "helpers", "everyone"):
            special.add("all")
        else:
            unknown.append(m.group(1))
    return helpers, special, unknown


def strip_mentions(text):
    """The request without its @names: what goes in the task's title."""
    return re.sub(r"\s{2,}", " ", MENTION.sub("", text)).strip(" ,:")


def mention_prefix(before_cursor):
    """The partial name after an @ right before the cursor ("" right after the @), or None."""
    m = re.search(r"(?:^|\s)@([A-Za-z0-9_-]*)$", before_cursor)
    return m.group(1) if m else None
