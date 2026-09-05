import { useEffect, useState } from "react";
import { listZones, type Zone } from "./zones";

/** How many rooms are in use, refreshed quietly in the background.
 *
 * The tower answers this properly, but only once it is open. The point of the
 * status line is that the answer is visible without opening anything — the
 * question "is something still playing in the balcony?" should not require
 * navigating to find out.
 */
export function useRoomActivity(pollMs = 8000) {
  const [zones, setZones] = useState<Zone[]>([]);

  useEffect(() => {
    let live = true;
    const read = () => {
      listZones()
        .then((z) => live && setZones(z))
        .catch(() => undefined);
    };
    read();
    const timer = window.setInterval(read, pollMs);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [pollMs]);

  // Rooms in use by something that is not this app count too: a receiver playing
  // Spotify is occupied, whoever started it.
  const playing = zones.filter(
    (z) => z.session?.state === "playing" || z.external?.busy,
  );

  return { zones, playing };
}
