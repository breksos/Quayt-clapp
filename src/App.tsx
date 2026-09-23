import { useCallback, useEffect, useState } from "react";
import { readStatus, subscribeToStatus } from "./bridge";
import type { Snapshot } from "./types";

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const applySnapshot = useCallback((next: Snapshot) => {
    setSnapshot((current) => (!current || next.rev >= current.rev ? next : current));
  }, []);
  const refresh = useCallback(async () => {
    setLoading(true); setError(null);
    try { applySnapshot(await readStatus()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setLoading(false); }
  }, [applySnapshot]);

  useEffect(() => {
    let dead = false;
    let unsubscribe: (() => void) | undefined;
    void subscribeToStatus((next) => {
      applySnapshot(next);
      setError(null); setLoading(false);
    }).then((cleanup) => {
      if (dead) cleanup();
      else unsubscribe = cleanup;
    }).catch((reason) => {
      setError(reason instanceof Error ? reason.message : String(reason));
    });
    void refresh();
    return () => { dead = true; unsubscribe?.(); };
  }, [applySnapshot, refresh]);

  return <main>
    <header className="masthead"><div className="brand-mark" aria-hidden="true">Q</div><div><p className="eyebrow">ClappKit desktop terminal</p><h1>Quayt</h1></div></header>
    <section className="status-panel" aria-labelledby="connection-heading">
      <div className="panel-heading"><div><p className="eyebrow">Shared state</p><h2 id="connection-heading">Connection status</h2></div><span className="status-chip" role="status" aria-live="polite"><span className="status-dot" aria-hidden="true" />{loading && !snapshot ? "Checking" : "Not configured"}</span></div>
      <p className="summary">{snapshot?.connection.summary ?? "Checking the local Quayt process…"}</p>
      <p className="phase-note">Service sign-in and operational controls arrive after the Phase 1 foundation.</p>
      {error && <p className="error" role="alert">Could not read connection status: {error}</p>}
      <button type="button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh status"}</button>
    </section>
    <footer><span>Local desktop shell</span><span aria-label={`State revision ${snapshot?.rev ?? 0}`}>Revision {snapshot?.rev ?? 0}</span></footer>
  </main>;
}
