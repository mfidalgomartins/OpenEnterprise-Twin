import { afterEach, vi } from "vitest";

import { ApiError, apiRequest } from "../src/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("requests and parses a typed JSON resource", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await apiRequest<{ status: string }>("/api/v1/health");

    expect(result).toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/health",
      expect.objectContaining({
        headers: expect.objectContaining({ Accept: "application/json" }),
      }),
    );
  });

  it("serializes request bodies as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 41 }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest<{ id: number }>("/api/v1/scenarios", {
      method: "POST",
      body: { scenario_id: "balanced-growth" },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/scenarios",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ scenario_id: "balanced-growth" }),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("throws the API problem contract with status, code, and trace id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Scenario not found",
            status: 404,
            code: "scenario_not_found",
            detail: "The requested scenario does not exist.",
            trace_id: "trace-42",
            violations: [],
          }),
          {
            status: 404,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
      ),
    );

    const request = apiRequest("/api/v1/scenarios/missing");

    await expect(request).rejects.toMatchObject({
      name: "ApiError",
      message: "The requested scenario does not exist.",
      status: 404,
      code: "scenario_not_found",
      traceId: "trace-42",
    } satisfies Partial<ApiError>);
  });
});
