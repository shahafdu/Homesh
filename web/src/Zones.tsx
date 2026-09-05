import { useCallback, useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import AudiencePicker, { type Choice } from "./Audience";
import { listPeople, type Person } from "./people";
import { ApiError, api } from "./api";
import { copyText } from "./copy";
import {
  listZones,
  pairDevice,
  setZoneVolume,
  nextInZone,
  pauseZone,
  previousInZone,
  removeZone,
  renameZone,
  jumpInZone,
  resumeZone,
  seekZone,
  shuffleZone,
  stopZone,
  zoneQueue,
  type QueueTrack,
  zoneStatus,
  type Zone,
} from "./zones";

/** The control tower: every zone and what is playing where.
 *
 * Each zone is controllable from here without opening it first, which is only
 * possible because the server owns playback state rather than the phone
 * (ARCHITECTURE.md §5.8).
 */

/** The address to type into a television, which is not the one in your browser.
 *
 * A phone reaching this server over Tailscale sees a ts.net name. A set-top box
 * is not on the tailnet and cannot resolve it — the address shown here was one
 * the television reported as ERR_NAME_NOT_RESOLVED, which looked like a broken
 * download and was actually a wrong address. So the server is asked for the
 * house address instead of assuming this browser's.
 */
function TvAppAddress() {
  const [lan, setLan] = useState<string | null>(null);
  const [short, setShort] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api
      .get<{ lan: string | null; short: string | null; detail: string | null }>("/tv.address")
      .then((r) => {
        setLan(r.lan);
        setShort(r.short);
        setDetail(r.detail);
      })
      .catch(() => setDetail("Could not ask the server for its address."));
  }, []);

  const here = window.location.origin;
  // The shortest address that answers. Every character is one more press on a
  // d-pad keyboard, and a long unfamiliar string is what makes a television
  // browser decide the whole thing was a search query instead of an address.
  const url = short ? `${short}/apk` : `${lan ?? here}/tv.apk`;
  // Only worth pointing out when the two differ — otherwise it is noise.
  const differs = lan !== null && !here.startsWith(lan);

  return (
    <>
      {/* Scrolls rather than truncates: an address that ends in an ellipsis
          cannot be typed, and this one has to be typed by hand on a remote
          control with no keyboard. */}
      <div className="invite-link scroll-x">{url}</div>
      <div className="zone-controls">
        <button
          className="compact"
          onClick={async () => {
            setCopied(await copyText(url));
            window.setTimeout(() => setCopied(false), 2500);
          }}
        >
          {copied ? "Copied" : "Copy address"}
        </button>
      </div>
      <p className="muted small">
        <b>Use Downloader</b> if the box has it — paste the address and press Go.
        In a normal browser, type the address and choose <b>Go</b> rather than
        the search suggestion: a TV browser will happily search Google for an
        address instead of opening it, and you get a results page rather than
        the app.
      </p>
      {differs && (
        <p className="muted small">
          This is the address on your home network. It is not the one in your
          browser’s bar, because a television is not on your Tailscale network
          and cannot reach that one.
        </p>
      )}
      {detail && <p className="muted small">{detail} Showing this browser’s address instead.</p>}
    </>
  );
}

