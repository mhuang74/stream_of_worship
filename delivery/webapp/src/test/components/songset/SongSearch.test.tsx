import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SongSearch } from "@/components/songset/SongSearch";

describe("SongSearch", () => {
  const mockOnSearch = vi.fn();

  const defaultProps = {
    onSearch: mockOnSearch,
    isLoading: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderSearch = (props = {}) => {
    return render(<SongSearch {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders search input", () => {
      renderSearch();
      expect(screen.getByTestId("search-input")).toBeInTheDocument();
    });

    it("renders with correct placeholder", () => {
      renderSearch();
      expect(screen.getByPlaceholderText(/search songs by title/i)).toBeInTheDocument();
    });

    it("renders keyword help text", () => {
      renderSearch();
      expect(screen.getByTestId("keyword-help-text")).toBeInTheDocument();
      expect(screen.getByTestId("keyword-help-text").textContent).toContain("奇異恩典");
    });

    it("renders search icon", () => {
      renderSearch();
      expect(screen.getByLabelText(/search songs/i)).toBeInTheDocument();
    });
  });

  describe("search functionality", () => {
    it("does not call onSearch when typing", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });

      expect(mockOnSearch).not.toHaveBeenCalled();
    });

    it("calls onSearch when Search is pressed", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });
      fireEvent.click(screen.getByTestId("search-button"));

      expect(mockOnSearch).toHaveBeenCalledWith("amazing", undefined);
    });

    it("allows blank Search", () => {
      renderSearch();

      fireEvent.click(screen.getByTestId("search-button"));

      expect(mockOnSearch).toHaveBeenCalledWith("", undefined);
    });

    it("does not call onSearch when cleared", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.click(screen.getByTestId("clear-search-button"));

      expect(mockOnSearch).not.toHaveBeenCalled();
    });

    it("shows clear button when query is not empty", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      expect(screen.getByTestId("clear-search-button")).toBeInTheDocument();
    });

    it("hides clear button when query is empty", () => {
      renderSearch();
      expect(screen.queryByTestId("clear-search-button")).not.toBeInTheDocument();
    });

    it("clears search when clear button is clicked", async () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      const clearButton = screen.getByTestId("clear-search-button");
      fireEvent.click(clearButton);

      expect(input).toHaveValue("");
    });
  });

  describe("loading state", () => {
    it("shows loading indicator when isLoading is true", () => {
      renderSearch({ isLoading: true });
      expect(screen.getByLabelText(/search songs/i).parentElement?.querySelector("svg")).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("has correct aria-label on search input", () => {
      renderSearch();
      expect(screen.getByLabelText(/search songs/i)).toBeInTheDocument();
    });

    it("has correct aria-label on clear button", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      expect(screen.getByLabelText(/clear search/i)).toBeInTheDocument();
    });
  });
});
