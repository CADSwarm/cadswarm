# Web live build: model + chat at cadswarm.design

Date: 2026-10-24
Status: approved (layout and front door reviewed in the visual companion)

## Goal

cadswarm.design becomes a usable remote control for a real CadSwarm build, not a demo. A teammate
opens `https://cadswarm.design/app`, scans the host's invite QR or pastes the invite code, and lands
in the live build: the model in a web-rendered 3D viewer, the team conversation beside it, and the
same controls the FreeCAD panel offers. Phones are the primary design target; desktop is the same
app with a right-hand rail.

## Non-goals

- No accounts, team directory or server-side session store. The invite ticket and the browser's
  remembered SpacetimeDB token are the whole auth story.
- No new model execution path. The host still runs every op; the browser only reads state and
  submits reducers.
- No separate mobile app or native shell.
- No public/mock demo. The demo code gate and the `?mock` route are removed.

## Entry and routing

- `website/` keeps the marketing page. Its secondary hero action becomes **Open CadSwarm on the
  web** and points at `./app/`; the demo button, gate markup, gate CSS and gate script are deleted.
- `website/app/` is a build of `web/` (existing copy process). Vercel serves it statically.
- Host invites point at the app, not the marketing root: `cadswarm-host init` defaults
  `--web-url` to `https://cadswarm.design/app`, so `phone_url` produces
  `https://cadswarm.design/app/#t=<ticket>`. Existing host.json files keep their value.
- The app's first screen when there is no ticket and no remembered build is one quiet join screen:
  brand, "Open your team's build", **Scan invite QR**, or a paste box and **Continue**.
- A ticket in `#t=` is consumed, removed from the address bar, and joined immediately.
- A returning browser rejoins the remembered build. The join screen's "last build" row can clear it.
- After connecting, the app mounts the viewer and the chat sheet. If the token is valid but the
  person is not yet a member, a full-screen name form redeems the invite, then the app appears.
- `?mock` is no longer a route. `web/src/mock.ts` stays only as a test fixture; nothing in the
  production entry imports it.

## Mobile shell: model stage + chat sheet

- The 3D viewer fills the viewport edge to edge. A glass top bar overlays it with the project name,
  live/reconnecting state and active helper avatars.
- The chat lives in a draggable sheet over the viewer with three detents:
  - **peek** (~190 px): grabber, title, last message and the composer;
  - **half** (~54dvh): tabs, thread, quick state and composer;
  - **full** (~100dvh minus the top bar): the whole thread and every tab.
- Drag uses pointer events; release snaps to the nearest detent. The header is also a button that
  toggles half/full for keyboard and assistive technology. `prefers-reduced-motion` removes the
  transition.
- Focusing the composer raises the sheet to at least half. Safe-area insets pad the composer.
- Sheet tabs are **Chat, Model, Library, Tasks, Team**. Switching tabs from peek expands to half.
- The viewer is told the sheet's covered height through a `cadswarm:inset` event so framing keeps
  the model above the sheet instead of behind it.
- On desktop the sheet becomes the existing 380 px right rail: no grabber, no detents, tabs across
  the top. The viewer fills the rest.
- Tapping a part shows a small glass tag with its label and holder. The tag is informational; Hold
  and Release stay in Model, where the existing lock controls live.

## Chat parity with the FreeCAD panel

The Chat tab replaces Ask and reads the real team conversation, not a second inbox.

- The thread contains team messages and helper messages whose kind is not `notice`, `help` or
  `result`, plus build bubbles. People chat, helper replies, instructions and peer messages all
  appear in time order. Activity and who-holds-what remain in Team.
- Each message shows the sender, **You** for the current person, `(AI helper)` for non-humans, and
  `to <name>` when the message targets one other member and the body does not already mention them.
- Instructions you sent show their receipts: `⏳ not read yet`, `👀 read, working on it`, `✓ done`,
  matching `COMMAND_WORDS` in the panel.
- Build bubbles keep the current five steps (Sent, Plan, Build, Review, Ready), task progress,
  review findings and the STL download.

### @ mentions

The composer implements the same rules as `cadswarm/mentions.py`:

