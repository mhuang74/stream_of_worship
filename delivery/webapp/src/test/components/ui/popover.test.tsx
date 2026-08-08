import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";

describe("PopoverContent", () => {
  it("positioner className contains z-[70] (not z-50)", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Open</Button>
        </PopoverTrigger>
        <PopoverContent side="top">Content</PopoverContent>
      </Popover>,
    );

    await user.click(screen.getByText("Open"));

    await waitFor(() => {
      expect(screen.getByText("Content")).toBeInTheDocument();
    });

    const positioner = document.querySelector("[class*='z-[70]']");
    expect(positioner).not.toBeNull();
    expect(positioner?.className).not.toContain("z-50");
  });

  it("popup has data-side='top' when side='top' is passed", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Open</Button>
        </PopoverTrigger>
        <PopoverContent side="top">Content</PopoverContent>
      </Popover>,
    );

    await user.click(screen.getByText("Open"));

    await waitFor(() => {
      expect(screen.getByText("Content")).toBeInTheDocument();
    });

    const popup = document.querySelector("[data-slot='popover-content']");
    expect(popup?.getAttribute("data-side")).toBe("top");
  });
});
