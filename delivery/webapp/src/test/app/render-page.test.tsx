import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { screen, act, fireEvent } from "@testing-library/react"
import { renderWithLocale as render } from "@/test/render"
import { RenderPageClient } from "@/app/songsets/[id]/render/RenderPageClient"

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
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

// The download helper runs for real (see the assertions on cacheArtifacts);
// only its Cache Storage / IndexedDB / device boundary is mocked.
vi.mock("@/lib/offline/artifact-cache", () => ({
  isOfflineSupportedOnCurrentDevice: vi.fn().mockReturnValue(true),
  requestPersistentStorage: vi.fn().mockResolvedValue(true),
  cacheArtifacts: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@/lib/offline/offline-index", () => ({
  putOfflineRecord: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@/lib/offline/document-cache", () => ({
  cacheControllerDocument: vi.fn().mockResolvedValue(true),
}))

import { toast } from "sonner"
import {
  cacheArtifacts,
  isOfflineSupportedOnCurrentDevice,
} from "@/lib/offline/artifact-cache"
import { putOfflineRecord } from "@/lib/offline/offline-index"
import { t } from "@/lib/i18n/messages"
import { RENDER_JOB_POLL_INTERVAL_MS } from "@/components/render/RenderSubmitted"

const POLL_INTERVAL_MS = RENDER_JOB_POLL_INTERVAL_MS

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

