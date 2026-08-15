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

export async function login(): Promise<void> {
  const begin = await api.post<BeginResponse>("/api/auth/login/begin", {});

  // No allowCredentials, so the authenticator presents whichever passkey it
  // holds for this site — the user never types a username.
  const credential = await startAuthentication({ optionsJSON: begin.options as never });

  await api.post("/api/auth/login/complete", {
    flow_id: begin.flow_id,
    credential,
    device_label: deviceLabel(),
  });
}

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
