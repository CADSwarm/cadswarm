# Learned design style: a triage inbox

**Date:** 2026-10-26
**Status:** approved for v1

## Problem

The host learns design-style rules from completed ops and briefs. Rules that are not yet strong
enough to guide agents become `candidate` rows and wait for a person to confirm or dismiss them.
The Learned design style dialog lists only the first 12 pending rules, one row and two buttons each;
the Team tab shows 4 and a "more in Tools" line. A busy project can accumulate dozens (seen: ~70),
so most rules are unreachable and the visible ones invite blind clicking. The two decisions are
also not really independent: a size convention and the correction pattern for the same Param are
two rows describing one fact, and each needs its own click.

## Goals

- Make one decision cover one thing learned, not one stored rule row.
- Put the whole backlog in front of a person, grouped and ordered, with a deterministic
  recommendation for each decision.
- Let a person act on a whole group or the whole recommended set in one visible action.
- Let undecided weak rules leave the inbox on their own, recoverably.
- Keep the documented promise that learning needs no account, keys or network. No model call in v1.

## Non-goals

- A model-based classifier. The recommendation is a pure function with a single seam
  (`recommend(kind, evidence, confidence, source)`) if one is added later.
- Changing how rules are learned, gated, recalled, or worded.
- Changing team-setting suggestions, which stay individual cards on the Team tab.
- A schema migration.

## Design

### Decisions

`style_memory.decisions(rules)` groups stored rules by subject:

| Subject | Rules merged |
| --- | --- |
| `param:<alias>` | `param_convention` for the alias and `correction_pattern` with key `param:<alias>` |
| `edit:<type>` | `correction_pattern` for edits to a part type |
| `part:<family>` | `preferred_component` for a standard-part family or library component |
| `habit:<key>` | `modelling_practice` |
| `problem:<board>:<norm>` | `recurring_problem` for a normalized finding |
| `proposal:<id>` | a helper's `teach_style` proposal (`source='proposed'`) |
| `taught:<id>` | a person's own rule (never pending) |

A decision carries the rules' ids, the strongest rule's statement, the other statements as
`also`, merged evidence (max `n`, max `confidence`, summed people/occasions are not needed), and one
section kind. It belongs to the section of its strongest rule, except `param:<alias>` which belongs
to shared sizes even when the correction row is stronger.

Resolving a decision resolves every rule in it. This is the guarantee that the same fact cannot
come back as a second card.

### Recommendation

`recommend` is deterministic and never auto-applies:

| Kind | `confirm` when | `dismiss` when |
| --- | --- | --- |
| `correction_pattern` | `n >= 4` (two corrections; corrections count double) | never (a single correction stays `look`) |
| `recurring_problem` | `n >= 4` (seen in two designs) | `n <= 2` (one design only) |
| `param_convention` | `confidence >= 0.45` and `n >= 5` | `n <= 1` and `confidence < 0.25` |
| `preferred_component` | `n >= 5` and share >= 0.6 | `n <= 1` |
| `modelling_practice` | `confidence >= 0.5` and `n >= 5` | `n <= 1` |
| `proposed` | never | never |

Everything else is `look`. A group with several rules uses the strongest recommendation
(`confirm` beats `look` beats `dismiss`).

### Expiry

`STATUSES` gains `expired`. During `distil` and when the inbox is read, candidates whose last
evidence (`seen_at`) is more than `CANDIDATE_TTL_DAYS` (14) old become `expired`, with
`resolved_n` set to their current evidence so the existing "comes back once evidence doubles"
rule revives them. Expired rules never enter `context()`. No column is added: `seen_at` and
`resolved_n` already exist. `distil` treats `expired` like `dismissed` when deciding whether to
revive.

### API

`StyleMemory.inbox()` returns:

```json
{
  "rules": 70, "decisions": 34, "ready": 24, "weak": 6,
  "sections": [
    {"kind": "param_convention", "label": "Shared sizes", "count": 18,
     "decisions": [{"subject": "param:wall", "title": "Params.wall is 2.4 mm",
                    "detail": "seen 6x by 2 people", "also": [], "ids": ["r_..."],
                    "confidence": 0.62, "recommendation": "confirm"}]}
  ],
  "used": [{"subject": "...", "title": "...", "status": "confirmed", "ids": ["..."]}],
  "history": [{"subject": "...", "title": "...", "status": "expired", "ids": ["..."]}]
}
```

`StyleMemory.resolve_many(ids, decision, by)` resolves a list and returns the updated rules.
`resolve()` remains for one id.

Hub routes: `POST /style/inbox`; `POST /style/resolve` accepts `rule_ids` as well as `rule_id`.
Helper ops: `style_inbox`, and `resolve_style` passes the list through (still people-only).

### Panel

`read_suggestions` asks for the inbox and returns one style summary row in place of individual
style cards:

> 70 design rules are waiting · 34 decisions · 24 ready to use — **Review**

The row opens the dialog; closing it refetches the summary. Setting suggestions are unchanged.
The current "N more in Tools" line goes away for style.

### Dialog

`Tools > Learned design style...` becomes the inbox:

- Top: counts and two bulk actions — **Make the 24 ready rules** and **Dismiss the 6 weak ones**.
  Each expands to the exact statements (all selected, any can be unticked) before applying.
- Sections: Shared sizes, Corrections, Preferred parts, Habits, Review problems, Helper proposals.
  Each has "Use all" / "Dismiss all" and one card per decision with a Ready/Look/Weak badge, merged
  evidence, and Confirm/Dismiss.
- "In use now" (confirmed and active), grouped, with Remove.
- "Expired or dismissed" collapsed, with Make it a rule.
- Teach a rule and Learn again now are unchanged.

Every action is reversible from the two lists, so a bulk action is one visible decision rather
than dozens of blind ones. No extra confirmation dialog.

## Testing

- Unit: subject merging, recommendation per kind, expiry and revival, `resolve_many` resolving
  siblings, inbox counts/sections, taught and proposed rules in the right section.
- GUI: update `panel_gui_check.py` to expect the summary row; add an offscreen `StyleDialog` pass
  with a fake inbox checking section counts and that a bulk apply posts `rule_ids`.
- Full pytest, host tests unchanged.

## Docs

README "Learned design style" and the website docs chapter 14.