const RUNNING_JOB = {
  id: "job-1",
  status: "running",
  createdAt: new Date(0).toISOString(),
  template: "dark",
  fontFamily: "noto_serif_tc",
  fontSizePreset: "M",
  includeTitleCard: false,
  mp3R2Key: null,
  mp4R2Key: null,
  chaptersR2Key: null,
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

interface FetchHandlers {
  offlineAutoCache?: boolean
  settingsOk?: boolean
  /** The finished job's artifact keys; null keys model an artifact-less render. */
  job?: Record<string, unknown>
  /** What /api/offline/cache hands back; false models a render with no playable artifact. */
  artifactUrls?: boolean
}

/** Serves the settings read, the render-job poll, and the offline-cache URLs. */
function stubRenderFetch({
  offlineAutoCache = true,
  settingsOk = true,
  job,
  artifactUrls = true,
}: FetchHandlers = {}) {
  const mock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.startsWith("/api/settings")) {
      return Promise.resolve({
        ok: settingsOk,
        json: () => Promise.resolve({ settings: { offlineAutoCache } }),
      })
    }
    if (url.startsWith("/api/render-jobs/")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            id: "job-1",
            status: "completed",
            mp3R2Key: "renders/job-1/output.mp3",
            mp4R2Key: "renders/job-1/output.mp4",
            chaptersR2Key: "renders/job-1/chapters.json",
            ...job,
          }),
      })
    }
    if (url.startsWith("/api/offline/cache")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            renderJobId: "job-1",
            mp3Url: artifactUrls ? "/api/r2/artifact/job-1/output.mp3" : null,
            mp4Url: artifactUrls ? "/api/r2/artifact/job-1/output.mp4" : null,
            chaptersUrl: artifactUrls ? "/api/r2/artifact/job-1/chapters.json" : null,
            chapterContentHashes: ["hash-a"],
          }),
      })
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`))
  })
  vi.stubGlobal("fetch", mock)
  return mock
}

function renderSubmitted() {
  return render(
    <RenderPageClient
      songsetId="test-songset"
      initialSongset={SONGSET}
      initialLatestJob={RUNNING_JOB}
      initialPreviousCompletedJob={null}
      initialRenderData={INITIAL_RENDER_DATA}
    />
  )
}

/** Ticks the fake clock in 1 ms steps until the text shows up (dynamic imports settle). */
async function waitForText(text: string) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (screen.queryByText(text)) return
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1)
    })
  }
}

/** Waits for the poll tick and everything it kicks off (including the download chain). */
async function runOnePoll() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
  })
  for (let attempt = 0; attempt < 10; attempt++) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
  }
}

describe("RenderPageClient", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(isOfflineSupportedOnCurrentDevice).mockReturnValue(true)
    vi.mocked(cacheArtifacts).mockResolvedValue(undefined)
    vi.stubGlobal("caches", { open: vi.fn().mockResolvedValue({}) })
    // Anything the component does not expect to call would hit the network.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("unstubbed fetch")))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it("renders server-loaded songset data", () => {
    render(
      <RenderPageClient
        songsetId="test-songset"
        initialSongset={SONGSET}
        initialLatestJob={null}
        initialPreviousCompletedJob={null}
        initialRenderData={INITIAL_RENDER_DATA}
      />
    )

    expect(screen.getByRole("heading", { name: /render/i })).toBeInTheDocument()
    expect(screen.getByText("Sunday Worship")).toBeInTheDocument()
  })

  it("renders zh-Hant heading and back label", () => {
    render(
      <RenderPageClient
        songsetId="test-songset"
        initialSongset={SONGSET}
        initialLatestJob={null}
        initialPreviousCompletedJob={null}
        initialRenderData={INITIAL_RENDER_DATA}
      />,
      "zh-Hant"
    )

    expect(screen.getByRole("heading", { name: "渲染" })).toBeInTheDocument()
    expect(screen.getByLabelText("返回")).toBeInTheDocument()
  })

  it("recovers polling for a still-running job on mount", async () => {
    vi.useFakeTimers()
    const fetchMock = stubRenderFetch()
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(fetchMock).toHaveBeenCalledWith("/api/render-jobs/job-1")
    expect(cacheArtifacts).toHaveBeenCalled()
  })

  it("downloads the finished render for offline when the setting is on", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ offlineAutoCache: true })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(toast.success).toHaveBeenCalledWith(t("en", "render.toast.completed"))
    expect(vi.mocked(cacheArtifacts).mock.calls[0][0]).toBe("job-1")
    expect(vi.mocked(cacheArtifacts).mock.calls[0][1]).toEqual({
      mp3Url: "/api/r2/artifact/job-1/output.mp3",
      mp4Url: "/api/r2/artifact/job-1/output.mp4",
      chaptersUrl: "/api/r2/artifact/job-1/chapters.json",
    })
    expect(putOfflineRecord).toHaveBeenCalledWith(
      expect.objectContaining({
        songsetId: "test-songset",
        songsetName: "Sunday Worship",
        renderJobId: "job-1",
        cachedMp4: true,
      })
    )
  })

  it("caches nothing when the auto-cache setting is off", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ offlineAutoCache: false })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(toast.success).toHaveBeenCalledWith(t("en", "render.toast.completed"))
    expect(cacheArtifacts).not.toHaveBeenCalled()
  })

  it("caches nothing when the setting cannot be read", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ settingsOk: false })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(cacheArtifacts).not.toHaveBeenCalled()
  })

  it("caches nothing on a device without offline caching support", async () => {
    vi.useFakeTimers()
    vi.mocked(isOfflineSupportedOnCurrentDevice).mockReturnValue(false)
    stubRenderFetch({ offlineAutoCache: true })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(cacheArtifacts).not.toHaveBeenCalled()
  })

  it("stops at the completion toast when the finished job has no playable artifact", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ offlineAutoCache: true, job: { mp3R2Key: null, mp4R2Key: null } })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(cacheArtifacts).not.toHaveBeenCalled()
  })

  it("does not make the screen wait for a long download", async () => {
    vi.useFakeTimers()
    // A download that never settles: the closing toast must still land.
    const { promise } = Promise.withResolvers<void>()
    vi.mocked(cacheArtifacts).mockReturnValue(promise)
    stubRenderFetch({ offlineAutoCache: true })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(toast.success).toHaveBeenCalledWith(t("en", "render.toast.completed"))
    expect(screen.getByRole("button", { name: /cancel render/i })).toBeEnabled()
  })

  it("toasts a failed download", async () => {
    vi.useFakeTimers()
    vi.mocked(cacheArtifacts).mockRejectedValue(new Error("disk full"))
    stubRenderFetch({ offlineAutoCache: true })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(toast.error).toHaveBeenCalledWith(t("en", "audio.offline.downloadFailed"))
    expect(screen.getByText("Render Started")).toBeInTheDocument()
  })

  it("toasts the no-artifacts case distinctly", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ offlineAutoCache: true, artifactUrls: false })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()

    expect(toast.error).toHaveBeenCalledWith(t("en", "audio.offline.noArtifacts"))
    expect(cacheArtifacts).not.toHaveBeenCalled()
  })

  it("does not report our own cancel as a render failure", async () => {
    vi.useFakeTimers()

    // The DELETE hangs; meanwhile a poll tick observes the cancelled status.
    const deleteCall = Promise.withResolvers<unknown>()
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === "DELETE") return deleteCall.promise
      if (url.startsWith("/api/render-jobs/")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ id: "job-1", status: "cancelled" }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ settings: { offlineAutoCache: false } }),
      })
    })
    vi.stubGlobal("fetch", fetchMock)
    renderSubmitted()
    await waitForText("Render Started")

    fireEvent.click(screen.getByRole("button", { name: /cancel render/i }))
    await runOnePoll()

    expect(toast.error).not.toHaveBeenCalledWith(t("en", "render.toast.failed"))

    await act(async () => {
      deleteCall.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    await waitForText("Output Options")

    expect(toast.info).toHaveBeenCalledWith(t("en", "render.toast.cancelled"))
  })

  it("returns to the form with a toast when the render fails", async () => {
    vi.useFakeTimers()
    stubRenderFetch({ job: { status: "failed" } })
    renderSubmitted()

    await waitForText("Render Started")

    await runOnePoll()
    await waitForText("Output Options")

    expect(toast.error).toHaveBeenCalledWith(t("en", "render.toast.failed"))
    expect(cacheArtifacts).not.toHaveBeenCalled()
    expect(screen.getByText("Output Options")).toBeInTheDocument()
  })
})
