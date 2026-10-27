# Lighten without losing stiffness — agent guide

For AI helpers (Claude Code, Codex, OpenCode) working through CADSwarm's MCP tools.

## When to suggest it

Offer `lighten_part` when:

- your person wants a part **lighter, cheaper, faster to print or with less material**: "make it lighter",
  "save weight", "it's too heavy", "add infill", "lattice", "hollow it out";
- an **FEA report shows strength to spare** (safety factor above about 3): the report itself says so and names
  `lighten_part`;
- the part is for something where mass matters: drones, robots, arms, brackets, mounts, prosthetics, anything
  printed in metal.

Do not run it unasked on someone else's part, and do not offer it for parts that must be solid (seals,
threads, thin sheet, transparent parts).

## Ask first, in one message

1. **How is it held, and where does the load go?** Face selectors as for `run_fea` ("xmin", "Face6",
   "cyl r=2.5"); `list_faces` helps you describe them in plain words. If a static FEA has already been run on
   this part, offer to reuse it (`fea_job_id`).
2. **The material** (preset like `pla`, `aluminium_6061_t6`, or a dict).
3. **How it will be made.** Powder (SLS, MJF, metal LPBF) or resin printing traps material inside a closed
   skin, so add two opposite `open_faces`. The thinnest printable wall defaults to the team's `min_wall`.

Offer clearly marked placeholders if they don't know; the job records them like FEA does.

## Tools

| Tool | What it does |
|---|---|
| `lighten_part(target, stiffness_target_pct=90, cell="gyroid", fea_job_id \| fixed+loads, material, ...)` | Starts a job on a copy of the model. Returns its status; waits up to 45 s. |
| `lattice_status(job_id, wait=45)` | Progress, then the result: weight saving, verified stiffness, walls, warnings, files, next step. |
| `lattice_apply(job_id, name=None, force=False)` | Adds the finished lattice to the model as a mesh next to the original (unchanged). Ask first. |
| `lattice_cancel(job_id)` | Stops a job (yours; people can stop anyone's). |
| `lattice_cells(target="stiffness"\|"density", value, cell_size_mm, min_wall_mm)` | Ranks the eight cells for a target, lightest first. |
| `lattice_estimate(target, ...)` | Checks a request without running it: recipe, warnings, rough time. |

Useful parameters: `cell_size_mm` (default 8; about a third of the part's thinnest side is a good start),
`min_thickness_mm` / `max_thickness_mm` (wall range), `skin_mm` (default 1.5; thickened automatically when the
core alone can't reach the target), `open_faces` (`["+z", "-z"]`), `voxel_mm` (build detail; smaller is finer
and slower), `grading="uniform"` with `thickness_mm` for an unverified even lattice when there is no load case.

## Reading the result

- `verdict: "passed"`: the **verified** stiffness (`verify.stiffness_retained`, real walls) meets the target.
  Quote that number, not the search's.
- `verdict: "no_passing_design"`: even the densest allowed lattice (and a thicker skin) missed the target.
  Read the notes out and offer the suggestions: lower the target, stiffer cell (diamond, iwp, octet), thicker
  walls, smaller cells.
- `verdict: "not_verified"`: no load case was given; the stiffness was not checked.
- `warnings`: always tell your person about trapped powder (`closed_skin`), walls too thin to resolve, Schwarz P
  channels closing, or a coarse verify.
- A thickened skin is reported in the notes ("The skin was thickened from 1.5 to 5 mm"). In bending the skin
  carries most of the stiffness, so a thin skin rarely keeps 90 %.

## Example

```text
person: can you make the motor bracket lighter? it's printed in PA12 on an MJF.
you:    I can fill it with a lattice that's thicker where it carries load and check it keeps its
        stiffness. How is it mounted and loaded? I can reuse the stress analysis from this morning
        (fea_3f2a). MJF needs drainage, so I'll leave the two short ends open. OK?
person: yes, reuse it, keep at least 85 %.
you:    lighten_part("MotorBracket", stiffness_target_pct=85, fea_job_id="fea_3f2a",
                     material="nylon_pa12", open_faces=["+x", "-x"])
        ... lattice_status("lj_91c0", wait=45) ...
you:    Done: 34 % lighter (41 g -> 27 g), verified to keep 87 % of its stiffness; walls 0.8-2.1 mm,
        gyroid 6 mm cells. Want me to add it to the model next to the original?
```

## Rules

- The model only changes on `lattice_apply`, and only with your person's OK.
- Never call a lattice "certified", "safe" or "tested". Say "verified in simulation".
- Don't lighten parts you don't hold or weren't asked about; ask the owner.
- One job at a time per part; cancel jobs you no longer need.
