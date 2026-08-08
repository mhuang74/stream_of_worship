import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Popover, PopoverTrigger, PopoverContent } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";

describe("PopoverContent", () => {
  it("default positioner className is z-50", async () => {
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

    const positioner = document.querySelector("[class*='z-50']");
    expect(positioner).not.toBeNull();
    expect(positioner?.className).not.toContain("z-[70]");
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

  it("positionerClassName override is forwarded", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Open</Button>
        </PopoverTrigger>
        <PopoverContent positionerClassName="z-[65]">Content</PopoverContent>
      </Popover>,
    );

    await user.click(screen.getByText("Open"));

    await waitFor(() => {
      expect(screen.getByText("Content")).toBeInTheDocument();
    });

    const positioner = document.querySelector("[class*='z-\\[65\\]']");
    expect(positioner).not.toBeNull();
    expect(positioner?.className).toContain("z-[65]");
    expect(positioner?.className).not.toContain("z-50");
  });

  it("collisionAvoidance is forwarded to Positioner", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Open</Button>
        </PopoverTrigger>
        <PopoverContent side="top" collisionAvoidance={{ side: "none", fallbackAxisSide: "none" }}>
          Content
        </PopoverContent>
      </Popover>,
    );

    await user.click(screen.getByText("Open"));

    await waitFor(() => {
      expect(screen.getByText("Content")).toBeInTheDocument();
    });

    const popup = document.querySelector("[data-slot='popover-content']");
    expect(popup).not.toBeNull();
    expect(popup?.getAttribute("data-side")).toBe("top");
    const positioner = popup?.parentElement;
    expect(positioner).not.toBeNull();
  });

  it("stacking-order classname guard (jsdom does not compute stacking; this is a classname regression guard, not a visual stacking test)", async () => {
    const user = userEvent.setup();
    render(
      <Popover>
        <PopoverTrigger asChild>
          <Button>Open</Button>
        </PopoverTrigger>
        <PopoverContent>Content</PopoverContent>
      </Popover>,
    );

    await user.click(screen.getByText("Open"));

    await waitFor(() => {
      expect(screen.getByText("Content")).toBeInTheDocument();
    });

    const positioner = document.querySelector("[class*='z-50']");
    expect(positioner).not.toBeNull();
    expect(positioner?.className).toContain("z-50");
  });
});
