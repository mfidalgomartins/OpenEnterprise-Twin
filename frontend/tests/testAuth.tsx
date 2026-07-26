import type { PropsWithChildren } from "react";

import {
  AuthContext,
  type AuthContextValue,
} from "../src/features/auth/authContext";

const authenticatedSession: AuthContextValue = {
  status: "authenticated",
  session: {
    subject: "test-admin",
    tenant_id: "northstar",
    roles: ["admin"],
    authentication_method: "local",
  },
  error: null,
  mode: "local",
  login: async () => undefined,
  logout: async () => undefined,
  completeSignin: async () => undefined,
  can: () => true,
};

export function AuthenticatedTestSession({ children }: PropsWithChildren) {
  return (
    <AuthContext.Provider value={authenticatedSession}>
      {children}
    </AuthContext.Provider>
  );
}
