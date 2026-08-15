import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type AuthState, type Health } from "./api";
import { login, logout, passkeysSupported, register } from "./auth";
import Browser from "./Browser";
import Player from "./Player";
import Settings from "./Settings";
import People from "./People";
import PlayTo from "./PlayTo";
import Viewer from "./Viewer";
import Zones from "./Zones";
import { usePlayer } from "./player";
import type { FileEntry } from "./library";
import { applyPrefs, DEFAULT_PREFS, getPrefs, savePrefs, type Prefs } from "./prefs";

export default function App() {
  const [state, setState] = useState<AuthState | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [prefs, setPrefs] = useState<Prefs>(DEFAULT_PREFS);
  const [showSettings, setShowSettings] = useState(false);
  const player = usePlayer();
  const [viewing, setViewing] = useState<{ files: FileEntry[]; index: number } | null>(null);
  const closeViewer = useCallback(() => setViewing(null), []);
  const [showZones, setShowZones] = useState(false);
  const [showPeople, setShowPeople] = useState(false);
  // An invite link is how somebody else joins, so it is read before anything
  // else decides which screen to show.
  const invite = new URLSearchParams(window.location.search).get("invite");
  const [sendTo, setSendTo] = useState<{ file: FileEntry; siblings: FileEntry[] } | null>(null);
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
          onOpenZones={() => setShowZones(true)}
          onOpenPeople={state.user.is_admin ? () => setShowPeople(true) : undefined}
          onSendTo={(file, siblings) => setSendTo({ file, siblings })}
          onPlay={player.play}
          onView={(files, index) => {
            // Arrowing through a folder should stay within the kind you opened —
            // stepping from a photo onto a PDF is never what was meant. Also drops
            // unavailable files, which would only fail to load.
            const clicked = files[index];
            const peers = files.filter((f) => f.kind === clicked.kind && f.available);
            setViewing({
              files: peers,
              index: Math.max(0, peers.findIndex((f) => f.item_id === clicked.item_id)),
            });
          }}
          playingId={player.current?.item_id ?? null}
        />

        {viewing && (
          <Viewer
            files={viewing.files}
            index={viewing.index}
            onIndex={(index) => setViewing({ ...viewing, index })}
            onClose={closeViewer}
          />
        )}

        {showZones && <Zones onClose={() => setShowZones(false)} />}
        {showPeople && <People onClose={() => setShowPeople(false)} />}

        {sendTo && (
          <PlayTo
            file={sendTo.file}
            siblings={sendTo.siblings}
            onHere={() => {
              const audio = sendTo.siblings.filter((f) => f.kind === "audio" && f.available);
              const index = Math.max(0, audio.findIndex((f) => f.item_id === sendTo.file.item_id));
              if (sendTo.file.kind === "audio") player.play(audio, index, "");
              else setViewing({ files: sendTo.siblings, index: sendTo.siblings.indexOf(sendTo.file) });
              setSendTo(null);
            }}
            onClose={() => setSendTo(null)}
          />
        )}

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

  if (invite) {
    return (
      <AcceptInvite
        code={invite}
        busy={busy}
        error={error}
        onAccept={() => run(() => register("", "", null, invite))}
      />
    );
  }

  return state.has_users ? (
    <SignIn busy={busy} error={error} onSignIn={() => run(login)} />
  ) : (
    <FirstRun busy={busy} error={error} onRegister={(h, d, c) => run(() => register(h, d, c))} />
  );
}

/** The screen an invited person lands on, on their own device.
 *
 * They choose nothing: the name and the access were decided by whoever invited
 * them, so the only action is to create a passkey.
 */
function AcceptInvite(props: {
  code: string;
  busy: boolean;
  error: string | null;
  onAccept: () => void;
}) {
  const [who, setWho] = useState<{ display_name: string } | null>(null);
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    api
      .get<{ display_name: string }>(`/api/auth/invite/${encodeURIComponent(props.code)}`)
      .then(setWho)
      .catch(() => setInvalid(true));
  }, [props.code]);

  if (invalid) {
    return (
      <div className="shell">
        <div className="card">
          <h1>That invitation has expired</h1>
          <p className="sub">Ask whoever invited you to send a new one.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <div className="card">
        <h1>Welcome{who ? `, ${who.display_name}` : ""}</h1>
        <p className="sub">
          Create a passkey on this device to finish. There is no password to choose
          or remember.
        </p>

        <button disabled={props.busy || !who || !passkeysSupported()} onClick={props.onAccept}>
          {props.busy ? "Waiting for passkey…" : "Create passkey"}
        </button>

        {!passkeysSupported() && (
          <div className="error">This browser does not support passkeys.</div>
        )}
        {props.error && <div className="error">{props.error}</div>}

        <div className="hint">
          Use the device you will actually watch on — a passkey belongs to the device
          that creates it.
        </div>
      </div>
    </div>
  );
}

function SignIn(props: { busy: boolean; error: string | null; onSignIn: () => void }) {
  return (
    <div className="shell">
      <div className="card">
        <h1>Homesh</h1>
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
        <h1>Set up Homesh</h1>
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
