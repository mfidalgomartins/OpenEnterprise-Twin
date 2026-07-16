export interface ApiRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

export interface FieldViolation {
  field: string;
  message: string;
}

export interface ApiProblem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  trace_id: string;
  violations: FieldViolation[];
}

export class ApiError extends Error {
  readonly code: string;
  readonly problem: ApiProblem;
  readonly status: number;
  readonly traceId: string;

  constructor(problem: ApiProblem) {
    super(problem.detail);
    this.name = "ApiError";
    this.code = problem.code;
    this.problem = problem;
    this.status = problem.status;
    this.traceId = problem.trace_id;
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function requestHeaders(
  headersInit: HeadersInit | undefined,
  hasBody: boolean,
): Record<string, string> {
  const suppliedHeaders = Object.fromEntries(new Headers(headersInit).entries());
  const headers: Record<string, string> = {
    Accept: suppliedHeaders.accept ?? "application/json",
  };

  if (hasBody) {
    headers["Content-Type"] =
      suppliedHeaders["content-type"] ?? "application/json";
  }

  for (const [name, value] of Object.entries(suppliedHeaders)) {
    if (name !== "accept" && name !== "content-type") {
      headers[name] = value;
    }
  }

  return headers;
}

function fallbackProblem(response: Response, detail: string): ApiProblem {
  return {
    type: "about:blank",
    title: response.statusText || "Request failed",
    status: response.status,
    code: `http_${response.status}`,
    detail: detail || "The request could not be completed.",
    trace_id: response.headers.get("X-Trace-ID") ?? "",
    violations: [],
  };
}

export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const { body, headers: headersInit, ...requestOptions } = options;
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...requestOptions,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: requestHeaders(headersInit, body !== undefined),
  });
  const contentType = response.headers.get("Content-Type") ?? "";
  const isJson = contentType.includes("json");
  const payload: unknown =
    response.status === 204
      ? undefined
      : isJson
        ? await response.json()
        : await response.text();

  if (!response.ok) {
    const problem =
      isJson && payload && typeof payload === "object" && "code" in payload
        ? (payload as ApiProblem)
        : fallbackProblem(response, typeof payload === "string" ? payload : "");

    throw new ApiError(problem);
  }

  return payload as T;
}
