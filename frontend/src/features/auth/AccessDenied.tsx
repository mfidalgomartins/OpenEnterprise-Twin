import { Link } from "wouter";

export function AccessDenied() {
  return (
    <section className="access-denied" aria-labelledby="access-denied-title">
      <p className="access-denied__code">403</p>
      <h1 id="access-denied-title">This action is outside your role</h1>
      <p>
        Your session is valid, but the active role does not permit this
        workspace. Ask an administrator to adjust access if this is unexpected.
      </p>
      <Link className="auth-action" href="/">
        Return to briefing
      </Link>
    </section>
  );
}
