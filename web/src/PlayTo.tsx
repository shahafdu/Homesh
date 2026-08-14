import { useEffect, useState } from "react";
import { ApiError } from "./api";
import type { FileEntry } from "./library";
import { listZones, playInZone, zoneAccepts, zoneStatus, type Zone } from "./zones";

/** Asks *where* something should play, rather than assuming the phone.
 *
 * Zones that cannot take this kind are shown but disabled with the reason. The
 * receiver carries no picture, so offering it a film would invite a failure we
 * already know about.
 */
export default function PlayTo(props: {
  file: FileEntry;
  siblings: FileEntry[];
  onHere: () => void;
  onClose: () => void;
}) {
  const { file, siblings } = props;
  const [zones, setZones] = useState<Zone[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listZones().then(setZones).catch(() => setZones([]));
  }, []);

  const send = async (zone: Zone) => {
    setBusy(zone.id);
    setError(null);
    try {
      // Send the whole folder so a zone behaves like an album, matching what
      // playing here does.
      const queue = siblings.filter((f) => f.kind === file.kind && f.available);
      const index = Math.max(0, queue.findIndex((f) => f.item_id === file.item_id));
      await playInZone(zone.id, queue.map((f) => f.item_id), index);
      props.onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Play to" onClick={props.onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <h2>Play to…</h2>
        <p className="muted small nm-clip">{file.filename}</p>

        {error && <div className="error">{error}</div>}

        <button className="pick" onClick={props.onHere}>
          <span className="pick-ic">▤</span>
          <span className="pick-text"><b>This device</b><span>Play here</span></span>
        </button>

        {zones === null && <p className="muted">Looking for zones…</p>}

        {zones?.map((zone) => {
          const ok = zoneAccepts(zone, file.kind);
          const status = zoneStatus(zone);
          const reason = !zone.renderer
            ? "no device"
            : !ok
              ? `audio only — cannot play ${file.kind}`
              : status.label;

          return (
            <button
              key={zone.id}
              className="pick"
              disabled={!ok || busy !== null}
              onClick={() => send(zone)}
            >
              <span className="pick-ic">{zone.renderer?.kind === "heos" ? "◉" : "▣"}</span>
              <span className="pick-text">
                <b>{zone.name}</b>
                <span>{busy === zone.id ? "sending…" : reason}</span>
              </span>
            </button>
          );
        })}

        {zones?.length === 0 && (
          <p className="muted small">
            No zones yet. Add a device from the zones screen to send media to another room.
          </p>
        )}

        <button className="compact" style={{ marginTop: 14 }} onClick={props.onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