- `@name <what to do>`: for each named helper, create a task (stripped request as the title, the
  full text as detail, currently picked parts as objects, helper as assignee), then send the full
  text as a `command` with that task id. One task per named helper.
- `@team <request>`: submit a brief with the mentions stripped. The existing Board selector is
  shown only while the text contains `@team`.
- `@all`, `@helpers`, `@everyone <message>`: send a `command` to `all`.
- No mention: send a `chat` message to `all`. Helpers do not treat it as an order.
- An unknown `@name` that is not a person shows "There's no AI helper called @name. Type @ to pick
  one from the list." and leaves the text in the box.
- Typing `@` (or `@par`) shows a completion list of live helpers plus `@team` and `@all`, with each
  helper's current task/status. Filtering is case-insensitive. Enter/Tab picks the highlighted
  row, arrow keys move, Escape closes, tap picks. The inserted text is `@name ` and Enter that
  closed the list must not also send.
- The composer hint matches the panel: `@name` gives a helper a task · `@team` plans and builds a
  request · `@all` tells every helper · no @ is chat between people.
- The old "To" dropdown and message chips are removed; they duplicated the mention menu and sent
  everything as commands.

## API additions

`web/src/api.ts` gains the reducer calls needed by the mention rules:

- `createTask({ title, detail, objects, assignee }): Promise<string>` calling `create_task`, with a
  locally generated task id.
- `sendChat(body): void` and a shared `sendMessage(to, body, kind, taskId)`.
- `submitBrief` strips mentions before submitting.

Rows and live subscriptions do not change. Task creation and command sending are sequential; a
failed task creation leaves the text in the composer and toasts the error.

## QR scanning

- The host shows the code, not a terminal: the FreeCAD panel's **Remote** tab mints one single-use
  invite and draws it as a QR (`cadswarm-host qr` prints the modules, the panel paints them) beside
  the same link as text and **Copy link**. "Invite a teammate" in the panel header opens that tab.
  A host whose `web_url` only reaches this computer (`localhost`) shows the public site instead.
- `Scan invite QR` opens a full-screen camera view with a framed target and a manual-entry link.
- The scanner uses `BarcodeDetector` when the browser provides it, otherwise `jsqr` against a
  downscaled canvas. Both run in a throttled `requestAnimationFrame` loop.
- The result is passed through `readInvite`, so a QR containing a full phone link or a bare ticket
  both work.
- Camera refusal, no camera and an unreadable code are explained in plain language and leave the
  paste box available. The stream is stopped on success, cancel, navigation and unmount.

## Viewer changes

- Keep the three.js scene, STL previews, pointers, owner tinting and reduced-motion behaviour.
- Frame with a bottom inset matching the sheet so the model is centred in the uncovered area.
- Move Fit clear of the glass top bar on phones; keep it bottom-left on desktop.
- Guard WebGL failure with a short message in the viewer instead of a blank screen.

## Error handling

- Unreachable host/database: explain that the hosting computer must be on and explain local-mode
  loopback limits; offer the join screen again.
- Expired/used invite: keep the pasted text and say a new invite is needed.
- Stored token refused (database reset): retry fresh with the ticket if one exists, otherwise clear
  the session and show the join screen.
- Reducer errors keep the existing toast behaviour.
- Camera errors never block manual entry.

## Deployment

- `web/` builds with `npm run build`; `website/app/` is replaced from `web/dist`.
- Host config default changes to the public app URL; README and website README are updated.
- `demo-up.ps1` continues to pass a tunnel URL, which still works because the phone link appends
  `/#t=` to whatever `--web-url` holds.

## Testing

- Vitest for the new pure modules: mention parsing/stripping/prefix/completion, message routing
  decisions, conversation assembly (including receipts and excluded kinds), and sheet detent math.
- Existing mock-driven panel tests stay, since `mock.ts` remains a test fixture.
- `npm run build` must pass TypeScript, and the existing web suite must stay green.
- `cargo test` covers `phone_url` with a path-bearing web URL.
- Manual check at phone width: join screen, QR fallback copy, sheet detents, @ completion, a
  `@helper` task, `@team` brief, plain chat, part tag, Fit.
