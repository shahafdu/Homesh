import { useCallback, useEffect, useRef, useState } from "react";

/** A screen that has joined the house.
 *
 * It holds a socket to the server, waits to be told what to play, and reports
 * back where it has got to. It never decides for itself — the server owns
 * playback state, which is what lets a phone drive this room and then walk away
 * (ARCHITECTURE.md §5.8).
 */

type Phase = "starting" | "pairing" | "idle" | "playing";

interface Command {
  type: string;
  url?: string;
  item_id?: string;
  filename?: string;
  tags?: string;
  kind?: "audio" | "video" | "photo";
  position_ms?: number;
  volume?: number;
}

const STORAGE_KEY = "homesh.tv.credential";

/** Stable per-installation identity, so re-pairing updates this screen rather
 *  than creating a second one. Survives reloads; regenerated only on reinstall. */
function deviceKey(): string {
  const existing = localStorage.getItem("homesh.tv.device");
  if (existing) return existing;
  const fresh = `tv-${crypto.randomUUID()}`;
  localStorage.setItem("homesh.tv.device", fresh);
  return fresh;
}

function formatTime(ms: number): string {
  if (!isFinite(ms) || ms <= 0) return "0:00";
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export default function TvApp() {
  const [phase, setPhase] = useState<Phase>("starting");
  const [code, setCode] = useState<string | null>(null);
  const [zoneName, setZoneName] = useState("This screen");
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState<Command | null>(null);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);

  const mediaRef = useRef<HTMLVideoElement | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const tokenRef = useRef<string | null>(localStorage.getItem(STORAGE_KEY));

  // ── Pairing ───────────────────────────────────────────────────────────────

  // connect() and startPairing() each need to call the other — a refused
  // credential sends us back to pairing, and a successful pairing must open the
  // socket. A ref breaks the cycle without either capturing a stale version.
  const connectRef = useRef<() => void>(() => undefined);

  const startPairing = useCallback(async () => {
    const res = await fetch("/api/renderers/pair/begin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_key: deviceKey(), device_name: "TV" }),
    });
    const { code, poll_token } = await res.json();
    setCode(code);
    setPhase("pairing");

    // Poll rather than hold a socket: pairing is brief, and a screen with no
    // credential has nothing to authenticate a socket with.
    const poll = window.setInterval(async () => {
      try {
        const r = await fetch(`/api/renderers/pair/status?poll_token=${poll_token}`);
        if (!r.ok) return;
        const body = await r.json();
        if (body.status === "paired") {
          window.clearInterval(poll);
          localStorage.setItem(STORAGE_KEY, body.device_token);
          tokenRef.current = body.device_token;
          setZoneName(body.name ?? "This screen");
          setPhase("idle");
          // Open the command channel straight away. Without this the screen sits
          // saying "connecting" until it is restarted.
          connectRef.current();
        } else if (body.status === "expired") {
          window.clearInterval(poll);
          void startPairing();   // a stale code helps nobody; show a fresh one
        }
      } catch {
        /* the server may be restarting; the next tick will retry */
      }
    }, 2000);
  }, []);

  // ── Command channel ───────────────────────────────────────────────────────

  const connect = useCallback(() => {
    const token = tokenRef.current;
    if (!token) return;

    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/api/renderers/ws?token=${token}`);
    socketRef.current = socket;

    socket.onopen = () => {
      setConnected(true);
      setPhase((p) => (p === "playing" ? p : "idle"));
    };

    socket.onclose = (event) => {
      setConnected(false);
      socketRef.current = null;

      // 1008 means the credential was refused — pairing was revoked or the
      // server forgot us. Retrying forever would be a screen stuck saying
      // "connecting"; asking to be paired again is the honest response.
      if (event.code === 1008) {
        localStorage.removeItem(STORAGE_KEY);
        tokenRef.current = null;
        void startPairing();
        return;
      }
      // Otherwise the server is restarting or the network blipped. Keep trying:
      // this screen may be unattended for weeks.
      window.setTimeout(connect, 3000);
    };

    socket.onmessage = (event) => {
      let cmd: Command;
      try {
        cmd = JSON.parse(event.data);
      } catch {
        return;
      }
      handle(cmd);
    };
  }, [startPairing]);

  const handle = useCallback((cmd: Command) => {
    const media = mediaRef.current;
    switch (cmd.type) {
      case "play":
        setNow(cmd);
        setPhase("playing");
        // The element mounts with this render, so defer until it exists.
        window.setTimeout(() => {
          const el = mediaRef.current;
          if (!el || !cmd.url) return;
          el.src = cmd.url;
          if (cmd.position_ms) el.currentTime = cmd.position_ms / 1000;
          void el.play().catch(() => undefined);
        }, 0);
        break;
      case "pause":
        media?.pause();
        break;
      case "resume":
        void media?.play().catch(() => undefined);
        break;
      case "stop":
        media?.pause();
        setNow(null);
        setPhase("idle");
        break;
      case "seek":
        if (media && cmd.position_ms != null) media.currentTime = cmd.position_ms / 1000;
        break;
      case "volume":
        if (media && cmd.volume != null) media.volume = Math.max(0, Math.min(1, cmd.volume / 100));
        break;
    }
  }, []);

  const report = useCallback((state: string) => {
    const socket = socketRef.current;
    const media = mediaRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    socket.send(
      JSON.stringify({
        type: "state",
        state,
        item_id: now?.item_id,
        position_ms: media ? Math.round(media.currentTime * 1000) : 0,
        duration_ms: media && isFinite(media.duration) ? Math.round(media.duration * 1000) : null,
      }),
    );
  }, [now]);

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  useEffect(() => {
    if (tokenRef.current) {
      setPhase("idle");
      connect();
    } else {
      void startPairing();
    }
    // Deliberately once: this is startup, and re-running it would open a second
    // socket every time a callback identity changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // A screen may sit untouched for weeks, so tell the server we are still here
    // rather than waiting for it to notice a dead socket.
    const beat = window.setInterval(() => {
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
    }, 20000);
    return () => window.clearInterval(beat);
  }, []);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media) return;

    const onTime = () => setPosition(media.currentTime * 1000);
    const onMeta = () => setDuration(isFinite(media.duration) ? media.duration * 1000 : 0);
    const onPlay = () => report("playing");
    const onPause = () => report("paused");
    const onEnded = () => report("ended");

    media.addEventListener("timeupdate", onTime);
    media.addEventListener("loadedmetadata", onMeta);
    media.addEventListener("play", onPlay);
    media.addEventListener("pause", onPause);
    media.addEventListener("ended", onEnded);
    return () => {
      media.removeEventListener("timeupdate", onTime);
      media.removeEventListener("loadedmetadata", onMeta);
      media.removeEventListener("play", onPlay);
      media.removeEventListener("pause", onPause);
      media.removeEventListener("ended", onEnded);
    };
  }, [phase, report]);

  useEffect(() => {
    // Position is reported on a timer rather than on every frame: the server
    // wants to know roughly where the room is, not to mirror it exactly.
    if (phase !== "playing") return;
    const tick = window.setInterval(() => report("playing"), 5000);
    return () => window.clearInterval(tick);
  }, [phase, report]);

  // ── Screens ───────────────────────────────────────────────────────────────

  if (phase === "pairing" || (phase === "starting" && code)) {
    return (
      <div className="tv">
        <div className="pair">
          <div className="brand">Homesh</div>
          <p className="lede">Add this screen from your phone</p>
          <div className="code">{code ?? "······"}</div>
          <p className="hint">
            Open Homesh, choose <b>Add a device</b>, and enter this code
          </p>
        </div>
      </div>
    );
  }

  if (phase === "playing" && now) {
    const isVideo = now.kind === "video";
    return (
      <div className="tv">
        <div className="player">
          <div className="stage">
            {isVideo ? (
              <video ref={mediaRef} playsInline />
            ) : (
              <>
                <div className="art">♪</div>
                {/* Audio still needs a media element; it is simply not shown. */}
                <video ref={mediaRef} style={{ display: "none" }} playsInline />
              </>
            )}
          </div>
          <div className="meta">
            <div className="title">{now.filename ?? "Playing"}</div>
            {now.tags && <div className="tags">{now.tags}</div>}
            <div className="scrub">
              <span className="time">{formatTime(position)}</span>
              <div className="bar">
                <i style={{ width: duration ? `${(position / duration) * 100}%` : "0%" }} />
              </div>
              <span className="time right">{formatTime(duration)}</span>
            </div>
          </div>
        </div>
        {!connected && (
          <div className="offline"><span className="dot" /> Reconnecting…</div>
        )}
      </div>
    );
  }

  return (
    <div className="tv">
      <div className="idle">
        <div className="brand">Homesh</div>
        <div className="zone-name">{zoneName}</div>
        <p className="lede">Ready. Choose something on your phone and send it here.</p>
        <div className="badge">
          <span className={`dot${connected ? " live" : ""}`} />
          {connected ? "Connected" : "Connecting…"}
        </div>
      </div>
    </div>
  );
}
