import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";

const WEBAPP_ROOT = path.resolve(__dirname, "../../../");
const REPO_ROOT = path.resolve(WEBAPP_ROOT, "..");
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

  it("uses paths filter for webapp and render-worker", () => {
    const workflow = loadWorkflow(CI_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    const pr = on.pull_request as { paths: string[] };
    expect(pr.paths).toEqual(
      expect.arrayContaining(["webapp/**", "services/render-worker/**"]),
    );
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

  it("deploy jobs depend on detect-changes", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const jobs = workflow.jobs as Record<string, Record<string, unknown>>;
    expect(jobs["migrate-db"].needs).toBe("detect-changes");
    expect(jobs["deploy-render-worker"].needs).toBe("detect-changes");
  });

  it("uses paths filter for webapp and render-worker", () => {
    const workflow = loadWorkflow(DEPLOY_WORKFLOW_PATH);
    const on = workflow.on as Record<string, unknown>;
    const push = on.push as { paths: string[] };
    expect(push.paths).toEqual(
      expect.arrayContaining(["webapp/**", "services/render-worker/**"]),
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
