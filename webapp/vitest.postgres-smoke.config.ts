import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/test/integration/postgres-hot-pages.smoke.test.ts"],
    pool: "forks",
    fileParallelism: false,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Regex alias: matches "@/db" exactly, NOT "@/db/schema".
      // This lets the test client intercept `import { db } from "@/db"`
      // while `import { songs } from "@/db/schema"` still resolves normally.
      "/^@\\/db$/": path.resolve(__dirname, "./src/test/db/postgres-client.ts"),
    },
  },
});
