import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { screen, fireEvent, waitFor } from "@testing-library/react"
import { renderWithLocale as render } from "@/test/render"
import { RenderPageClient } from "@/app/songsets/[id]/render/RenderPageClient"

const mockPush = vi.fn()
const mockReplace = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
}))

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn().mockReturnValue("toast-id"),
  },
}))

import { toast } from "sonner"
import { t } from "@/lib/i18n/messages"

const SONGSET = {
  id: "test-songset",
  name: "Sunday Worship",
  description: "Easter service",
  markedLineCount: 0,
  renderState: "unrendered" as const,
  songTitles: [],
  lastCompletedRenderJobId: null,
  durationSeconds: null,
}

const INITIAL_RENDER_DATA = {
  audioEnabled: true,
  videoEnabled: true,
  template: "dark" as const,
  resolution: "720p" as const,
  fontSizePreset: "M" as const,
  fontFamily: "noto_serif_tc" as const,
  includeTitleCard: false,
  titleCardDurationSeconds: 10,
  titleCardLines: [],
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

/** Renders the page and waits for the dynamically imported form. */
async function renderForm(locale?: "en" | "zh-Hant") {
  const view = render(
    <RenderPageClient
      songsetId="test-songset"
      initialSongset={SONGSET}
      initialPreviousCompletedJob={null}
      initialRenderData={INITIAL_RENDER_DATA}
    />,
    locale
  )
  // The form is a `next/dynamic` import: the wait is a chunk load, so give it
  // more than the default second when the suite runs in parallel.
  await screen.findByRole(
    "button",
    { name: locale === "zh-Hant" ? /開始渲染/i : /start render/i },
    { timeout: 5000 }
  )
  return view
}

async function submitRender(locale?: "en" | "zh-Hant") {
  fireEvent.click(
    await screen.findByRole(
      "button",
      { name: locale === "zh-Hant" ? /開始渲染/i : /start render/i },
      { timeout: 5000 }
    )
  )
}

describe("RenderPageClient", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("unstubbed fetch")))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("renders server-loaded songset data", async () => {
    await renderForm()

    expect(screen.getByRole("heading", { name: /render/i })).toBeInTheDocument()
    expect(screen.getByText("Sunday Worship")).toBeInTheDocument()
    expect(screen.getByText("Output Options")).toBeInTheDocument()
  })

  it("renders zh-Hant heading and back label", async () => {
    await renderForm("zh-Hant")

    expect(screen.getByRole("heading", { name: "渲染" })).toBeInTheDocument()
    expect(screen.getByLabelText("返回")).toBeInTheDocument()
  })

  it("replaces to the songset editor after a successful submit", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ id: "job-1" })))
    vi.stubGlobal("fetch", fetchMock)

    await renderForm()
    await submitRender()

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/songsets/test-songset")
    })

    expect(toast.success).toHaveBeenCalledWith(t("en", "render.toast.started"))
    // The submitted screen is gone: no second screen to strand the user on.
    expect(screen.queryByText("Render Started")).not.toBeInTheDocument()
  })

  it("replaces to the songset editor when a job is already in progress", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse(
          {
            error: "A render job is already in progress",
            jobId: "job-1",
            config: { audioEnabled: true, videoEnabled: false },
          },
          409
        )
      )
    )
    vi.stubGlobal("fetch", fetchMock)

    await renderForm()
    await submitRender()

    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/songsets/test-songset")
    })

    expect(toast.info).toHaveBeenCalledWith(
      `${t("en", "render.toast.alreadyInProgress")} (${t("en", "render.toast.config.audio")})`
    )
    expect(toast.success).not.toHaveBeenCalled()
  })

  it("stays on the form when the 409 is a broken saved arrangement", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        jsonResponse({ error: "stale", code: "ARRANGEMENT_STALE" }, 409)
      )
    )
    vi.stubGlobal("fetch", fetchMock)

    await renderForm()
    await submitRender()

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(t("en", "render.error.arrangementRepairOrReset"))
    })

    expect(mockReplace).not.toHaveBeenCalled()
    expect(screen.getByText("Output Options")).toBeInTheDocument()
  })

  it("sends the user to the login page when the session is gone", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({}, 401)))
    vi.stubGlobal("fetch", fetchMock)

    await renderForm()
    await submitRender()

    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/login")
    })
  })
})
