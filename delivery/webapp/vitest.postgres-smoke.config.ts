import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/test/integration/*.smoke.test.ts"],
    pool: "forks",
    fileParallelism: false,
  },
  resolve: {
    alias: [
      // server-only's throwing entry must be stubbed in Vitest; Next keeps the
      // real guard because this alias is test-only.
      {
        find: /^server-only$/,
        replacement: path.resolve(__dirname, "./src/test/server-only-stub.ts"),
      },
      // Regex alias: matches "@/db" exactly, NOT "@/db/schema".
      // This lets the test client intercept `import { db } from "@/db"`
      // while `import { songs } from "@/db/schema"` still resolves normally.
      {
        find: /^@\/db$/,
        replacement: path.resolve(__dirname, "./src/test/db/postgres-client.ts"),
      },
      {
        find: "@",
        replacement: path.resolve(__dirname, "./src"),
      },
    ],
  },
});