export default function Zones(props: { onClose: () => void }) {
  useLockScroll();
  const [zones, setZones] = useState<Zone[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setZones(await listZones());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Zones change from outside this app — a screen switching on, a track ending
    // — so the tower polls rather than assuming it caused every change.
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
    await refresh();
  };

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Zones" onClick={props.onClose}>
      <div className="sheet-inner wide" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Zones</h2>
          <button className="compact" onClick={() => setAdding(true)}>＋ Add a device</button>
        </div>

        {error && <div className="error">{error}</div>}

        {zones === null && <p className="muted">Loading…</p>}
        {zones?.length === 0 && (
          <p className="muted">
            No zones yet. Add a device to send music and video to another room.
          </p>
        )}

        {zones?.map((zone) => (
          <ZoneCard
            key={zone.id}
            zone={zone}
            onChanged={() => void refresh()}
            onSeek={(ms) => act(() => seekZone(zone.id, ms))}
            onStop={() => act(() => stopZone(zone.id))}
            onVolume={(v) => act(() => setZoneVolume(zone.id, v))}
            onToggle={() =>
              act(() =>
                zone.session?.state === "playing" ? pauseZone(zone.id) : resumeZone(zone.id),
              )
            }
            onNext={() => act(() => nextInZone(zone.id))}
            onPrevious={() => act(() => previousInZone(zone.id))}
            onRename={(name) => act(() => renameZone(zone.id, name))}
            onRemove={() => act(() => removeZone(zone.id))}
          />
        ))}

        {adding && (
          <AddDevice
            onDone={async () => {
              setAdding(false);
              await refresh();
            }}
            onCancel={() => setAdding(false)}
          />
        )}

        <button className="compact" style={{ marginTop: 16 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** Where a room is in what it is playing, and a way to move it.
 *
 * The control the tower was missing. Without it the only way past a slow
 * passage on the bedroom screen was to walk to the bedroom — and an hour-long
 * video is exactly the case where somebody is not in that room.
 */
function ZoneSeek(props: { zone: Zone; onSeek: (positionMs: number) => void }) {
  const session = props.zone.session;
  // What the screen reports first, the catalog second. Music has a length in
  // the catalog; a video being transcoded as it plays does not, and only the
  // thing decoding it knows — which is why the bar appeared for songs and
  // never for films.
  const duration = session?.duration_ms ?? session?.now?.duration_ms ?? 0;

  // While dragging, the bar follows the finger rather than the four-second
  // poll — which would otherwise yank it back to where the room still is.
  const [dragging, setDragging] = useState<number | null>(null);
  const position = dragging ?? session?.position_ms ?? 0;

  // A receiver plays a stream it is being fed and cannot be moved through, so
  // there is nothing honest to offer. Nor is there for anything with no known
  // length: a bar with no end is not a bar.
  if (!session || duration <= 0 || props.zone.renderer?.kind !== "tvapp") return null;

  return (
    <div className="zone-seek">
      <span className="time">{formatClock(position)}</span>
      <input
        type="range"
        min={0}
        max={duration}
        step={1000}
        value={Math.min(position, duration)}
        aria-label={`Position in ${props.zone.name}`}
        onChange={(e) => setDragging(Number(e.target.value))}
        // Sent on release, not on every pixel: dragging across an hour would
        // otherwise be a hundred requests and a hundred restarts of the encoder.
        onPointerUp={() => {
          if (dragging !== null) props.onSeek(dragging);
          setDragging(null);
        }}
        onKeyUp={() => {
          if (dragging !== null) props.onSeek(dragging);
          setDragging(null);
        }}
        style={{ ["--pct" as string]: `${(position / duration) * 100}%` }}
      />
      <span className="time">{formatClock(duration)}</span>
    </div>
  );
}

/** mm:ss, or h:mm:ss once it earns the hour. */
function formatClock(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}

/** What a room is going to play, and picking something else from it.
 *
 * A playlist sent to a room used to be a black box: the tower could say "4 of
 * 31" but not what the other thirty were, so choosing a different song meant
 * sending the whole list again from the top.
 *
 * Collapsed by default — thirty-one rows under every card is a wall — and
 * fetched only when opened, since it is one query per room per poll otherwise.
 */
function ZoneQueue(props: { zone: Zone; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [tracks, setTracks] = useState<QueueTrack[] | null>(null);
  const [busy, setBusy] = useState(false);
  const { zone } = props;
  const cursor = zone.session?.cursor ?? 0;
  const total = zone.session?.queue_length ?? 0;

  const load = useCallback(async () => {
    try {
      setTracks((await zoneQueue(zone.id)).tracks);
    } catch {
      setTracks([]);
    }
  }, [zone.id]);

  useEffect(() => {
    if (open) void load();
    // Reloaded when the room moves on, so the highlight follows the music
    // rather than sitting on whatever was playing when this was opened.
  }, [open, load, cursor]);

  if (total < 1) return null;

  return (
    <div className="zone-queue">
      <div className="zone-controls">
        <button className="compact" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"} Playing next
          <span className="muted small"> · {total}</span>
        </button>
        {total > 1 && (
          <button
            className={`compact${zone.session?.shuffle ? " primary" : ""}`}
            disabled={busy}
            aria-pressed={zone.session?.shuffle ?? false}
            title={
              zone.session?.shuffle
                ? "Shuffle is on — tap to play in order"
                : "Play what is left in a random order"
            }
            onClick={async () => {
              setBusy(true);
              try {
                await shuffleZone(zone.id, !zone.session?.shuffle);
                // Both, because the button's own state lives on the zone and
                // the order it just changed lives in the queue.
                props.onChanged();
                await load();
              } finally {
                setBusy(false);
              }
            }}
          >
            ⤨ Shuffle{zone.session?.shuffle ? " on" : ""}
          </button>
        )}
      </div>

      {open && tracks === null && <p className="muted small">Loading…</p>}

      {open && tracks && (
        <ol className="q-list">
          {tracks.map((t) => (
            <li key={`${t.index}-${t.item_id}`} className={t.index === cursor ? "current" : ""}>
              <button
                className="q-row"
                disabled={busy}
                onClick={async () => {
                  if (t.index === cursor) return;
                  setBusy(true);
                  try {
                    await jumpInZone(zone.id, t.index);
                    props.onChanged();
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {/* The playing one is marked rather than merely highlighted:
                    colour alone is not something everyone can see. */}
                <span className="q-pos">{t.index === cursor ? "▶" : t.index + 1}</span>
                <span className="q-name nm-clip">{t.title ?? t.filename ?? "—"}</span>
                {t.artist && <span className="muted small q-artist">{t.artist}</span>}
              </button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function ZoneCard(props: {
  zone: Zone;
  onChanged: () => void;
  onSeek: (positionMs: number) => void;
  onStop: () => void;
  onVolume: (level: number) => void;
  onToggle: () => void;
  onNext: () => void;
  onPrevious: () => void;
  onRename: (name: string) => void;
  onRemove: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  const { zone } = props;
  const status = zoneStatus(zone);
  const live = zone.session?.state === "playing" || zone.session?.state === "paused";

  return (
    <div className={`zone-card${live ? " live" : ""}`}>
      <div className="zone-head">
        <span className={`dot ${status.tone}`} />
        {editing ? (
          <input
            className="zone-rename"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && draft.trim()) {
                props.onRename(draft.trim());
                setEditing(false);
              }
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <span className="zone-name">{zone.name}</span>
        )}
        <span className="zone-state">{status.label}</span>
        <button
          className="iconbtn tiny"
          title={`Rename or remove ${zone.name}`}
          aria-label="Room settings"
          onClick={() => {
            setDraft(zone.name);
            setEditing((e) => !e);
            setConfirming(false);
          }}
        >
          ⚙
        </button>
      </div>

      {editing && (
        <div className="zone-controls">
          <button
            className="compact primary"
            disabled={!draft.trim() || draft.trim() === zone.name}
            onClick={() => {
              props.onRename(draft.trim());
              setEditing(false);
            }}
          >
            Rename
          </button>
          {confirming ? (
            <>
              {/* Removing takes the paired screen with it, which is not obvious
                  from the word "remove" — so it is said before it happens. */}
              <span className="muted small">
                Remove {zone.name} and unpair its screen?
              </span>
              <button className="compact" onClick={props.onRemove}>Yes, remove</button>
              <button className="compact" onClick={() => setConfirming(false)}>Cancel</button>
            </>
          ) : (
            <button className="compact" onClick={() => setConfirming(true)}>Remove…</button>
          )}
        </div>
      )}

      {zone.renderer && (
        <div className="muted small">
          {zone.renderer.kind === "heos" ? "Receiver · audio only" : "Screen"}
        </div>
      )}

      {live && zone.session && (
        <>
          {/* The name first, the position second. Standing in the kitchen you
              want to know what is on, not that it is the second of five. */}
          <div className="now-playing">
            <span className="now-title">
              {zone.session.now?.title ?? zone.session.now?.filename ?? "Playing"}
            </span>
            {zone.session.now?.artist && (
              <span className="now-artist">{zone.session.now.artist}</span>
            )}
          </div>
          {zone.session.queue_length > 1 && (
            <div className="muted small">
              Track {zone.session.cursor + 1} of {zone.session.queue_length}
            </div>
          )}

          <ZoneSeek zone={zone} onSeek={props.onSeek} />
          <div className="zone-controls">
            {/* The same four controls whatever is in the room. A phone should not
                have to know whether it is driving a television or a receiver. */}
            <button
              className="compact"
              onClick={props.onPrevious}
              aria-label={`Previous track in ${zone.name}`}
              title="Previous"
            >
              ⏮
            </button>
            <button
              className="compact"
              onClick={props.onToggle}
              aria-label={
                zone.session?.state === "playing"
                  ? `Pause ${zone.name}`
                  : `Resume ${zone.name}`
              }
              title={zone.session?.state === "playing" ? "Pause" : "Play"}
            >
              {zone.session?.state === "playing" ? "⏸" : "▶"}
            </button>
            <button
              className="compact"
              onClick={props.onNext}
              aria-label={`Next track in ${zone.name}`}
              title="Next"
            >
              ⏭
            </button>
            <button className="compact" onClick={props.onStop}>Stop</button>
          </div>

          <ZoneQueue zone={zone} onChanged={props.onChanged} />

          {/* Its own row beneath the buttons. Six controls on one line pushed the
              slider past the edge of the card, and the slider is the one that
              needs the width. */}
          <div className="zone-volume">
            <span className="vol-ic" aria-hidden="true">🔈</span>
            <input
              className="zone-vol"
              type="range"
              min={0}
              max={100}
              value={zone.session.volume ?? 40}
              onChange={(e) => props.onVolume(Number(e.target.value))}
              aria-label={`Volume in ${zone.name}`}
            />
            <span className="muted small vol-n">{zone.session.volume ?? "–"}</span>
          </div>
        </>
      )}

      {!live && zone.renderer?.state === "unavailable" && zone.renderer.kind === "tvapp" && (
        <div className="muted small">
          Not connected. Open Homesh on that screen — or remove the room if the
          app is gone from it.
        </div>
      )}
    </div>
  );
}

function AddDevice(props: { onDone: () => void; onCancel: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Administrators only until told otherwise. A room is reachable the instant it
  // pairs, so the safe answer has to be the one already selected.
  const [audience, setAudience] = useState<Choice>("admins");
  const [grantTo, setGrantTo] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);

  useEffect(() => {
    listPeople().then(setPeople).catch(() => setPeople([]));
  }, []);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await pairDevice(code.trim(), name.trim(), audience, grantTo);
      props.onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="zone-card">
      <div className="zone-head"><span className="zone-name">Add a device</span></div>
      <p className="muted small" style={{ margin: 0 }}>
        Open Homesh on the screen. It shows a six-character code.
      </p>

      {/* A set-top box has no easy way to receive a file, but it can fetch a URL.
          Shown here because this is where somebody stands when they discover the
          screen has no app on it yet. */}
      <details className="install">
        <summary className="muted small">Nothing installed on that screen yet?</summary>
        <p className="muted small">
          On the box, open <b>Downloader</b> (or any browser) and go to:
        </p>
        <TvAppAddress />
        <p className="muted small">
          Allow it to install unknown apps when asked. Then open Homesh from the
          launcher — it will ask where this server is, and show a pairing code.
        </p>
      </details>

      <label htmlFor="pair-code">Code from the screen</label>
      <input
        id="pair-code"
        value={code}
        autoCapitalize="characters"
        autoCorrect="off"
        spellCheck={false}
        placeholder="K7 4Q 2M"
        onChange={(e) => setCode(e.target.value.toUpperCase())}
      />

      <label htmlFor="pair-name">Name this room</label>
      <input
        id="pair-name"
        value={name}
        placeholder="Bedroom"
        onChange={(e) => setName(e.target.value)}
      />

      <AudiencePicker
        value={audience}
        users={grantTo}
        people={people}
        onChange={(v, u) => {
          setAudience(v);
          setGrantTo(u);
        }}
      />

      {error && <div className="error">{error}</div>}

      <div className="zone-controls" style={{ marginTop: 12 }}>
        <button
          className="compact primary"
          disabled={busy || code.trim().length < 6 || !name.trim()}
          onClick={submit}
        >
          {busy ? "Pairing…" : "Add"}
        </button>
        <button className="compact" onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}
