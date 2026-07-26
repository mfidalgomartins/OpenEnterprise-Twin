import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";

import { useAuth } from "./authContext";

export function CallbackPage() {
  const { completeSignin } = useAuth();
  const [, navigate] = useLocation();
  const started = useRef(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (started.current) {
      return;
    }
    started.current = true;
    void completeSignin()
      .then(() => navigate("/", { replace: true }))
      .catch((reason: unknown) => {
        setError(
          reason instanceof Error
            ? reason.message
            : "Sign-in could not be completed.",
        );
      });
  }, [completeSignin, navigate]);

  return (
    <main className="auth-page" aria-live="polite">
      <div className="auth-panel">
        <BrandCopy />
        {error ? (
          <>
            <h1>Sign-in could not be completed</h1>
            <p>{error}</p>
            <a className="auth-action" href="/">
              Return to sign in
            </a>
          </>
        ) : (
          <>
            <h1>Completing secure sign-in</h1>
            <p>Validating the authorization response and effective access.</p>
          </>
        )}
      </div>
    </main>
  );
}

function BrandCopy() {
  return <p className="auth-panel__brand">OpenEnterprise Twin</p>;
}
