import { render, screen } from "@testing-library/react";

import { Status } from "../src/components/Status";

describe("Status", () => {
  it("pairs a visible label with a non-essential visual marker", () => {
    render(<Status tone="success">Ready for review</Status>);

    const status = screen.getByText("Ready for review");

    expect(status).toBeVisible();
    expect(status.querySelector("[aria-hidden='true']")).toBeInTheDocument();
  });
});
