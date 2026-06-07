import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { RenderPageClient } from "@/app/songsets/[id]/render/RenderPageClient"

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: mockPush,
  }),
}))

describe("RenderPageClient", () => {
  it("renders server-loaded songset data", () => {
    render(
      <RenderPageClient
        songsetId="test-songset"
        initialSongset={{
          id: "test-songset",
          name: "Sunday Worship",
          description: "Easter service",
          markedLineCount: 0,
          renderState: "unrendered",
          songTitles: [],
          lastCompletedRenderJobId: null,
        }}
        initialLatestJob={null}
        initialPreviousCompletedJob={null}
        initialRenderData={{
          audioEnabled: true,
          videoEnabled: true,
          template: "dark",
          resolution: "720p",
          fontSizePreset: "M",
          fontFamily: "noto_serif_tc",
          includeTitleCard: false,
          titleCardDurationSeconds: 10,
          titleCardLines: [],
          offlineEnabled: false,
        }}
      />
    )

    expect(screen.getByRole("heading", { name: /render/i })).toBeInTheDocument()
    expect(screen.getByText("Sunday Worship")).toBeInTheDocument()
  })
})
