import { render, screen } from "@testing-library/react";

import { App } from "../src/app/App";

describe("App", () => {
  it("renders the routed report workspace inside the persistent shell", () => {
    window.history.pushState({}, "", "/reports");

    render(<App />);

    expect(screen.getByRole("heading", { name: "Reports" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeVisible();

    window.history.pushState({}, "", "/");
  });
});
