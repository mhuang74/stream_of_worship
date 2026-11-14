/**
 * Which Vercel environment this bundle was built for.
 *
 * `NEXT_PUBLIC_VERCEL_ENV` is a Vercel system variable, inlined into the client
 * bundle at build time. It is absent on local `pnpm dev` and on non-Vercel
 * hosts; every value other than the exact strings below is treated as
 * "development" so an unrecognised or missing value shows the non-production
 * banner instead of silently passing as production.
 *
 * The full literal `process.env.NEXT_PUBLIC_VERCEL_ENV` must stay in this form
 * (no destructuring, no computed key) — Next only statically inlines
 * `NEXT_PUBLIC_*` references it can see verbatim.
 */
export type DeploymentEnv = "production" | "preview" | "development";

export function getDeploymentEnv(): DeploymentEnv {
  const raw = process.env.NEXT_PUBLIC_VERCEL_ENV;
  if (raw === "production") return "production";
  if (raw === "preview") return "preview";
  return "development";
}
