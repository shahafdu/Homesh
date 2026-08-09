import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AuthState, type Health } from "./api";
import { login, logout, passkeysSupported, register } from "./auth";
import Browser from "./Browser";
import Player from "./Player";
import Settings from "./Settings";
import { usePlayer } from "./player";
import { applyPrefs, DEFAULT_PREFS, getPrefs, savePrefs, type Prefs } from "./prefs";

export default function App() {
  const [state, setState] = useState<AuthState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [showSettings, setShowSettings] = useState(false);
  const player = usePlayer();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [s, h] = await Promise.all([
      api.get<AuthState>("/api/auth/state"),
      api.get<Health>("/api/health"),
    ]);
    setState(s);
    setHealth(h);

    if (s.authenticated) {
      // Preferences live on the account, so they arrive with the session rather
      // than being re-chosen on every device.
      const p = await getPrefs();
      setPrefs(p);
      applyPrefs(p);
    } else {
      applyPrefs(DEFAULT_PREFS);
    }
  }, []);

  useEffect(() => {
    refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      if (e instanceof ApiError) setError(e.message);
      else if (e instanceof Error && e.name === "NotAllowedError")
        setError("Passkey prompt was dismissed or timed out.");
      else setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const changePrefs = async (patch: Partial<Prefs>) => {
    // Apply immediately, then persist: waiting on the round trip makes choosing a
    // colour feel broken.
    const next = { ...prefs, ...patch };
    setPrefs(next);
    applyPrefs(next);
    try {
      setPrefs(await savePrefs(patch));
    } catch {
      /* the local change stands; it will re-sync on next load */
    }
  };

  if (!state) {
    return (
      <div className="shell">
        <div className="card">
          <p className="sub">Connecting…</p>
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    );
  }

  if (state.authenticated && state.user) {
    return (
      <div className="app">
        <Browser
          isAdmin={state.user.is_admin}
          view={prefs.view}
          onViewChange={(view) => void changePrefs({ view })}
          onOpenSettings={() => setShowSettings(true)}
          onPlay={player.play}
          playingId={player.current?.item_id ?? null}
        />

        {showSettings && (
          <Settings
            prefs={prefs}
            onChange={(patch) => void changePrefs(patch)}
            onClose={() => setShowSettings(false)}
          />
        )}

        <Player
          state={player.state}
          current={player.current}
          onToggle={player.toggle}
          onSkip={player.skip}
          onSeek={player.seek}
          onVolume={player.setVolume}
          onStop={player.stop}
        />

        <footer className="footer">
          <span className="status">
            <span className={`dot${health?.status === "ok" ? "" : " bad"}`} />
            {state.user.display_name} · server {health?.version} · db {health?.database}
          </span>
          <button className="linklike" disabled={busy} onClick={() => run(logout)}>
            Sign out
          </button>
        </footer>
      </div>
    );
  }

  return state.has_users ? (
    <SignIn busy={busy} error={error} onSignIn={() => run(login)} />
  ) : (
    <FirstRun busy={busy} error={error} onRegister={(h, d, c) => run(() => register(h, d, c))} />
  );
}

function SignIn(props: { busy: boolean; error: string | null; onSignIn: () => void }) {
  return (
    <div className="shell">
      <div className="card">
        <h1>Hearth</h1>
        <p className="sub">Sign in with your passkey.</p>

        <button disabled={props.busy || !passkeysSupported()} onClick={props.onSignIn}>
          {props.busy ? "Waiting for passkey…" : "Sign in"}
        </button>

        {!passkeysSupported() && (
          <div className="error">This browser does not support passkeys.</div>
        )}
        {props.error && <div className="error">{props.error}</div>}
      </div>
    </div>
  );
}

function FirstRun(props: {
  busy: boolean;
  error: string | null;
  onRegister: (handle: string, displayName: string, code: string) => void;
}) {
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [code, setCode] = useState("");

  const ready = handle.trim() !== "" && displayName.trim() !== "" && code.trim() !== "";

  return (
    <div className="shell">
      <div className="card">
        <h1>Set up Hearth</h1>
        <p className="sub">No accounts exist yet. Create the first one — it becomes the admin.</p>

        <label htmlFor="handle">Username</label>
        <input id="handle" value={handle} autoComplete="username" onChange={(e) => setHandle(e.target.value)} />

        <label htmlFor="display">Display name</label>
        <input id="display" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />

        <label htmlFor="code">Bootstrap code</label>
        <input
          id="code"
          value={code}
          placeholder="from the server log"
          onChange={(e) => setCode(e.target.value)}
        />

        <button
          disabled={props.busy || !ready || !passkeysSupported()}
          onClick={() => props.onRegister(handle.trim(), displayName.trim(), code.trim())}
        >
          {props.busy ? "Waiting for passkey…" : "Create passkey"}
        </button>

        {props.error && <div className="error">{props.error}</div>}

        <div className="hint">
          Find the code with <code>docker compose logs api</code>. It is regenerated on every
          restart and retired once the first account exists.
        </div>
      </div>
    </div>
  );
}
