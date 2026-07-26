import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, vi } from "vitest";

import { AccessDenied } from "../src/features/auth/AccessDenied";
import { AuthProvider } from "../src/features/auth/AuthProvider";
import {
  useAuth,
  type AuthConfig,
  type OidcManager,
} from "../src/features/auth/authContext";
import { clearApiAccessToken } from "../src/lib/api";

const localConfig: AuthConfig = {
  mode: "local",
  scope: "openid profile",
};
const oidcConfig: AuthConfig = {
  mode: "oidc",
  authority: "https://identity.example",
  clientId: "cockpit",
  redirectUri: "http://localhost/auth/callback",
  postLogoutRedirectUri: "http://localhost",
  scope: "openid profile",
};

function sessionResponse() {
  return new Response(
    JSON.stringify({
      subject: "finance-lead",
      tenant_id: "northstar",
      roles: ["analyst"],
      authentication_method: "oidc",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

function manager(
  user: { access_token: string; expired?: boolean } | null,
): OidcManager & {
  getUser: ReturnType<typeof vi.fn>;
  signinRedirect: ReturnType<typeof vi.fn>;
  signinRedirectCallback: ReturnType<typeof vi.fn>;
  removeUser: ReturnType<typeof vi.fn>;
  signoutRedirect: ReturnType<typeof vi.fn>;
} {
  return {
    events: {
      addAccessTokenExpired: vi.fn(),
      removeAccessTokenExpired: vi.fn(),
    },
    getUser: vi.fn().mockResolvedValue(user),
    signinRedirect: vi.fn().mockResolvedValue(undefined),
    signinRedirectCallback: vi
      .fn()
      .mockResolvedValue({ access_token: "callback-token" }),
    removeUser: vi.fn().mockResolvedValue(undefined),
    signoutRedirect: vi.fn().mockResolvedValue(undefined),
  };
}

function SessionProbe() {
  const auth = useAuth();
  return (
    <div>
      <p>{auth.status}</p>
      <p>{auth.session?.tenant_id ?? "no-tenant"}</p>
      <p>{auth.can("analyst", "admin") ? "can-analyze" : "read-only"}</p>
      <button onClick={() => void auth.login()} type="button">
        Login
      </button>
      <button onClick={() => void auth.logout()} type="button">
        Logout
      </button>
      <CallbackButton />
    </div>
  );
}

function CallbackButton() {
  const { completeSignin } = useAuth();
  const [completed, setCompleted] = useState(false);
  return (
    <button
      onClick={() => void completeSignin().then(() => setCompleted(true))}
      type="button"
    >
      {completed ? "Callback complete" : "Complete callback"}
    </button>
  );
}

afterEach(() => {
  clearApiAccessToken();
  vi.unstubAllGlobals();
});

describe("AuthProvider", () => {
  it("loads the backend session as the source of tenant and roles", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionResponse()));

    render(
      <AuthProvider config={localConfig}>
        <SessionProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("northstar")).toBeInTheDocument();
    expect(screen.getByText("authenticated")).toBeInTheDocument();
    expect(screen.getByText("can-analyze")).toBeInTheDocument();
  });

  it("restores an OIDC user and attaches its bearer to the session request", async () => {
    const oidc = manager({ access_token: "restored-token" });
    const fetchMock = vi.fn().mockResolvedValue(sessionResponse());
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider config={oidcConfig} managerFactory={() => oidc}>
        <SessionProbe />
      </AuthProvider>,
    );

    await screen.findByText("northstar");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/session",
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer restored-token",
        }),
      }),
    );
  });

  it("treats an expired OIDC user as anonymous without calling the API", async () => {
    const oidc = manager({ access_token: "expired-token", expired: true });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider config={oidcConfig} managerFactory={() => oidc}>
        <SessionProbe />
      </AuthProvider>,
    );

    expect(await screen.findByText("anonymous")).toBeInTheDocument();
    expect(screen.getByText("no-tenant")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("completes the PKCE callback and clears the session on logout", async () => {
    const oidc = manager(null);
    const fetchMock = vi.fn().mockResolvedValue(sessionResponse());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(
      <AuthProvider config={oidcConfig} managerFactory={() => oidc}>
        <SessionProbe />
      </AuthProvider>,
    );
    await screen.findByText("anonymous");
    await user.click(screen.getByRole("button", { name: "Complete callback" }));
    expect(await screen.findByText("Callback complete")).toBeInTheDocument();
    expect(oidc.signinRedirectCallback).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "Logout" }));
    await waitFor(() =>
      expect(screen.getByText("anonymous")).toBeInTheDocument(),
    );
    expect(oidc.removeUser).toHaveBeenCalledOnce();
    expect(oidc.signoutRedirect).toHaveBeenCalledOnce();
  });
});

test("AccessDenied gives a keyboard-reachable recovery action", () => {
  render(<AccessDenied />);

  expect(
    screen.getByRole("heading", { name: "This action is outside your role" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Return to briefing" }),
  ).toHaveAttribute("href", "/");
});
