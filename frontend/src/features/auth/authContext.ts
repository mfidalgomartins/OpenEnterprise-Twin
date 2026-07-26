import { createContext, useContext } from "react";

export type Role = "viewer" | "analyst" | "approver" | "admin";
export type AuthStatus =
  | "loading"
  | "authenticated"
  | "anonymous"
  | "error";

export interface SessionInfo {
  subject: string;
  tenant_id: string;
  roles: Role[];
  authentication_method: "local" | "api_key" | "oidc";
}

export interface AuthConfig {
  mode: "local" | "api_key" | "oidc";
  authority?: string;
  clientId?: string;
  redirectUri?: string;
  postLogoutRedirectUri?: string;
  scope: string;
}

interface OidcUser {
  access_token: string;
  expired?: boolean;
}

interface OidcEvents {
  addAccessTokenExpired(callback: () => void): void;
  removeAccessTokenExpired(callback: () => void): void;
}

export interface OidcManager {
  events: OidcEvents;
  getUser(): Promise<OidcUser | null>;
  signinRedirect(): Promise<void>;
  signinRedirectCallback(): Promise<OidcUser>;
  removeUser(): Promise<void>;
  signoutRedirect(): Promise<void>;
}

export type OidcManagerFactory = (config: AuthConfig) => OidcManager;

export interface AuthContextValue {
  status: AuthStatus;
  session: SessionInfo | null;
  error: string | null;
  mode: AuthConfig["mode"];
  login(): Promise<void>;
  logout(): Promise<void>;
  completeSignin(): Promise<void>;
  can(...roles: Role[]): boolean;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
