# CadSwarm website

The public page (waitlist + gated demo) and the waitlist API. Vercel serves this folder.

## The demo button

`index.html` has an **Open the live demo** button. It asks for the demo code (`swarm-13`,
printed on the Devpost page) and then opens `app/?mock`, the CadSwarm panels running against a
sample build. The gate is client-side on purpose: it keeps the demo out of casual clicks and
search results, not out of view of anyone who reads the page source.

## `app/` is a build

`app/` is the built panels app from `web/` (Vite), copied here so the demo lives on the same
domain as the page with no extra deployment. Rebuild it when `web/` changes:

```powershell
npm --prefix ..\web ci
npm --prefix ..\web run build
Remove-Item -Recurse -Force .\app
Copy-Item -Recurse ..\web\dist .\app
```

`web/vite.config.ts` sets `base: './'`, so the assets keep working under `/app/`.
