# Web Live Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cadswarm.design/app` open a real hosted build: model in the three.js viewer, team chat with the panel's `@` rules, remote controls, and a clean QR/code front door.

**Architecture:** Keep the existing vanilla TypeScript + three.js app and SpacetimeDB bindings. Extract pure chat/mention/sheet logic into small tested modules, rebuild the shell as a full-screen viewer with a draggable chat sheet, and add a join screen with camera QR scanning. Delete the public mock route and demo gate.

**Tech Stack:** TypeScript 6, Vite 8, three.js 0.186, SpacetimeDB SDK 2.13, vitest 5, `jsqr` 1.4, Rust host (clap), static Vercel hosting.

## Global Constraints

- Phones are the primary target; desktop is the same UI with the sheet rendered as a 380 px right rail.
- No accounts. Entry is an invite ticket (`#t=`) or a remembered SpacetimeDB session.
- Chat routing must match `cadswarm/mentions.py` and `cadswarm/gui/panel.py` exactly:
  `@name` = task + command per helper, `@team` = brief, `@all/@helpers/@everyone` = command to all,
  no mention = `chat` to all. Unknown non-person `@name` is refused with the panel's wording.
- Receipt words: `sent` → `⏳ not read yet`, `seen` → `👀 read, working on it`, `done` → `✓ done`.
- Message kinds `notice`, `help`, `result` never appear in the chat thread.
- Camera QR scanning must fall back to a paste box on every failure.
- Public demo is removed: no `?mock` route, no gate, no "Open the live demo".
- Host default web URL is `https://cadswarm.design/app`.
- Existing Python/Rust/web tests stay green; `npm run build` must pass TypeScript.

---

### Task 1: Mention rules

**Files:**
- Create: `web/src/ui/mentions.ts`
- Test: `web/test/mentions.test.ts`

**Interfaces:**
- Produces: `parseMentions(text: string, helperNames: string[]): { helpers: string[]; special: Set<'team' | 'all'>; unknown: string[] }`
- Produces: `stripMentions(text: string): string`
- Produces: `mentionPrefix(beforeCursor: string): string | null`
- Produces: `completions(helperNames: string[]): string[]` → helper names then `team`, `all`
- Produces: `insertMention(text: string, cursor: number, name: string): { text: string; cursor: number }`

- [ ] **Step 1: Write failing tests** covering: two helpers in one line, case-insensitive names, `@team`, `@helpers` mapping to `all`, unknown names, an email-like `a@b` being ignored, stripping punctuation, prefix after whitespace only, and cursor-preserving insertion.
- [ ] **Step 2: Run `npm --prefix web test -- mentions` and confirm failure (module missing).**
- [ ] **Step 3: Implement** with the regex `/(?<![\w@])@([A-Za-z0-9_-]+)/g` and `mentionPrefix` regex `/(?:^|\s)@([A-Za-z0-9_-]*)$/`, mirroring `cadswarm/mentions.py`.
- [ ] **Step 4: Run the test file and confirm all pass.**
- [ ] **Step 5: Commit** `feat(web): mirror the panel's @mention rules`.

### Task 2: Message routing decision

**Files:**
- Create: `web/src/ui/chat.ts`
- Test: `web/test/chat.test.ts`

**Interfaces:**
- Produces:
```ts
export type ChatAction =
  | { type: 'brief'; text: string }
  | { type: 'task'; helpers: string[]; text: string; what: string }
  | { type: 'command'; to: 'all'; text: string }
  | { type: 'chat'; text: string }
  | { type: 'unknown'; name: string };
export function routeChat(text: string, helperNames: string[], personNames: string[]): ChatAction;
```
- Rule order: trim; parse mentions; unknown and no helper/special and name not a person → `unknown`;
  `team` → `brief`; helpers → `task`; `all` → `command`; else `chat`.
- Produces `chatThread(state)` items: `{ kind:'message', ... } | { kind:'build', ... }`, sorted by time, excluding `notice/help/result`. Message item carries `mine`, `helper`, `to`, `body`, `state: 'sent'|'seen'|'done'`.

- [ ] **Step 1: Write failing tests** for every action branch, unknown-person fallback, kind filtering, sort order, and receipt mapping (`delivered`→`seen`, `consumed`→`done`, missing→`sent`).
- [ ] **Step 2: Run test and confirm failure.**
- [ ] **Step 3: Implement** `routeChat` and `chatThread` as pure functions over the `State` shape.
- [ ] **Step 4: Run tests and confirm pass.**
- [ ] **Step 5: Commit** `feat(web): route chat like the FreeCAD panel`.

