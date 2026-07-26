import type { AuthConfig } from "./authContext";

function envValue(name: string): string | undefined {
  const value = import.meta.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function readAuthConfig(): AuthConfig {
  const rawMode = envValue("VITE_AUTH_MODE") ?? "local";
  const mode =
    rawMode === "oidc" || rawMode === "api_key" ? rawMode : "local";
  return {
    mode,
    authority: envValue("VITE_OIDC_AUTHORITY"),
    clientId: envValue("VITE_OIDC_CLIENT_ID"),
    redirectUri:
      envValue("VITE_OIDC_REDIRECT_URI") ??
      `${window.location.origin}/auth/callback`,
    postLogoutRedirectUri:
      envValue("VITE_OIDC_POST_LOGOUT_REDIRECT_URI") ??
      window.location.origin,
    scope: envValue("VITE_OIDC_SCOPE") ?? "openid profile",
  };
}
