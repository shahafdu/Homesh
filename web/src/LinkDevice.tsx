import { useEffect, useState } from "react";
import { ApiError } from "./api";
import { linkDevice, type DeviceLink } from "./auth";

/** Getting this account onto a phone.
 *
 * A passkey belongs to the device that made it, and a phone on the house network
 * cannot make one — WebAuthn is unavailable outside a secure context, which plain
 * http at a LAN address is not. So the phone is authorised from here instead,
 * by a code that only a signed-in session can produce.
 */
export default function LinkDevice(props: { onClose: () => void }) {
  const [link, setLink] = useState<DeviceLink | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [left, setLeft] = useState(0);

  useEffect(() => {
    linkDevice()
      .then((l) => {
        setLink(l);
        setLeft(l.expires_in);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (left <= 0) return;
    const t = window.setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => window.clearInterval(t);
  }, [left > 0]);

  const minutes = Math.floor(left / 60);
  const seconds = String(left % 60).padStart(2, "0");

  return (
    <div className="sheet" role="dialog" aria-modal="true" aria-label="Use on another device"
         onClick={props.onClose}>
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head"><h2>Use on another device</h2></div>

        {error && <div className="error">{error}</div>}
        {!link && !error && <p className="muted">Preparing…</p>}

        {link && (
          <>
            <p className="muted small">On your phone, open a browser and go to:</p>
            <div className="invite-link nm-clip">{link.address}</div>

            <p className="muted small">Choose <b>Use a code</b>, then enter:</p>
            <div className="code big">{link.code}</div>

            <p className="muted small">
              {left > 0
                ? `Expires in ${minutes}:${seconds}. It works once.`
                : "Expired — close and open this again for a new code."}
            </p>

            <p className="muted small">
              Your phone cannot create a passkey over a plain connection, so this
              signs it in instead. Anyone who reads the code within those minutes
              could use it once, so do not leave it on screen.
            </p>
          </>
        )}

        <button className="compact" style={{ marginTop: 12 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