### Task 3: Sheet detents

**Files:**
- Create: `web/src/ui/sheet.ts`
- Test: `web/test/sheet.test.ts`

**Interfaces:**
- Produces: `detents(viewport: number): { peek: number; half: number; full: number }` with `peek=190`, `half=Math.round(viewport*0.54)`, `full=viewport-56`, clamped to `[peek, viewport]`.
- Produces: `nearestDetent(height: number, viewport: number): SheetStop` and `nextStop(stop: SheetStop): SheetStop`.

- [ ] **Step 1: Write failing tests** for short/tall viewports, monotonic clamping, and nearest selection ties.
- [ ] **Step 2: Run test, confirm failure.**
- [ ] **Step 3: Implement the pure math.**
- [ ] **Step 4: Run test, confirm pass.**
- [ ] **Step 5: Commit** `feat(web): sheet detent math`.

### Task 4: API additions

**Files:**
- Modify: `web/src/api.ts`
- Test: `web/test/api.test.ts` (extend split-error or pure-task-id tests only)

**Interfaces:**
- Add `createTask(input: { title: string; detail: string; objects: string[]; assignee: string }): Promise<string>` → generates `t_` id, calls `r.createTask({ fence: fence(), taskId, title, detail, objects, assignee, brief: '' })`, reports errors via `report`, returns the id.
- Add `sendMessage(to: string, body: string, kind: string, taskId?: string): string` → one `r.sendMessage` call, new message id.
- Add `sendChat(body: string): string` → `sendMessage('all', body, 'chat')`.
- Change `submitBrief(text, board)` to send `stripMentions(text)`.
- Keep `sendCommand` as `sendMessage(to, body, 'command', taskId)`.

- [ ] **Step 1: Add a test that `stripMentions('@team make a lid') === 'make a lid'` (already covered in Task 1) and that the Api interface exposes the new names (type-level check in `api.test.ts`).**
- [ ] **Step 2: Implement the three methods and update `submitBrief`.**
- [ ] **Step 3: Run `npm --prefix web test` and `npm --prefix web run build` to check bindings compile.**
- [ ] **Step 4: Commit** `feat(web): create tasks and send chat from the browser`.

### Task 5: Chat pane and composer

**Files:**
- Create: `web/src/ui/chat-pane.ts` (DOM; replaces `web/src/ui/ask.ts`)
- Delete: `web/src/ui/ask.ts`
- Modify: `web/src/ui/helpers.ts` (keep quick-instruction text helpers that are still used; remove `conversation`, `destinations`, `TEAM` if unused)
- Test: covered by Task 2 plus existing `helpers.test.ts` updates.

**Interfaces:**
- `mountChat(el: HTMLElement, store: Store, api: Api | null, picked: () => string[]): void`
- Composer: textarea, send, no To dropdown, no chips.
- On send: `routeChat(text, helperNames, personNames)`.
  - `task`: for each helper `await api.createTask({ title: firstLine(what).slice(0,90), detail: what, objects: picked(), assignee: helper })` then `api.sendCommand(helper, text, taskId)`; show the first modal error if any create fails; keep the text.
  - `brief`: `api.submitBrief(text, board)`; board select visible only for `@team`.
  - `command`: `api.sendCommand('all', text)`.
  - `chat`: `api.sendChat(text)`.
  - `unknown`: toast/notice with the panel wording and keep the text.
- Mention completion popup above the composer: filtered by `mentionPrefix`; rows for helpers plus `@team`, `@all`; ArrowUp/Down, Enter/Tab, Escape, click; Enter that selects never sends.
- Hint line under the field.
- Thread renders `chatThread`: message bubbles with sender, `(AI helper)`, `to X`, receipt words, and build bubbles with steps/findings/STL.
- Stick to newest unless scrolled up.
- Empty state: "No messages yet. Type @ and pick a helper to give it something to build. It answers here as it works."

- [ ] **Step 1: Port the existing thread rendering into `chat-pane.ts` and wire the new routing.**
- [ ] **Step 2: Add the completion popup and keyboard/tap handling.**
- [ ] **Step 3: Remove `ask.ts` and its imports; update tests that imported `conversation`/`destinations` to the new `chat.ts` functions.**
- [ ] **Step 4: Run `npm --prefix web test`; fix expectations.**
- [ ] **Step 5: Commit** `feat(web): chat pane with @ completion and receipts`.

