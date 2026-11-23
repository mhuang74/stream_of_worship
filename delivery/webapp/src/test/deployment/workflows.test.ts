import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";

const WEBAPP_ROOT = path.resolve(__dirname, "../../../");
const REPO_ROOT = path.resolve(WEBAPP_ROOT, "../..");
const CI_WORKFLOW_PATH = path.join(REPO_ROOT, ".github", "workflows", "ci.yml");
const DEPLOY_WORKFLOW_PATH = path.join(REPO_ROOT, ".github", "workflows", "deploy.yml");

function loadWorkflow(filePath: string): Record<string, unknown> {
  const raw = fs.readFileSync(filePath, "utf-8");
  return yaml.load(raw) as Record<string, unknown>;
}

describe("CI workflow", () => {
  it("exists at .github/workflows/ci.yml", () => {
    expect(fs.existsSync(CI_WORKFLOW_PATH)).toBe(true);
  });

  it("is valid YAML", () => {
    expect(() => loadWorkflow(CI_WORKFLOW_PATH)).not.toThrow();
  });

  it("triggers on pull_request to main", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    expect(on.pull_request).toBeDefined();
    const pr = on.pull_request as { branches: string[] };
    expect(pr.branches).toContain("main");
  });

  it("has webapp-lint-and-test job", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["webapp-lint-and-test"]).toBeDefined();
  });

  it("has render-worker-test job", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["render-worker-test"]).toBeDefined();
  });

  it("webapp job runs pnpm lint", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["webapp-lint-and-test"].steps as Array<{ run?: string }>;
    const lintStep = steps.find((s) => s.run?.includes("pnpm lint"));
    expect(lintStep).toBeDefined();
  });

  it("webapp job runs pnpm test", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["webapp-lint-and-test"].steps as Array<{ run?: string }>;
    const testStep = steps.find((s) => s.run?.includes("pnpm test"));
    expect(testStep).toBeDefined();
  });

  it("render-worker job runs pytest", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["render-worker-test"].steps as Array<{ run?: string }>;
    const testStep = steps.find((s) => s.run?.includes("pytest"));
    expect(testStep).toBeDefined();
  });

  it("uses paths filter for delivery/webapp and render-worker", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    const pr = on.pull_request as { paths: string[] };
    expect(pr.paths).toEqual(
      expect.arrayContaining(["delivery/webapp/**", "delivery/render-worker/**"]),
    );
  });

  it("webapp job defines a Postgres service", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const services = jobs["webapp-lint-and-test"].services as Record<string, unknown>;
    expect(services["postgres"]).toBeDefined();
  });

  it("webapp job uses a pgvector-capable image", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const services = jobs["webapp-lint-and-test"].services as Record<string, Record<string, unknown>>;
    const postgres = services["postgres"] as Record<string, unknown>;
    expect(postgres.image).toContain("pgvector");
  });

  it("webapp job Postgres service has expected environment variables", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const services = jobs["webapp-lint-and-test"].services as Record<string, Record<string, unknown>>;
    const postgres = services["postgres"] as Record<string, unknown>;
    const env = postgres.env as Record<string, string>;
    expect(env.POSTGRES_USER).toBe("sow");
    expect(env.POSTGRES_PASSWORD).toBe("sow");
    expect(env.POSTGRES_DB).toBe("sow_test");
  });

  it("webapp job runs drizzle-kit migrate with SOW_DATABASE_URL", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["webapp-lint-and-test"].steps as Array<{ run?: string }>;
    const migrateStep = steps.find((s) => s.run?.includes("drizzle-kit migrate"));
    expect(migrateStep).toBeDefined();
    expect(migrateStep!.run).toContain("SOW_DATABASE_URL");
  });

  it("webapp job runs pnpm test:postgres-smoke with SOW_DATABASE_URL", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["webapp-lint-and-test"].steps as Array<{ run?: string }>;
    const smokeStep = steps.find((s) => s.run?.includes("test:postgres-smoke"));
    expect(smokeStep).toBeDefined();
    expect(smokeStep!.run).toContain("SOW_DATABASE_URL");
  });
});

