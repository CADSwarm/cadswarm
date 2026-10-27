# CADSwarm website

The public page (waitlist + the web app) and the waitlist API. Vercel serves this folder.

## The app button

`index.html` has an **Open CadSwarm on the web** button that links to `app/`. The app is not a
demo any more: it opens on the join screen and asks for the host's invite code or QR, so nobody
lands in a sample build by accident.

## `app/` is a build

`app/` is the built app from `web/` (Vite), copied here so it lives on the same domain as the page
with no extra deployment. Rebuild it when `web/` changes:

```powershell
npm --prefix ..\web ci
npm --prefix ..\web run build
Remove-Item -Recurse -Force .\app
Copy-Item -Recurse ..\web\dist .\app
```

`web/vite.config.ts` sets `base: './'`, so the assets keep working under `/app/`.

## Docs pages

Three plain HTML pages share `docs.css` (the marketing tokens, plus a reading layout):

| File | URL | What it is |
|---|---|---|
| `quickstart.html` | `/quickstart` | The ten-minute path: install, start a team, connect a helper, first build. |
| `docs.html` | `/docs` | The full guide, 18 chapters with a sticky chapter list and deep links. |
| `developer.html` | `/developer` | Architecture, repository map, CLI, MCP tools, invariants, publishing, tests. |

`cleanUrls` in `vercel.json` serves them without the `.html`. Links between pages use the
`.html` names so the pages also work from a plain folder (`python -m http.server`).

Check them before committing — local links, anchors, tag balance and CSS classes:

```powershell
python ..\scripts\check_docs.py
```

Content rules: plain words, no invented settings, and every number (file limits, lock times,
helper run limits) copied from the code or the README. Update this guide in the same change as
the behaviour it describes.

