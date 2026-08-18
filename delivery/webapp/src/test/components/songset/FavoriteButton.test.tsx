import { describe, it, expect, vi } from "vitest";
import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithLocale as render } from "@/test/render";
import { FavoriteButton } from "@/components/songset/FavoriteButton";
import {
  markSongCompleted,
  resetCompletionForTests,
} from "@/lib/audio/completion";

describe("FavoriteButton (completion-gated heart)", () => {
  it("is disabled until the song is Completed (heard ≥90%)", () => {
    resetCompletionForTests();
    render(<FavoriteButton songId="s1" isFavorite={false} onToggle={vi.fn()} />);
    const button = screen.getByTestId("favorite-button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("data-eligible", "false");
  });

  it("is enabled and toggles once the song is Completed", async () => {
    resetCompletionForTests();
    markSongCompleted("s1");
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(<FavoriteButton songId="s1" isFavorite={false} onToggle={onToggle} />);
    const button = screen.getByTestId("favorite-button");
    expect(button).toBeEnabled();
    expect(button).toHaveAttribute("data-eligible", "true");

    await user.click(button);
    expect(onToggle).toHaveBeenCalledWith("s1");
  });

  it("enables live the moment the song crosses the threshold", async () => {
    resetCompletionForTests();
    const onToggle = vi.fn();
    render(<FavoriteButton songId="s1" isFavorite={false} onToggle={onToggle} />);
    const button = screen.getByTestId("favorite-button");
    expect(button).toBeDisabled();

    // The subscription flips component state; React needs act() to flush the
    // update triggered from outside an event handler. Once completed, the card
    // switches out of the tooltip-wrapped branch, so re-query the fresh node.
    await act(async () => {
      markSongCompleted("s1");
    });
    await waitFor(() =>
      expect(screen.getByTestId("favorite-button")).toBeEnabled()
    );
  });

  it("allows unfavoriting regardless of completion", async () => {
    resetCompletionForTests();
    const onToggle = vi.fn();
    const user = userEvent.setup();
    render(<FavoriteButton songId="s1" isFavorite onToggle={onToggle} />);
    const button = screen.getByTestId("favorite-button");
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onToggle).toHaveBeenCalledWith("s1");
  });

  it("localizes the favorite aria-label in zh-Hant", () => {
    resetCompletionForTests();
    render(<FavoriteButton songId="s1" isFavorite onToggle={vi.fn()} />, "zh-Hant");
    const button = screen.getByTestId("favorite-button");
    expect(button).toHaveAttribute("aria-label", "移除最愛");
  });
});
