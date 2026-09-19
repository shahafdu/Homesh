/** Passkey registration and login.
 *
 * @simplewebauthn/browser handles the base64url <-> ArrayBuffer marshalling that
 * the raw WebAuthn API demands; everything else is our own two-step flow.
 */

import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { api } from "./api";

interface BeginResponse {
  flow_id: string;
  options: Record<string, unknown>;
}

/** A label so you can tell your devices apart in the sessions list later. */
function deviceLabel(): string {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return "Android";
  if (/iPhone|iPad|iPod/i.test(ua)) return "iOS";
  if (/Windows/i.test(ua)) return "Windows";
  if (/Mac OS X/i.test(ua)) return "macOS";
  if (/Linux/i.test(ua)) return "Linux";
  return "Unknown device";
}

export function passkeysSupported(): boolean {
  return typeof window.PublicKeyCredential !== "undefined";
}

export async function register(
  handle: string,
  displayName: string,
  bootstrapCode: string | null,
  inviteCode: string | null = null,
): Promise<void> {
  const begin = await api.post<BeginResponse>("/api/auth/register/begin", {
    handle,
    display_name: displayName,
    bootstrap_code: bootstrapCode,
    invite_code: inviteCode,
  });

  const credential = await startRegistration({ optionsJSON: begin.options as never });

  await api.post("/api/auth/register/complete", {
    flow_id: begin.flow_id,
    credential,
    device_label: deviceLabel(),
  });
}

interface LoginBegin extends BeginResponse {
  /** Whether passkeys made for the PC's old name are still accepted here. */
  legacy_available: boolean;
}

interface LoginDone {
  ok: boolean;
  /** Signed in with an old passkey, which works on the PC only. */
  upgrade: boolean;
  credential: string | null;
}

// Which name this device's passkey belongs to, once known. Only steers the
// order of the attempts; it is never trusted for anything.
const RP_KEY = "homesh.passkey.name";
const UPGRADE_KEY = "homesh.passkey.upgrade";

function remember(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage refused -- the only cost is the order of the next attempt */
  }
}

function recall(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** Sign in with whichever passkey this device holds.
 *
 * Passkeys used to belong to the PC's full name and now belong to the tailnet's
 * domain, so that one works on the PC and the standby both. While devices move
 * across, the PC accepts either -- and the browser can only be asked for one
 * name at a time, so this asks for the likelier first. A device that has never
 * signed in under the new name almost certainly holds an old passkey.
 */
export async function login(): Promise<void> {
  const first = await api.post<LoginBegin>("/api/auth/login/begin", {});
  const order =
    first.legacy_available && recall(RP_KEY) !== "shared" ? [true, false] : [false];

  let failure: unknown = null;
  for (const legacy of order) {
    try {
      const begin = legacy
        ? await api.post<LoginBegin>("/api/auth/login/begin", { legacy: true })
        : first;
      // No allowCredentials, so the authenticator presents whichever passkey it
      // holds for this name — the user never types a username.
      const credential = await startAuthentication({ optionsJSON: begin.options as never });
      const done = await api.post<LoginDone>("/api/auth/login/complete", {
        flow_id: begin.flow_id,
        credential,
        device_label: deviceLabel(),
      });
      remember(RP_KEY, done.upgrade ? "old" : "shared");
      remember(UPGRADE_KEY, done.upgrade ? done.credential : null);
      return;
    } catch (e) {
      failure = e;
      // "No passkey for that name" and "cancelled" look the same from here:
      // NotAllowedError. Worth one more try under the other name; anything
      // else is a real failure and is reported as one.
      if (!(e instanceof Error && e.name === "NotAllowedError")) throw e;
    }
  }
  throw failure;
}

/** The old passkey this device signed in with, if it has not been swapped yet. */
export const pendingUpgrade = (): string | null => recall(UPGRADE_KEY);

/** Whether this device should be offered the swap.
 *
 * Known for certain when it signed in with an old passkey. Otherwise inferred:
 * the account still has a PC-only passkey and this device has never made one
 * under the shared name. Devices already signed in -- most of them, since a
 * session lasts -- would never see the offer if it waited for a sign-in.
 */
export async function shouldOfferUpgrade(): Promise<boolean> {
  if (pendingUpgrade() !== null) return true;
  if (recall(RP_KEY) === "shared" || recall(RP_KEY) === "declined") return false;
  try {
    return (await listPasskeys()).some((k) => k.pc_only);
  } catch {
    return false;
  }
}

/** "Not now": not asked again on this device until it next signs in with an
 *  old passkey, which asks afresh. */
export const dismissUpgrade = (): void => {
  remember(UPGRADE_KEY, null);
  if (recall(RP_KEY) !== "shared") remember(RP_KEY, "declined");
};

export async function logout(): Promise<void> {
  await api.post("/api/auth/logout");
}

export interface DeviceLink {
  code: string;
  expires_in: number;
  /** What to type on the other device. Configuration, sent by the server —
   *  never written down in this repository. */
  address: string;
}

/** Issue a code that signs this same account in on another device.
 *
 * The way onto a phone, where passkeys are unavailable: WebAuthn needs a secure
 * context, and plain http at a LAN address is not one.
 */
export const linkDevice = () => api.post<DeviceLink>("/api/auth/devices/link");

export const claimDeviceLink = (code: string) =>
  api.post<{ handle: string; display_name: string }>("/api/auth/devices/claim", {
    code,
    device_label: deviceLabel(),
  });

export interface Passkey {
  id: string;
  label: string | null;
  created_at: string;
  last_used_at: string | null;
  /** Made for the PC's old name: signs in there, not on the standby. */
  pc_only?: boolean;
}

export const listPasskeys = () => api.get<Passkey[]>("/api/auth/passkeys");

export const removePasskey = (id: string) => api.delete(`/api/auth/passkeys/${id}`);

/** Enrol another passkey for the account already signed in.
 *
 * A passkey belongs to the device that made it, so an account with one has one
 * device. It also belongs to the address it was made against — which is why
 * this has to exist before the server moves to a real hostname, or securing the
 * server would lock its owner out of it.
 */
export async function addPasskey(replaces: string | null = null): Promise<void> {
  const begin = await api.post<BeginResponse>("/api/auth/passkeys/begin");
  const credential = await startRegistration({ optionsJSON: begin.options as never });
  await api.post("/api/auth/passkeys/complete", {
    flow_id: begin.flow_id,
    credential,
    device_label: deviceLabel(),
    replaces,
  });
  // Made under the shared name, so this device signs in there first from now on.
  remember(RP_KEY, "shared");
  remember(UPGRADE_KEY, null);
}

/** Swap this device's old passkey for one that works on the standby too. */
export const upgradePasskey = (): Promise<void> => addPasskey(pendingUpgrade());
