import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig(async () => {
  const migrations = await readD1Migrations("./migrations");
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: "./wrangler.jsonc" },
        miniflare: {
          // nodejs_compat only for the tests: jpeg-js's *encoder* (used to synthesise photos) needs Buffer
          compatibilityFlags: ["nodejs_compat"],
          bindings: { TEST_MIGRATIONS: migrations, LABRAT_TOKEN: "labrat", LABRAT_PHOTO_WAIT_S: "0.3", ANTHROPIC_API_KEY: "" },
        },
      }),
    ],
    test: { setupFiles: ["./test/setup.ts"], testTimeout: 60000 },
  };
});
