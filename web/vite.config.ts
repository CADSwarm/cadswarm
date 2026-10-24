import { defineConfig } from 'vite';
// Phones reach a local preview through a Cloudflare quick tunnel, whose hostname changes each run.
export default defineConfig({ base: './', preview: { allowedHosts: ['.trycloudflare.com'] } });
