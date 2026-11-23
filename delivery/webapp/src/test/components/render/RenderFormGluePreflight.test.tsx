import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest"
import { screen, fireEvent, waitFor } from "@testing-library/react"
import { renderWithLocale as render } from "@/test/render"
import { RenderForm } from "@/components/render/RenderForm"
import type { GlueRenderPreflight } from "@/lib/db/songsets"

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

const preflight: GlueRenderPreflight = {
  code: "GLUE_NOT_READY",
  glues: [
    {
      fromItemId: "item-a",
      toItemId: "item-b",
      fromTitle: "Amazing Grace",
      toTitle: "Holy Holy Holy",
      reasonCode: "GLUE_PROVIDER_UNKNOWN",
      jobId: "job-1",
      jobPhase: "failed",
      retryKind: "paid",
    },
    {
      fromItemId: "item-b",
      toItemId: "item-c",
      fromTitle: "Holy Holy Holy",
      toTitle: "Great Is Thy Faithfulness",
      reasonCode: "GLUE_RETRY_PROCESSING_FREE",
      jobId: "job-2",
      jobPhase: "staging",
      retryKind: "free",
    },
  ],
}

describe("RenderForm AI-transition preflight", () => {
  const onSubmit = vi.fn()
  const onCancel = vi.fn()
  const onGluePreflightChanged = vi.fn()
  let fetchMock: Mock

  beforeEach(() => {
    vi.clearAllMocks()
    fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ok: true }),
    })
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  function renderForm(locale?: "en" | "zh-Hant") {
    return render(
      <RenderForm
        songsetId="test-songset"
        onSubmit={onSubmit}
        onCancel={onCancel}
        isSubmitting={false}
        gluePreflight={preflight}
        onGluePreflightChanged={onGluePreflightChanged}
      />,
      locale
    )
  }

  it("renders a blocking panel per Junction with reason, phase, job, and actions", () => {
    renderForm()

    expect(screen.getByTestId("render-glue-preflight")).toBeInTheDocument()
    expect(screen.getByText("AI transitions need attention")).toBeInTheDocument()
    expect(screen.getByText("Amazing Grace")).toBeInTheDocument()
    expect(screen.getAllByText("Holy Holy Holy")).toHaveLength(2)
    expect(
      screen.getByText(
        "The AI transition service did not respond. Try another (paid) or open the editor."
      )
    ).toBeInTheDocument()
    expect(screen.getAllByTestId("render-glue-preflight-phase")[0]).toHaveTextContent("Status: The last attempt did not finish")
    expect(screen.getAllByTestId("render-glue-preflight-job")[0]).toHaveTextContent("Job: job-1")

    expect(screen.getByRole("button", { name: "Retry (free)" })).toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: "Try another (paid)" })).toHaveLength(1)
    expect(screen.getAllByRole("link", { name: "Open editor" })[0]).toHaveAttribute(
      "href",
      "/songsets/test-songset?glueItem=item-a"
    )
    expect(
      screen.getAllByRole("button", {
        name: "Disable AI transitions and use ordinary transitions",
      })
    ).toHaveLength(2)

    expect(screen.getByRole("button", { name: "Start Render" })).toBeDisabled()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("uses Traditional Chinese 轉場 copy without provider vocabulary", () => {
    renderForm("zh-Hant")

    expect(screen.getByText("AI 轉場需要處理")).toBeInTheDocument()
    expect(
      screen.getByText("AI 轉場服務沒有回應，請再試一次（付費）或開啟編輯器。")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "免費重試" })).toBeInTheDocument()
    expect(
      screen.getAllByRole("button", { name: "關閉 AI 轉場，改用一般轉場" })
    ).toHaveLength(2)
  })

  it("retries a free Junction through the resume endpoint, then re-resolves", async () => {
    renderForm()

    fireEvent.click(screen.getByRole("button", { name: "Retry (free)" }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/glue-jobs/job-2/resume",
        expect.objectContaining({ method: "POST" })
      )
    })
    await waitFor(() => expect(onGluePreflightChanged).toHaveBeenCalledTimes(1))
  })

  it("confirms disabling one Junction and patches only its item", async () => {
    renderForm()

    fireEvent.click(
      screen.getAllByRole("button", {
        name: "Disable AI transitions and use ordinary transitions",
      })[0]
    )

    const dialog = await screen.findByRole("alertdialog")
    expect(dialog).toHaveTextContent("Amazing Grace")
    expect(dialog).toHaveTextContent("Holy Holy Holy")

    fireEvent.click(
      screen.getByRole("button", { name: "Disable and use ordinary transitions" })
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/songsets/test-songset/items",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ itemId: "item-a", glueEnabled: false }),
        })
      )
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(onGluePreflightChanged).toHaveBeenCalledTimes(1))
  })
})
