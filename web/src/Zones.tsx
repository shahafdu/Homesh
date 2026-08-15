import { useCallback, useEffect, useState } from "react";
import AudiencePicker, { type Choice } from "./Audience";
import { listPeople, type Person } from "./people";
import { ApiError } from "./api";
import {
  listZones,
  pairDevice,
  setZoneVolume,
  stopZone,
  zoneStatus,
  type Zone,
} from "./zones";

/** The control tower: every zone and what is playing where.
 *
 * Each zone is controllable from here without opening it first, which is only
 * possible because the server owns playback state rather than the phone
 * (ARCHITECTURE.md §5.8).
 */
export default function Zones(props: { onClose: () => void }) {
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
            onStop={() => act(() => stopZone(zone.id))}
            onVolume={(v) => act(() => setZoneVolume(zone.id, v))}
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

function ZoneCard(props: {
  zone: Zone;
  onStop: () => void;
  onVolume: (level: number) => void;
}) {
  const { zone } = props;
  const status = zoneStatus(zone);
  const live = zone.session?.state === "playing" || zone.session?.state === "paused";

  return (
    <div className={`zone-card${live ? " live" : ""}`}>
      <div className="zone-head">
        <span className={`dot ${status.tone}`} />
        <span className="zone-name">{zone.name}</span>
        <span className="zone-state">{status.label}</span>
      </div>

      {zone.renderer && (
        <div className="muted small">
          {zone.renderer.kind === "heos" ? "Receiver · audio only" : "Screen"}
        </div>
      )}

      {live && zone.session && (
        <>
          <div className="muted small">
            {zone.session.queue_length > 1
              ? `Track ${zone.session.cursor + 1} of ${zone.session.queue_length}`
              : "Playing"}
          </div>
          <div className="zone-controls">
            <button className="compact" onClick={props.onStop}>Stop</button>
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
        <div className="muted small">Open Homesh on that screen to use this zone.</div>
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
