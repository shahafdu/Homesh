import { useCallback, useEffect, useRef, useState } from "react";
import { randomId } from "../id";

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

/** The native player, when this screen is the Android shell rather than a browser.
 *
 * A WebView carries roughly H.264 and VP8/9; the box underneath it decodes
 * MPEG-2, MKV and AVI in hardware. Handing video over is the difference between
 * a wedding tape playing and a player sitting at 0:00.
 */
interface NativeVideo {
  available(): boolean;
  play(url: string, positionMs: number): void;
  pause(): void;
  resume(): void;
  stop(): void;
  positionMs(): number;
  durationMs(): number;
  isPlaying(): boolean;
}

declare global {
  interface Window {
    HomeshVideo?: NativeVideo;
    homeshVideoEnded?: () => void;
    homeshVideoFailed?: (code: number) => void;
  }
}

const native = (): NativeVideo | null =>
  typeof window.HomeshVideo?.available === "function" && window.HomeshVideo.available()
    ? window.HomeshVideo
    : null;

/** Stable per-installation identity, so re-pairing updates this screen rather
 *  than creating a second one. Survives reloads; regenerated only on reinstall. */
function deviceKey(): string {
  const existing = localStorage.getItem("homesh.tv.device");
  if (existing) return existing;
  const fresh = `tv-${randomId()}`;
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
  // The server is unreachable. Fatal to everything, so it takes over the screen.
  const [fault, setFault] = useState<string | null>(null);
  // This one file would not play. Not fatal at all — and conflating the two is
  // why a bad file put "Cannot reach the server" on the television and left it
  // there, unable to accept anything else until the app was force-closed.
  const [playFault, setPlayFault] = useState<string | null>(null);
  const [zoneName, setZoneName] = useState("This screen");
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState<Command | null>(null);
  // What is playing, readable from the command handler.
  //
  // `handle` is deliberately built once — it is handed to the socket, and
  // rebuilding it would mean reconnecting whenever a track changed — so it
  // closes over the *first* render's state. Reading `now` from there gave null
  // forever, which is why resuming a video went to the media element instead of
  // the box's own decoder. A ref is current whenever it is read.
  const nowRef = useRef<Command | null>(null);
  useEffect(() => {
    nowRef.current = now;
  }, [now]);
  const [position, setPosition] = useState(0);

  // ── Showing that the remote was heard ─────────────────────────────────────
  //
  // A television has no cursor and no window chrome. If nothing on screen
  // changes, a press is indistinguishable from a flat battery — which is what
  // made these controls feel unresponsive: they worked, silently, and the
  // evidence arrived a second or two later when the stream caught up.

  /** Where a seek asked to land, shown until playback actually reports it. */
  const [seekTo, setSeekTo] = useState<number | null>(null);
  /** A short confirmation of the last press: "▸▸ 12:30", "❚❚ Paused". */
  const [gesture, setGesture] = useState<string | null>(null);
  /** Whether the title and bar are on screen. They fade out while watching. */
  const [overlayShown, setOverlayShown] = useState(true);

  const wake = useCallback(() => {
    setOverlayShown(true);
    // Deliberately not a dependency-tracked timer: every wake replaces the
    // previous deadline, which is what "a few seconds after the last press"
    // means.
    window.clearTimeout(hideAt.current);
    hideAt.current = window.setTimeout(() => setOverlayShown(false), 4000);
  }, []);
  const hideAt = useRef<number | undefined>(undefined);

  // The bar follows the request until reality catches up with it, then follows
  // reality. Without the first half a seek looks like nothing happened.
  const shownPosition = seekTo ?? position;
  useEffect(() => {
    if (seekTo === null) return;
    // Landed near enough, or the stream moved on by itself.
    if (Math.abs(position - seekTo) < 2) setSeekTo(null);
  }, [position, seekTo]);

  // The confirmation is a flash, not a status line.
  useEffect(() => {
    if (!gesture) return;
    const clear = window.setTimeout(() => setGesture(null), 1600);
    return () => window.clearTimeout(clear);
  }, [gesture]);

  // Playing something new starts the overlay visible and lets it fade.
  useEffect(() => {
    wake();
    setSeekTo(null);
  }, [now?.item_id, wake]);
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
    let poll_token: string;
    try {
      const res = await fetch("/api/renderers/pair/begin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_key: deviceKey(), device_name: "TV" }),
      });
      if (!res.ok) throw new Error(`pairing refused (${res.status})`);
      const body = await res.json();
      setCode(body.code);
      poll_token = body.poll_token;
    } catch (e) {
      // Say so on the screen. Falling through to the idle view would leave a
      // television claiming to be ready while it had never reached the server,
      // which is exactly how a startup failure went unnoticed before.
      setFault(e instanceof Error ? e.message : String(e));
      window.setTimeout(() => void startPairing(), 5000);
      return;
    }

    setFault(null);
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
        setPlayFault(null);
        setNow(cmd);
        setPhase("playing");
        // A photo has nothing to start: it is an <img>, and there is no media
        // element to hand a source to.
        if (cmd.kind === "photo") break;

        // Video goes to the box's own decoder where there is one. The web app
        // keeps the queue and the reporting either way — only the pixels move.
        if (cmd.kind === "video" && native()) {
          native()!.play(cmd.url ?? "", cmd.position_ms ?? 0);
          break;
        }
        // The element mounts with this render, so defer until it exists.
        window.setTimeout(() => {
          const el = mediaRef.current;
          if (!el || !cmd.url) return;
          el.src = cmd.url;
          if (cmd.position_ms) el.currentTime = cmd.position_ms / 1000;
          void el.play().catch((e) => {
            const why =
              e instanceof Error && e.name === "NotSupportedError"
                ? "this screen cannot decode that format"
                : String(e);
            setPlayFault(`${cmd.filename ?? "That file"} — ${why}`);
            // Back to a state that can be given something else. A screen stuck
            // on an error is a screen somebody has to walk over to.
            setPhase("idle");
            setNow(null);
          });
        }, 0);
        break;
      case "pause":
        if (native()?.isPlaying()) native()!.pause();
        media?.pause();
        break;
      case "resume":
        if (native() && nowRef.current?.kind === "video") native()!.resume();
        else void media?.play().catch(() => undefined);
        break;
      case "stop":
        setPlayFault(null);
        native()?.stop();
        media?.pause();
        setNow(null);
        setPhase("idle");
        break;
      case "seek":
        if (cmd.position_ms == null) break;
        // The native player has no seek of its own — it is restarted at the new
        // offset, which is also how the transcoded stream is moved through,
        // since a stream still being encoded has no index to seek in. Without
        // this branch the tower's bar moved and the television ignored it.
        if (native() && nowRef.current?.kind === "video") {
          native()!.play(nowRef.current.url ?? "", cmd.position_ms);
        } else if (media) {
          media.currentTime = cmd.position_ms / 1000;
        }
        setSeekTo(cmd.position_ms / 1000);
        setGesture(`▸ ${formatTime(cmd.position_ms / 1000)}`);
        wake();
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

  // The remote, on the screen itself.
  //
  // Everything was driven from a phone, which is right until the phone is in
  // another room, or the person watching is not the person who started it. These
  // are the keys a television remote actually sends.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const media = mediaRef.current;
      const player = native();

      // Any key wakes the overlay, including one this does not otherwise
      // handle: on a television the only evidence a press arrived is the screen
      // changing, and a remote with no visible effect reads as a dead remote.
      wake();

      const seekBy = (seconds: number) => {
        const from = player && now?.kind === "video"
          ? player.positionMs() / 1000
          : (media?.currentTime ?? 0);
        const to = Math.max(0, from + seconds);

        // Shown immediately, before anything has loaded. A jump in a
        // transcoded stream restarts the encoder, which takes a second or two —
        // and with the bar frozen at the old position the whole time, there was
        // nothing to tell you the press had registered or where you had landed.
        setSeekTo(to);
        setGesture(`${seconds > 0 ? "▸▸" : "◂◂"} ${formatTime(to)}`);

        if (player && now?.kind === "video") player.play(now.url ?? "", to * 1000);
        else if (media) media.currentTime = to;
      };

      switch (event.key) {
        case "ArrowRight":
          seekBy(30);
          break;
        case "ArrowLeft":
          seekBy(-15);
          break;
        case "MediaPlayPause":
        case "Enter":
        case " ": {
          const playing = player?.isPlaying() ?? !media?.paused;
          setGesture(playing ? "❚❚ Paused" : "▶ Playing");
          report(playing ? "paused" : "playing");
          if (player && now?.kind === "video") {
            playing ? player.pause() : player.resume();
          } else if (media) {
            playing ? media.pause() : void media.play().catch(() => undefined);
          }
          break;
        }
        case "MediaStop":
          setGesture("■ Stopped");
          handle({ type: "stop" });
          break;
        default:
          return;   // wake() already ran; nothing else to do
      }
      event.preventDefault();
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [now, handle, report, wake]);


  useEffect(() => {
    // The native player is outside React and outside the media element, so it
    // reports through these two hooks. Without the first, the server never
    // learns a film ended and the queue stops after one.
    window.homeshVideoEnded = () => report("ended");
    window.homeshVideoFailed = (code: number) => {
      setPlayFault(`The screen could not play that file (error ${code}).`);
      setPhase("idle");
      setNow(null);
    };
    return () => {
      window.homeshVideoEnded = undefined;
      window.homeshVideoFailed = undefined;
    };
  }, [report]);

  useEffect(() => {
    // Position while the box is playing: the media element knows nothing about
    // it, so it is polled and reported like any other progress.
    if (phase !== "playing" || now?.kind !== "video" || !native()) return;
    const timer = window.setInterval(() => {
      const player = native();
      if (!player) return;
      setPosition(player.positionMs());
      setDuration(player.durationMs());
      report(player.isPlaying() ? "playing" : "paused");
    }, 2000);
    return () => window.clearInterval(timer);
  }, [phase, now?.kind, report]);

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

  // Before anything else: a screen that could not reach the server says so.
  // The idle view below is the fallback for every remaining phase, so without
  // this a failed start renders as "Ready" — which is what hid one.
  if (fault) {
    return (
      <div className="tv">
        <div className="idle">
          <div className="brand">Homesh</div>
          <p className="lede">Cannot reach the server.</p>
          <p className="hint">{fault}</p>
          <div className="badge"><span className="dot" /> Retrying…</div>
        </div>
      </div>
    );
  }

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
    // Three kinds arrive here and each needs a different element. A photo sent
    // to a video tag renders nothing at all: the screen showed a player with a
    // scrub bar frozen at 0:00 and no picture, which looks like a broken film
    // rather than a photograph nobody displayed.
    const isVideo = now.kind === "video";
    const isPhoto = now.kind === "photo";

    return (
      <div className="tv">
        <div className={`player${isPhoto ? " photo" : ""}`}>
          <div className="stage">
            {isVideo && <video ref={mediaRef} playsInline />}

            {isPhoto && now.url && (
              <img className="tv-photo" src={now.url} alt={now.filename ?? ""} />
            )}

            {!isVideo && !isPhoto && (
              <>
                <div className="art">♪</div>
                {/* Audio still needs a media element; it is simply not shown. */}
                <video ref={mediaRef} style={{ display: "none" }} playsInline />
              </>
            )}
          </div>
          {/* Over the picture, not beside it. It used to take a strip off the
              bottom of every frame, so a 16:9 film was letterboxed into what
              was left — on the one screen in the house where the whole point is
              that it fills the wall. It fades out after a few seconds and any
              remote key brings it back. */}
          <div className={`meta${overlayShown ? "" : " hidden"}`}>
            <div className="title">{now.filename ?? "Playing"}</div>
            {now.tags && <div className="tags">{now.tags}</div>}
            {!isPhoto && (
              <div className="scrub">
                <span className="time">{formatTime(shownPosition)}</span>
                <div className="bar">
                  <i
                    style={{
                      width: duration ? `${(shownPosition / duration) * 100}%` : "0%",
                    }}
                  />
                </div>
                <span className="time right">{formatTime(duration)}</span>
              </div>
            )}
          </div>

          {/* Said plainly and large, because on a television there is no cursor
              to hover and no window title to check: the only way to know a
              press registered is to see the screen change. */}
          {gesture && <div className="gesture">{gesture}</div>}
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
        {playFault ? (
          <p className="lede warn">{playFault}</p>
        ) : (
          <p className="lede">Ready. Choose something on your phone and send it here.</p>
        )}
        <div className="badge">
          <span className={`dot${connected ? " live" : ""}`} />
          {connected ? "Connected" : "Connecting…"}
        </div>
      </div>
    </div>
  );
}