### Task 6: Shell, tabs and Team activity

**Files:**
- Modify: `web/src/ui/panels.ts`
- Modify: `web/src/style.css`

**Interfaces:**
- Tabs: `['chat','Chat'], ['model','Model'], ['library','Library'], ['tasks','Tasks'], ['team','Team']`; migrate stored `ask` to `chat`.
- Team tab's "Team chat" section becomes "Activity": render `s.activity` only, newest last, with the existing tone rules.
- Add the mobile sheet structure: grabber, header, tabs, panels, and a `.sheet` element that Task 7 controls.
- Desktop: `.panels` stays the 380 px rail; hide grabber.

- [ ] **Step 1: Rename the Ask tab/host element to Chat and import `mountChat`.**
- [ ] **Step 2: Replace the Team log with the activity feed.**
- [ ] **Step 3: Add the sheet markup and CSS tokens for `--sheet-h`, safe-area padding, top bar and part tag.**
- [ ] **Step 4: Run `npm --prefix web run build` and the test suite.**
- [ ] **Step 5: Commit** `feat(web): chat-first shell with mobile sheet`.

### Task 7: Sheet controller and top bar

**Files:**
- Create: `web/src/ui/sheet-controller.ts`
- Modify: `web/src/main.ts`
- Modify: `web/src/ui/panels.ts`

**Interfaces:**
- `mountSheet(sheet: HTMLElement, viewer: HTMLElement, onChange: (coveredPx: number) => void): { setStop(stop: SheetStop): void }`
- Pointer drag on the grabber/header; snap with `nearestDetent`; transition unless reduced motion.
- Header button toggles half/full; `aria-expanded`.
- Composer focus calls `setStop('half')` if peek.
- Dispatch `cadswarm:inset` with the covered pixel height.
- Top bar shows project name, live dot, active helper avatars; hidden when the join screen is shown.

- [ ] **Step 1: Implement pointer + keyboard control with no dependencies.**
- [ ] **Step 2: Wire main.ts to mount the sheet and pass the inset to the viewer.**
- [ ] **Step 3: Add a test for `nearestDetent` transitions (Task 3) and verify no layout regression at 390×844 and 1280×800 in the browser preview.**
- [ ] **Step 4: Commit** `feat(web): draggable chat sheet`.

### Task 8: Viewer inset, Fit and WebGL guard

**Files:**
- Modify: `web/src/viewer/scene.ts`
- Modify: `web/src/style.css`

**Interfaces:**
- `mountViewer` listens for `cadswarm:inset` and calls `camera.setViewOffset(w, h, 0, Math.round(px * 0.45), w, h)`; zero clears the offset.
- `frame()` and `aim()` keep using the visible centre.
- On WebGL constructor failure, render a short "This browser can't show 3D. Install or update it." message and return without throwing.
- Mobile Fit sits below the top bar; desktop keeps it bottom-left.

- [ ] **Step 1: Add the inset listener and offset math.**
- [ ] **Step 2: Move the Fit placement in CSS and wrap the renderer creation in try/catch.**
- [ ] **Step 3: Build and visually check that framing changes when the sheet moves.**
- [ ] **Step 4: Commit** `feat(web): frame the model above the chat sheet`.

### Task 9: Join screen and QR scanner

**Files:**
- Create: `web/src/ui/join.ts`
- Create: `web/src/ui/scan.ts`
- Modify: `web/src/main.ts`
- Modify: `web/package.json` (`jsqr` dependency)

**Interfaces:**
- `mountJoin(app: HTMLElement, opts: { last: { db: string } | null; onCode(text: string): Promise<void>; onSwitch(): void }): { setBusy(text?: string): void; setError(text: string): void }`
- `scanQR(video: HTMLVideoElement): Promise<string>` using `BarcodeDetector` or `jsqr`.
- `openScanner(onText: (text: string) => void, onError: (text: string) => void, onClose: () => void): void` renders the camera overlay, owns the stream, and stops it on every exit.

Join markup follows the approved mockup: brand, headline, scan button, divider, paste textarea, Continue, fine print, optional last-build row.