describe("Deploy workflow", () => {
  it("exists at .github/workflows/deploy.yml", () => {
    expect(fs.existsSync(DEPLOY_WORKFLOW_PATH)).toBe(true);
  });

  it("is valid YAML", () => {
    expect(() => loadWorkflow(DEPLOY_WORKFLOW_PATH)).not.toThrow();
  });

  it("triggers on push to main", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    expect(on.push).toBeDefined();
    const push = on.push as { branches: string[] };
    expect(push.branches).toContain("main");
  });

  it("has migrate-db job", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["migrate-db"]).toBeDefined();
  });

  it("has deploy-render-worker job", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["deploy-render-worker"]).toBeDefined();
  });

  it("has detect-changes job", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["detect-changes"]).toBeDefined();
  });

  it("migrates before either deployable and never skips worker-only pushes", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    expect(jobs["migrate-db"].needs).toBe("detect-changes");
    expect(jobs["migrate-db"].if).toBe(
      "needs.detect-changes.outputs.webapp == 'true' || needs.detect-changes.outputs.render-worker == 'true'",
    );
    expect(jobs["deploy-render-worker"].needs).toEqual(["detect-changes", "migrate-db"]);
    expect(jobs["deploy-render-worker"].if).toBe(
      "needs.detect-changes.outputs.render-worker == 'true' && needs.migrate-db.result == 'success'",
    );
  });

  it("uses paths filter for delivery/webapp and render-worker", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    const push = on.push as { paths: string[] };
    expect(push.paths).toEqual(
      expect.arrayContaining(["delivery/webapp/**", "delivery/render-worker/**"]),
    );
  });

  it("migrate-db triggers Vercel deploy via hook", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["migrate-db"].steps as Array<{ run?: string; name?: string }>;
    const deployStep = steps.find(
      (s) => s.run?.includes("curl") && s.run?.includes("VERCEL_DEPLOY_HOOK_URL"),
    );
    expect(deployStep).toBeDefined();
  });

  it("migrate-db references VERCEL_DEPLOY_HOOK_URL secret", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["migrate-db"].steps as Array<Record<string, unknown>>;
    const raw = JSON.stringify(steps);
    expect(raw).toContain("secrets.VERCEL_DEPLOY_HOOK_URL");
  });

  it("migrate-db runs DB migration step", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["migrate-db"].steps as Array<{ run?: string; name?: string }>;
    const migrateStep = steps.find(
      (s) => s.run?.includes("scripts/migrate.ts") || s.name?.toLowerCase().includes("migrate"),
    );
    expect(migrateStep).toBeDefined();
  });

  it("migrate-db references SOW_DATABASE_URL secret for schema push", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["migrate-db"].steps as Array<Record<string, unknown>>;
    const raw = JSON.stringify(steps);
    expect(raw).toContain("secrets.SOW_DATABASE_URL");
  });

  it("deploy-render-worker uses AWS ECR login", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["deploy-render-worker"].steps as Array<{ uses?: string }>;
    const ecrStep = steps.find((s) => s.uses?.includes("amazon-ecr-login"));
    expect(ecrStep).toBeDefined();
  });

  it("deploy-render-worker references SOW_AWS_ACCESS_KEY_ID secret", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["deploy-render-worker"].steps as Array<Record<string, unknown>>;
    const raw = JSON.stringify(steps);
    expect(raw).toContain("secrets.SOW_AWS_ACCESS_KEY_ID");
  });

  it("deploy-render-worker references SOW_AWS_SECRET_ACCESS_KEY secret", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["deploy-render-worker"].steps as Array<Record<string, unknown>>;
    const raw = JSON.stringify(steps);
    expect(raw).toContain("secrets.SOW_AWS_SECRET_ACCESS_KEY");
  });

  it("deploy-render-worker updates Lambda function", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const steps = jobs["deploy-render-worker"].steps as Array<{ run?: string }>;
    const lambdaStep = steps.find((s) => s.run?.includes("lambda update-function-code"));
    expect(lambdaStep).toBeDefined();
  });
});

