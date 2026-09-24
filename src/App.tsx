import { FormEvent, useCallback, useEffect, useState } from "react";
import { runCommand, subscribeToStatus } from "./bridge";
import type { GuiRequest, Snapshot } from "./types";

type Action = "connect" | "login" | "logout" | "tenant" | "service" | null;

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [serviceUrl, setServiceUrl] = useState("");
  const [selectedTenant, setSelectedTenant] = useState("");
  const [action, setAction] = useState<Action>(null);
  const [clientError, setClientError] = useState(false);

  const applySnapshot = useCallback((next: Snapshot) => {
    if (!next?.ok) return;
    setSnapshot((current) => (!current || next.rev >= current.rev ? next : current));
  }, []);

  const execute = useCallback(async (request: GuiRequest, nextAction: Action) => {
    setAction(nextAction);
    setClientError(false);
    try {
      applySnapshot(await runCommand(request));
    } catch {
      setClientError(true);
    } finally {
      setAction(null);
    }
  }, [applySnapshot]);

  useEffect(() => {
    let dead = false;
    let unsubscribe: (() => void) | undefined;
    void subscribeToStatus(applySnapshot).then((cleanup) => {
      if (dead) cleanup(); else unsubscribe = cleanup;
    }).catch(() => setClientError(true));
    void execute({ cmd: "state" }, null);
    return () => { dead = true; unsubscribe?.(); };
  }, [applySnapshot, execute]);

  useEffect(() => {
    if (snapshot?.connection.serviceUrl) setServiceUrl(snapshot.connection.serviceUrl);
  }, [snapshot?.connection.serviceUrl]);

  useEffect(() => {
    setSelectedTenant(snapshot?.tenants.active?.id ?? snapshot?.tenants.available[0]?.id ?? "");
  }, [snapshot?.tenants.active?.id, snapshot?.tenants.available]);

  const configure = (event: FormEvent) => {
    event.preventDefault();
    void execute({ cmd: "configureService", serviceUrl }, "service");
  };

  const selectTenant = (event: FormEvent) => {
    event.preventDefault();
    if (selectedTenant) void execute({ cmd: "selectTenant", tenantId: selectedTenant }, "tenant");
  };

  const connectionLabel = snapshot?.connection.status === "ready" ? "Service ready"
    : snapshot?.connection.status === "checking" ? "Checking service"
      : snapshot?.connection.status === "unavailable" ? "Service unavailable" : "Setup required";
  const authLabel = action === "login" || snapshot?.authentication.status === "signingIn" ? "Browser sign-in pending"
    : snapshot?.authentication.status === "authenticated" ? "Signed in"
      : snapshot?.authentication.status === "logoutPending" ? "Sign-out incomplete" : "Signed out";

  return <main>
    <header className="masthead">
      <div className="brand-mark" aria-hidden="true">Q</div>
      <div><p className="eyebrow">ClappKit desktop terminal</p><h1>Quayt</h1></div>
    </header>

    <div className="workspace">
      <section className="status-panel" aria-labelledby="connection-heading">
        <div className="panel-heading">
          <div><p className="eyebrow">Development service</p><h2 id="connection-heading">Connection</h2></div>
          <span className={`status-chip ${snapshot?.connection.status ?? "checking"}`} role="status" aria-live="polite"><span className="status-dot" aria-hidden="true" />{connectionLabel}</span>
        </div>
        <p className="summary">{snapshot?.connection.summary ?? "Reading local status…"}</p>

        <form className="service-form" onSubmit={configure}>
          <label htmlFor="service-url">Service URL</label>
          <div className="field-row">
            <input id="service-url" name="serviceUrl" type="url" inputMode="url" autoComplete="url" spellCheck="false" placeholder="https://quayt.example" value={serviceUrl} onChange={(event) => setServiceUrl(event.target.value)} required />
            <button type="submit" disabled={action !== null}>{action === "service" ? "Checking…" : "Save and connect"}</button>
          </div>
          <p className="hint">HTTPS is required. Explicit loopback HTTP addresses are accepted for local development.</p>
        </form>

        {snapshot?.connection.status === "unavailable" && <button className="secondary" type="button" onClick={() => void execute({ cmd: "connect" }, "connect")} disabled={action !== null}>{action === "connect" ? "Checking…" : "Retry connection"}</button>}
      </section>

      <section className="status-panel" aria-labelledby="session-heading">
        <div className="panel-heading">
          <div><p className="eyebrow">Secure session</p><h2 id="session-heading">Authentication</h2></div>
          <span className={`status-chip ${snapshot?.authentication.status ?? "signedOut"}`} role="status" aria-live="polite"><span className="status-dot" aria-hidden="true" />{authLabel}</span>
        </div>
        <p className="summary compact">{snapshot?.authentication.summary ?? "No active session."}</p>

        {snapshot?.connection.status === "ready" && snapshot.authentication.status === "signedOut" && action !== "login" && <button type="button" onClick={() => void execute({ cmd: "login" }, "login")} disabled={action !== null}>Sign in with browser</button>}
        {(action === "login" || snapshot?.authentication.status === "signingIn") && <div className="browser-wait"><p>Complete sign-in in the browser window. Quayt accepts one callback for two minutes.</p><button className="secondary" type="button" onClick={() => void execute({ cmd: "cancelLogin" }, null)}>Cancel sign-in</button></div>}
        {snapshot?.authentication.status === "authenticated" && <button className="secondary" type="button" onClick={() => void execute({ cmd: "logout" }, "logout")} disabled={action !== null}>{action === "logout" ? "Signing out…" : "Sign out"}</button>}
        {snapshot?.authentication.status === "logoutPending" && <button type="button" onClick={() => void execute({ cmd: "logout" }, "logout")} disabled={action !== null}>Retry secure sign-out</button>}
      </section>

      {snapshot?.authentication.status === "authenticated" && <section className="status-panel tenant-panel" aria-labelledby="tenant-heading">
        <div><p className="eyebrow">Server-verified membership</p><h2 id="tenant-heading">Workspace</h2></div>
        {snapshot.tenants.active && <p className="active-tenant"><span>Active workspace</span><strong>{snapshot.tenants.active.name}</strong></p>}
        {snapshot.tenants.available.length > 0 ? <form onSubmit={selectTenant}>
          <label htmlFor="tenant">Choose a workspace</label>
          <div className="field-row">
            <select id="tenant" value={selectedTenant} onChange={(event) => setSelectedTenant(event.target.value)}>
              {snapshot.tenants.available.map((tenant) => <option key={tenant.id} value={tenant.id}>{tenant.name}</option>)}
            </select>
            <button type="submit" disabled={!selectedTenant || action !== null}>{action === "tenant" ? "Switching…" : "Use workspace"}</button>
          </div>
        </form> : <p className="summary compact">No active tenant memberships are available.</p>}
      </section>}

      {(snapshot?.issue || clientError) && <div className="error" role="alert"><strong>Quayt needs attention.</strong><span>{snapshot?.issue?.summary ?? "The desktop request could not be completed. Try again."}</span></div>}
    </div>

    <footer><span>Credentials stay in the operating system credential store.</span><span aria-label={`State revision ${snapshot?.rev ?? 0}`}>Revision {snapshot?.rev ?? 0}</span></footer>
  </main>;
}
