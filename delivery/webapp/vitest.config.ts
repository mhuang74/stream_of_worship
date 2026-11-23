import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    exclude: [
      ...configDefaults.exclude,
      "src/test/integration/*.smoke.test.ts",
    ],
  },
  resolve: {
    alias: [
      // server-only's throwing entry must be stubbed in Vitest; Next keeps the
      // real guard because this alias is test-only.
      {
        find: /^server-only$/,
        replacement: path.resolve(__dirname, "./src/test/server-only-stub.ts"),
      },
      { find: "@", replacement: path.resolve(__dirname, "./src") },
    ],
  },
});
