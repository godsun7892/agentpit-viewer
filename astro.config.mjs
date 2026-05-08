import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

// https://astro.build/config
export default defineConfig({
  site: "https://agentpit.com",
  output: "static",
  vite: {
    // @ts-expect-error — Tailwind v4 vite plugin typing mismatch with Astro's pinned Vite
    plugins: [tailwindcss()],
  },
  build: {
    inlineStylesheets: "auto",
  },
});
