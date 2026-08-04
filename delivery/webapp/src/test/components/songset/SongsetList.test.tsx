import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongsetList } from "@/components/songset/SongsetList";
import { RenderState } from "@/components/songset/RenderStatusBadge";

describe("SongsetList", () => {
  const mockSongsets = [
    {
      id: "songset-1",
      name: "Sunday Worship",
      description: "Easter service",
      itemCount: 5,
      durationSeconds: 1200,
      updatedAt: new Date("2024-01-15T10:30:00Z"),
      renderState: "fresh" as RenderState,
      isOfflineAvailable: true,
      isArtifactsStale: false,
    },
    {
      id: "songset-2",
      name: "Youth Service",
      description: null,
      itemCount: 3,
      durationSeconds: 600,
      updatedAt: new Date("2024-01-14T15:00:00Z"),
      renderState: "stale" as RenderState,
      isOfflineAvailable: false,
      isArtifactsStale: true,
    },
  ];

  const defaultProps = {
    songsets: mockSongsets,
    isLoading: false,
    error: null,
    onCreateSongset: vi.fn(),
    onRender: vi.fn(),
    onPlay: vi.fn(),
    onRetry: vi.fn(),
    onRename: vi.fn(),
    onDuplicate: vi.fn(),
    onShare: vi.fn(),
    onDelete: vi.fn(),
    currentPage: 1,
    totalPages: 1,
    onPageChange: vi.fn(),
    search: "",
    onSearchChange: vi.fn(),
    isSearching: false,
  };

  const renderList = (props = {}) => {
    return render(<SongsetList {...defaultProps} {...props} />);
  };

  describe("loading state", () => {
    it("renders loading skeletons when isLoading is true", () => {
      renderList({ isLoading: true });
      // SongsetListSkeleton renders a status region with aria-label "Loading songsets"
      const skeleton = screen.getByRole("status", { name: /loading songsets/i });
      expect(skeleton).toBeInTheDocument();
    });
  });

  describe("error state", () => {
    it("renders error message when error is present", () => {
      renderList({ error: "Failed to load songsets" });
      expect(screen.getByText(/failed to load songsets/i)).toBeInTheDocument();
    });

    it("renders retry button when error is present", () => {
      renderList({ error: "Failed to load songsets" });
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    });
  });

  describe("empty state", () => {
    it("renders empty message when no songsets", () => {
      renderList({ songsets: [] });
      expect(screen.getByText(/no songsets yet/i)).toBeInTheDocument();
    });

    it("renders create button in empty state", () => {
      renderList({ songsets: [] });
      expect(screen.getByRole("button", { name: /create songset/i })).toBeInTheDocument();
    });

    it("opens create dialog when create button clicked", async () => {
      renderList({ songsets: [] });
      fireEvent.click(screen.getByRole("button", { name: /create songset/i }));
      
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/create new songset/i)).toBeInTheDocument();
      });
    });
  });

  describe("songset list display", () => {
    it("renders all songsets", () => {
      renderList();
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
      expect(screen.getByText("Youth Service")).toBeInTheDocument();
    });

    it("renders FAB for creating new songset", () => {
      renderList();
      expect(screen.getByRole("button", { name: /create new songset/i })).toBeInTheDocument();
    });
  });

  describe("create songset dialog", () => {
    it("opens create dialog when FAB clicked", async () => {
      renderList();
      fireEvent.click(screen.getByRole("button", { name: /create new songset/i }));
      
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
      });
    });

    it("has name input field", async () => {
      renderList();
      fireEvent.click(screen.getByRole("button", { name: /create new songset/i }));
      
      await waitFor(() => {
        expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
      });
    });

    it("has description input field", async () => {
      renderList();
      fireEvent.click(screen.getByRole("button", { name: /create new songset/i }));
      
      await waitFor(() => {
        expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
      });
    });

    it("calls onCreateSongset when form submitted", async () => {
      const onCreateSongset = vi.fn().mockResolvedValue(undefined);
      renderList({ onCreateSongset });
      
      fireEvent.click(screen.getByRole("button", { name: /create new songset/i }));
      
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
      });
      
      const nameInput = screen.getByLabelText(/name/i);
      fireEvent.change(nameInput, { target: { value: "New Songset" } });
      
      const createButton = screen.getByRole("button", { name: /^create$/i });
      fireEvent.click(createButton);
      
      await waitFor(() => {
        expect(onCreateSongset).toHaveBeenCalledWith("New Songset", undefined);
      });
    });

    it("disables create button when name is empty", async () => {
      renderList();
      fireEvent.click(screen.getByRole("button", { name: /create new songset/i }));
      
      await waitFor(() => {
        const createButton = screen.getByRole("button", { name: /^create$/i });
        expect(createButton).toBeDisabled();
      });
    });
  });

  describe("delete confirmation dialog", () => {
    it("opens delete dialog when delete is triggered", async () => {
      renderList();
      
      // Find and click the menu button for the first songset
      const menuButtons = screen.getAllByRole("button", { name: /open menu/i });
      fireEvent.click(menuButtons[0]);
      
      await waitFor(() => {
        const deleteItem = screen.getByRole("menuitem", { name: /delete/i });
        fireEvent.click(deleteItem);
      });
      
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/delete songset/i)).toBeInTheDocument();
      });
    });
  });

  describe("rename dialog", () => {
    it("opens rename dialog when rename is triggered", async () => {
      renderList();
      
      const menuButtons = screen.getAllByRole("button", { name: /open menu/i });
      fireEvent.click(menuButtons[0]);
      
      await waitFor(() => {
        const renameItem = screen.getByRole("menuitem", { name: /rename/i });
        fireEvent.click(renameItem);
      });
      
      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/rename songset/i)).toBeInTheDocument();
      });
    });
  });

  describe("callbacks", () => {
    it("calls onRender when render is triggered", async () => {
      const onRender = vi.fn();
      renderList({ onRender });
      
      const menuButtons = screen.getAllByRole("button", { name: /open menu/i });
      fireEvent.click(menuButtons[0]);
      
      await waitFor(() => {
        const renderItem = screen.getByRole("menuitem", { name: /^render$/i });
        fireEvent.click(renderItem);
      });
      
      expect(onRender).toHaveBeenCalledWith("songset-1");
    });

    it("calls onPlay when play is triggered", async () => {
      const onPlay = vi.fn();
      renderList({ onPlay });
      
      const menuButtons = screen.getAllByRole("button", { name: /open menu/i });
      fireEvent.click(menuButtons[0]);
      
      await waitFor(() => {
        const playItems = screen.getAllByRole("menuitem", { name: /play/i });
        fireEvent.click(playItems[0]);
      });
      
      expect(onPlay).toHaveBeenCalledWith("songset-1");
    });

    it("calls onShare when share is triggered", async () => {
      const onShare = vi.fn();
      renderList({ onShare });
      
      const menuButtons = screen.getAllByRole("button", { name: /open menu/i });
      fireEvent.click(menuButtons[0]);
      
      await waitFor(() => {
        const shareItem = screen.getByRole("menuitem", { name: /share/i });
        fireEvent.click(shareItem);
      });
      
      expect(onShare).toHaveBeenCalledWith("songset-1");
    });
  });

  describe("search bar", () => {
    it("renders search input with aria-label", () => {
      renderList();
      expect(screen.getByLabelText(/search songsets/i)).toBeInTheDocument();
    });

    it("calls onSearchChange when typing in search input", () => {
      const onSearchChange = vi.fn();
      renderList({ onSearchChange });

      const input = screen.getByLabelText(/search songsets/i);
      fireEvent.change(input, { target: { value: "Sunday" } });

      expect(onSearchChange).toHaveBeenCalledWith("Sunday");
    });

    it("does not show clear button when search is empty", () => {
      renderList({ search: "" });
      expect(screen.queryByLabelText(/clear search/i)).not.toBeInTheDocument();
    });

    it("shows clear button when search has text", () => {
      renderList({ search: "Sunday" });
      expect(screen.getByLabelText(/clear search/i)).toBeInTheDocument();
    });

    it("calls onSearchChange with empty string when clear button clicked", () => {
      const onSearchChange = vi.fn();
      renderList({ search: "Sunday", onSearchChange });

      fireEvent.click(screen.getByLabelText(/clear search/i));

      expect(onSearchChange).toHaveBeenCalledWith("");
    });

    it("shows loading spinner when isSearching is true", () => {
      renderList({ search: "Sun", isSearching: true });
      expect(screen.getByRole("status")).toHaveTextContent(/searching songsets/i);
    });

    it("does not show loading spinner when isSearching is false", () => {
      renderList({ isSearching: false });
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  describe("pagination", () => {
    it("does not render pagination when totalPages <= 1", () => {
      renderList({ totalPages: 1, currentPage: 1 });
      expect(screen.queryByLabelText(/songset pagination/i)).not.toBeInTheDocument();
    });

    it("renders pagination nav when totalPages > 1", () => {
      renderList({ totalPages: 3, currentPage: 1 });
      expect(screen.getByLabelText(/songset pagination/i)).toBeInTheDocument();
    });

    it("renders correct page number buttons", () => {
      renderList({ totalPages: 3, currentPage: 1 });
      expect(screen.getByTestId("pagination-page-1")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-2")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-3")).toBeInTheDocument();
    });

    it("marks current page with aria-current=page", () => {
      renderList({ totalPages: 3, currentPage: 2 });
      expect(screen.getByTestId("pagination-page-2")).toHaveAttribute("aria-current", "page");
    });

    it("does not mark non-current pages with aria-current", () => {
      renderList({ totalPages: 3, currentPage: 2 });
      expect(screen.getByTestId("pagination-page-1")).not.toHaveAttribute("aria-current");
      expect(screen.getByTestId("pagination-page-3")).not.toHaveAttribute("aria-current");
    });

    it("calls onPageChange with currentPage - 1 when Prev clicked", () => {
      const onPageChange = vi.fn();
      renderList({ totalPages: 3, currentPage: 2, onPageChange });

      fireEvent.click(screen.getByTestId("pagination-prev"));

      expect(onPageChange).toHaveBeenCalledWith(1);
    });

    it("calls onPageChange with currentPage + 1 when Next clicked", () => {
      const onPageChange = vi.fn();
      renderList({ totalPages: 3, currentPage: 2, onPageChange });

      fireEvent.click(screen.getByTestId("pagination-next"));

      expect(onPageChange).toHaveBeenCalledWith(3);
    });

    it("disables Prev button on first page", () => {
      renderList({ totalPages: 3, currentPage: 1 });
      expect(screen.getByTestId("pagination-prev")).toBeDisabled();
    });

    it("disables Next button on last page", () => {
      renderList({ totalPages: 3, currentPage: 3 });
      expect(screen.getByTestId("pagination-next")).toBeDisabled();
    });

    it("shows limited page numbers when totalPages > 5", () => {
      renderList({ totalPages: 10, currentPage: 5 });
      // Should show 5 page buttons around current page
      expect(screen.getByTestId("pagination-page-3")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-4")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-5")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-6")).toBeInTheDocument();
      expect(screen.getByTestId("pagination-page-7")).toBeInTheDocument();
      expect(screen.queryByTestId("pagination-page-1")).not.toBeInTheDocument();
      expect(screen.queryByTestId("pagination-page-10")).not.toBeInTheDocument();
    });
  });

  describe("empty state with search", () => {
    it("shows no-match message when search is non-empty and list is empty", () => {
      renderList({ songsets: [], search: "nonexistent" });
      expect(screen.getByText(/no songsets match your search/i)).toBeInTheDocument();
    });

    it("shows default empty message when search is empty and list is empty", () => {
      renderList({ songsets: [], search: "" });
      expect(screen.getByText(/no songsets yet/i)).toBeInTheDocument();
    });

    it("hides create button when search is active and no results", () => {
      renderList({ songsets: [], search: "nonexistent" });
      expect(screen.queryByRole("button", { name: /create songset/i })).not.toBeInTheDocument();
    });

    it("shows search bar in empty state when search yields no results", () => {
      renderList({ songsets: [], search: "nonexistent" });
      expect(screen.getByLabelText(/search songsets/i)).toBeInTheDocument();
    });
  });
});
