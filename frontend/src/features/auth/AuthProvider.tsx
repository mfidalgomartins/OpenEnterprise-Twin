import {
  UserManager,
  WebStorageStateStore,
  type UserManagerSettings,
} from "oidc-client-ts";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";

import {
  apiRequest,
  clearApiAccessToken,
  setApiAccessToken,
} from "../../lib/api";
import { readAuthConfig } from "./authConfig";
import {
  AuthContext,
  type AuthConfig,
  type AuthContextValue,
  type AuthStatus,
  type OidcManager,
  type OidcManagerFactory,
  type SessionInfo,
} from "./authContext";

function createOidcManager(config: AuthConfig): OidcManager {
  if (!config.authority || !config.clientId || !config.redirectUri) {
    throw new Error("OIDC runtime configuration is incomplete.");
  }
  const settings: UserManagerSettings = {
    authority: config.authority,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    post_logout_redirect_uri: config.postLogoutRedirectUri,
    response_type: "code",
    scope: config.scope,
    automaticSilentRenew: false,
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
  };
  return new UserManager(settings) as OidcManager;
}

interface AuthProviderProps extends PropsWithChildren {
  config?: AuthConfig;
  managerFactory?: OidcManagerFactory;
}

export function AuthProvider({
  children,
  config: suppliedConfig,
  managerFactory = createOidcManager,
}: AuthProviderProps) {
  const config = useMemo(
    () => suppliedConfig ?? readAuthConfig(),
    [suppliedConfig],
  );
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const manager = useMemo(() => {
    if (config.mode !== "oidc") {
      return null;
    }
    try {
      return managerFactory(config);
    } catch {
      return null;
    }
  }, [config, managerFactory]);

  const establishSession = useCallback(async (token: string | null) => {
    if (token) {
      setApiAccessToken(token);
    } else {
      clearApiAccessToken();
    }
    const effectiveSession = await apiRequest<SessionInfo>("/api/v1/session");
    setSession(effectiveSession);
    setError(null);
    setStatus("authenticated");
  }, []);

  const expireSession = useCallback(() => {
    clearApiAccessToken();
    setSession(null);
    setError(null);
    setStatus("anonymous");
  }, []);

  useEffect(() => {
    let active = true;
    const initialize = async () => {
      setStatus("loading");
      try {
        if (config.mode !== "oidc") {
          await establishSession(null);
          return;
        }
        if (!manager) {
          throw new Error("OIDC runtime configuration is incomplete.");
        }
        const user = await manager.getUser();
        if (!active) {
          return;
        }
        if (!user || user.expired || !user.access_token) {
          expireSession();
          return;
        }
        await establishSession(user.access_token);
      } catch (reason) {
        if (!active) {
          return;
        }
        clearApiAccessToken();
        setSession(null);
        setError(
          reason instanceof Error
            ? reason.message
            : "Authentication could not be completed.",
        );
        setStatus("error");
      }
    };
    void initialize();
    return () => {
      active = false;
      clearApiAccessToken();
    };
  }, [config.mode, establishSession, expireSession, manager]);

  useEffect(() => {
    if (!manager) {
      return;
    }
    manager.events.addAccessTokenExpired(expireSession);
    return () => {
      manager.events.removeAccessTokenExpired(expireSession);
    };
  }, [expireSession, manager]);

  const login = useCallback(async () => {
    if (!manager) {
      setError("Interactive login is not configured for this deployment.");
      setStatus("error");
      return;
    }
    setError(null);
    await manager.signinRedirect();
  }, [manager]);

  const completeSignin = useCallback(async () => {
    if (!manager) {
      throw new Error("OIDC runtime configuration is incomplete.");
    }
    setStatus("loading");
    const user = await manager.signinRedirectCallback();
    if (user.expired || !user.access_token) {
      expireSession();
      throw new Error("The returned identity session is expired.");
    }
    await establishSession(user.access_token);
  }, [establishSession, expireSession, manager]);

  const logout = useCallback(async () => {
    expireSession();
    if (!manager) {
      return;
    }
    await manager.removeUser();
    if (config.postLogoutRedirectUri) {
      await manager.signoutRedirect();
    }
  }, [config.postLogoutRedirectUri, expireSession, manager]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      session,
      error,
      mode: config.mode,
      login,
      logout,
      completeSignin,
      can: (...roles) =>
        session !== null && roles.some((role) => session.roles.includes(role)),
    }),
    [config.mode, completeSignin, error, login, logout, session, status],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