- [ ] **Step 1: Add `jsqr` with `npm --prefix web install jsqr@1.4.0`.**
- [ ] **Step 2: Implement the scanner with `getUserMedia({ video: { facingMode: 'environment' } })`, throttled decode, `playinline`, and full cleanup.**
- [ ] **Step 3: Implement the join screen and wire `readInvite` → `start(t)`.**
- [ ] **Step 4: Restructure `main.ts`: join screen first, `mountShell()` only after connection; remove the `?mock` branch and `startMockSwarm` import.**
- [ ] **Step 5: Build; test the paste path and the no-camera fallback.**
- [ ] **Step 6: Commit** `feat(web): clean join screen with QR scanning`.

### Task 10: Remove the public demo

**Files:**
- Modify: `website/index.html`
- Modify: `website/README.md`
- Modify: `web/src/main.ts` (already in Task 9)

- [ ] **Step 1: Delete the gate CSS, gate markup, gate script and `DEMO` constant from `website/index.html`.**
- [ ] **Step 2: Replace the hero secondary button with `<a class="btn btn-lg btn-ghost" href="./app/">Open CadSwarm on the web <span aria-hidden="true">→</span></a>`.**
- [ ] **Step 3: Update `website/README.md`: no demo code, app opens on the join screen, rebuild instructions unchanged.**
- [ ] **Step 4: `Select-String` for `mock`, `demo code`, `data-demo-open`, `gate` in `website/` and confirm only legitimate uses remain.**
- [ ] **Step 5: Commit** `chore(website): remove the gated mock demo`.

### Task 11: Host invite URL default

**Files:**
- Modify: `host/src/main.rs`
- Modify: `host/src/ticket.rs` (test)
- Modify: `README.md`
- Modify: `cadswarm/hosting.py` if a default is duplicated there

- [ ] **Step 1: Change the `web_url` default to `https://cadswarm.design/app`.**
- [ ] **Step 2: Add a `phone_url("https://cadswarm.design/app", "ABC")` test expecting `.../app/#t=ABC`.**
- [ ] **Step 3: Update README's phone-link description and the demo note that a fixed `-WebUrl` still works.**
- [ ] **Step 4: Run `cargo test --manifest-path host/Cargo.toml` (at least the ticket tests).**
- [ ] **Step 5: Commit** `feat(host): phone links point at cadswarm.design/app`.

### Task 12: Rebuild, verify, publish assets

**Files:**
- Replace: `website/app/` from `web/dist/`

- [ ] **Step 1: Run `npm --prefix web test`.**
- [ ] **Step 2: Run `npm --prefix web run build`.**
- [ ] **Step 3: Remove `website/app` and copy `web/dist` to it (PowerShell `Remove-Item -Recurse -Force` + `Copy-Item -Recurse`).**
- [ ] **Step 4: Serve `website/` with `npx serve` or `npm --prefix website`? Use `npx --yes serve website -l 4174` and open `/app/` at phone width; verify join screen, sheet, @ menu and Fit.**
- [ ] **Step 5: Run the full test suites listed in README (`pytest`, `cargo test`, `npm test`) or the relevant subset.**
- [ ] **Step 6: Commit** `chore(website): rebuild the live build app`.

## Decisions made while executing

- **The QR is shown by the host, not by the web app.** The phone is what scans, so a scannable code
  only ever belongs next to the computer hosting the team: the FreeCAD panel's **Remote** tab mints
  one single-use invite, draws it (via `cadswarm-host qr --text`, painted in Qt so FreeCAD's Python
  needs no QR or image package), and shows the same link as text with **Copy link** / **New code**.
  "Invite a teammate" opens that tab.
- **The web app has no Remote surface of its own, on phone or desktop.** A desktop browser showing a
  code to be scanned by the same phone that would rather just tap the link adds nothing; the phone
  joins from the panel's code or the pasted invite. This is deliberate, not a gap.

## Self-review

- Spec coverage: entry (Tasks 9, 10, 11), mobile shell (6, 7, 8), chat parity and @ (1, 2, 4, 5), viewer (8), QR (9), errors (5, 8, 9), deployment (10, 11, 12), tests (1, 2, 3, 4, 12).
- No placeholders: all interfaces and decisions are concrete.
- Naming: `mountChat`, `routeChat`, `chatThread`, `mountSheet`, `mountJoin`, `scanQR`, `openScanner` are used consistently.
