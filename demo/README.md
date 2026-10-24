# Demo: three agents, one enclosure

**Goal:** a small electronics enclosure: base plate, standoff posts, and a lid. It's built by a
lead agent and two teammates' agents at the same time while a human watches and steers in FreeCAD.

## Setup (about 2 minutes)

```powershell
# 1. Host: publish the module, then start the team (or let FreeCAD's guide do all of this).
spacetime publish --server maincloud --yes=all --delete-data=never --bin-path module\target\wasm32-unknown-unknown\release\cadswarm_module.wasm cadswarm-demo-1a2b
host\target\release\cadswarm-host --data .\demo-model init --mode maincloud --db cadswarm-demo-1a2b --python "<FreeCAD>\bin\python.exe"
host\target\release\cadswarm-host --data .\demo-model run

# 2. Host: one single-use invite per agent (and one for the human watching in FreeCAD).
host\target\release\cadswarm-host --data .\demo-model invite --kind agent --role lead     # lead-claude
host\target\release\cadswarm-host --data .\demo-model invite --kind agent --role member   # posts-codex
host\target\release\cadswarm-host --data .\demo-model invite --kind agent --role member   # lid-claude
host\target\release\cadswarm-host --data .\demo-model invite --kind human --role lead     # you-human

# 3. Each agent gets its own folder, wired to the team through its ticket.
cadswarm setup --client claude --invite "<lead ticket>"  --name lead-claude  --dir ..\lead
cadswarm setup --client codex  --invite "<posts ticket>" --name posts-codex  --dir ..\posts
cadswarm setup --client claude --invite "<lid ticket>"   --name lid-claude   --dir ..\lid

# 4. The human watches in FreeCAD: install the add-on, then CadSwarm > Get started and paste the human invite.
cadswarm install-workbench
```

Start `claude` or `codex` in each folder and paste the prompt for that agent.

## Prompts

**lead-claude**

> You lead this build. Lock Params and define: plate_len=120 mm, plate_w=80 mm, plate_t=6 mm,
> hole_pitch=100 mm, post_r=5 mm, post_h=30 mm, wall=2 mm. Create three tasks: "Base plate"
> (assign yourself), "Four standoff posts at the corners on hole_pitch" (assign posts-codex), and
> "Lid that sits on the posts, wall thickness Params.wall" (assign lid-claude). Build the base
> plate with every dimension bound to Params. Then wait_for_changes and review teammates' work
> with check_interference. Export STEP once all tasks are done.

**posts-codex**

> Check your inbox and claim your task. Build four cylinders bound to Params
> (Radius=Params.post_r, Height=Params.post_h, positions from Params.hole_pitch and plate_len /
> plate_w, sitting on top of the plate at z=Params.plate_t). Don't touch anything you didn't
> create. Run check_interference, then report_result with evidence.

**lid-claude**

> Check your inbox and claim your task. Use run_part_script to build a hollow lid: an outer box
> plate_len × plate_w × 15 mm minus an inner box inset by Params.wall, open at the bottom, placed at
> z = plate_t + post_h. Read the posts' positions with model_status and make sure the lid clears
> them. Coordinate with posts-codex by send_message if they conflict. Report with evidence.

## Moments to show

1. **Live building.** Parts appear in the FreeCAD mirror in each owner's color as the agents work.
2. **Protection.** Ask posts-codex to "make the base plate thicker". It's refused with "locked by
   lead-claude", and the agent messages the lead instead.
3. **Shared interface.** In the panel, use *Set param…* `hole_pitch = 90 mm`. The posts move, and
   posts-codex is notified and re-checks interference.
4. **Steering.** Send lid-claude "make the lid 20 mm tall" from the panel. It arrives as a `steer`
   message, ahead of peer messages.
5. **Crash and takeover.** Kill the Codex window mid-task. About 30s later the panel shows it
   *stale* with its locks still held. The lead calls `take_over('posts-codex')` (or a human uses
   *Take over member*) and finishes the job.
6. **Saving.** Ask lead-claude to "write a parts list". It saves `files/docs/bom.csv`. Ask
   posts-codex to save the same file: it's refused ("already exists... nothing was overwritten")
   until it reads the file and replaces that exact version. Open *the CadSwarm panel's Tools tab > Project files* to see both.
7. **Output.** *Export STEP*, then open it in FreeCAD or any CAD tool.
