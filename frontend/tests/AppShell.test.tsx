import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { AppShell } from "../src/app/AppShell";

const destinations = [
  "Briefing",
  "Twin",
  "Scenarios",
  "Decisions",
  "Reports",
];

function CurrentLocation() {
  const location = useLocation();

  return <span data-testid="location">{location.pathname}</span>;
}

function renderShell(initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AppShell>
        <h1>Decision content</h1>
        <CurrentLocation />
      </AppShell>
    </MemoryRouter>,
  );
}

describe("AppShell", () => {
  it("keeps every executive destination visible without a sidebar", () => {
    renderShell();

    const navigation = screen.getByRole("navigation", { name: /primary/i });

    for (const destination of destinations) {
      expect(within(navigation).getByRole("link", { name: destination })).toBeVisible();
    }

    expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /menu|navigation/i })).not.toBeInTheDocument();
  });

  it("marks the destination that matches the current route", () => {
    renderShell("/scenarios");

    expect(screen.getByRole("link", { name: "Scenarios" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Briefing" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("navigates between destinations without replacing the shell", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("link", { name: "Reports" }));

    expect(screen.getByTestId("location")).toHaveTextContent("/reports");
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("heading", { name: "Decision content" })).toBeVisible();
  });

  it("provides a skip link, semantic main region, and operational context", () => {
    renderShell();

    expect(screen.getByRole("link", { name: /skip to content/i })).toHaveAttribute(
      "href",
      "#main-content",
    );
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    expect(screen.getByRole("link", { name: "OpenEnterprise Twin" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.getByText("Northstar Components")).toBeVisible();
    expect(screen.getByText("Reporting date")).toBeVisible();
    expect(screen.getByText("Currency")).toBeVisible();
    expect(screen.getByText("Model version")).toBeVisible();
    expect(screen.getByText("Data freshness")).toBeVisible();
  });
});
