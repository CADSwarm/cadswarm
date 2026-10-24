# CadSwarm website

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