describe("Deploy workflow — Glue worker", () => {
  function glueSteps(): Array<Record<string, unknown>> {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    return jobs["deploy-glue-worker"].steps as Array<Record<string, unknown>>;
  }

  it("has deploy-glue-worker job", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, unknown>;
    expect(jobs["deploy-glue-worker"]).toBeDefined();
  });

  it("waits for the render worker deploy and reuses its image URI", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const job = jobs["deploy-glue-worker"];
    expect(job.needs).toEqual(["detect-changes", "migrate-db", "deploy-render-worker"]);
    expect(job.if).toBe(
      "needs.detect-changes.outputs.render-worker == 'true' && needs.deploy-render-worker.result == 'success'",
    );
    expect(JSON.stringify(job.steps)).toContain("needs.deploy-render-worker.outputs.image");
  });

  it("updates sow-glue-worker with the shared render-worker image", () => {
    const raw = JSON.stringify(glueSteps());
    expect(raw).toContain("aws lambda update-function-code");
    expect(raw).toContain("--function-name sow-glue-worker");
    expect(raw).toContain("--image-uri \\\"$IMAGE_URI\\\"");
  });

  it("publishes a Glue Lambda version", () => {
    const publish = glueSteps().find((s) =>
      JSON.stringify(s).includes("aws lambda publish-version"),
    );
    expect(publish).toBeDefined();
    const raw = JSON.stringify(publish);
    expect(raw).toContain("--function-name sow-glue-worker");
    expect(raw).toContain("--publish-to LATEST_PUBLISHED");
  });

  it("verifies the published Glue version reached Active", () => {
    // Match on content, not the step name: renaming the step must not silently
    // disarm the guard.
    const verify = glueSteps().find((s) => {
      const raw = JSON.stringify(s);
      return (
        raw.includes("get-function-configuration") &&
        raw.includes("$LATEST.PUBLISHED") &&
        raw.includes("State") &&
        raw.includes("Failed")
      );
    });
    expect(verify).toBeDefined();
  });

  it("applies execution-environment scaling with the separate API", () => {
    const steps = glueSteps();
    const scaling = steps.filter((s) =>
      JSON.stringify(s).includes("--function-scaling-config"),
    );
    // FunctionScalingConfig is not a CreateFunction/UpdateFunctionConfiguration
    // request field, so exactly one step may carry it and it must be the
    // PutFunctionScalingConfig call.
    expect(scaling).toHaveLength(1);
    const script = JSON.stringify(scaling[0]);
    expect(script).toContain("aws lambda put-function-scaling-config");
    expect(script).toContain("--qualifier '$LATEST.PUBLISHED'");
    expect(script).toContain("MinExecutionEnvironments=0,MaxExecutionEnvironments=2");
  });

  it("sets SOW_ELEVENLABS_API_KEY only on the Glue function", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    const glueRaw = JSON.stringify(jobs["deploy-glue-worker"].steps);
    const renderRaw = JSON.stringify(jobs["deploy-render-worker"].steps);

    expect(glueRaw).toContain("SOW_ELEVENLABS_API_KEY");
    expect(renderRaw).not.toContain("SOW_ELEVENLABS_API_KEY");
  });

  it("fails fast unless the Glue function uses Managed Instances and Timeout 1900", () => {
    const steps = glueSteps();
    const check = steps.find((s) => {
      const run = typeof s.run === "string" ? s.run : "";
      return run.includes("get-function-configuration") && run.includes("sow-glue-worker");
    });
    expect(check).toBeDefined();
    const run = check!.run as string;
    expect(run).toContain(
      "CapacityProviderConfig.LambdaManagedInstancesCapacityProviderConfig.CapacityProviderArn",
    );
    expect(run).toContain("Timeout");
    expect(run).toContain("1900");
    expect(run).toContain("exit 1");
  });

  it("does not rebuild or push an image for the Glue function", () => {
    const raw = JSON.stringify(glueSteps());
    expect(raw).not.toContain("docker buildx");
    expect(raw).not.toContain("docker push");
  });
});
