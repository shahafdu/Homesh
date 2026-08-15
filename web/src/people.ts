import { api } from "./api";

export interface Person {
  id: string;
  handle: string;
  display_name: string;
  is_admin: boolean;
  created_at: string;
  passkeys: number;
  /** null means unrestricted — deliberately not an empty array, which would mean
   *  the opposite. */
  library: string[] | null;
  zones: { id: string; name: string }[] | null;
}

export interface Invite {
  code: string;
  handle: string;
  display_name: string;
  expires_at: string;
}

export const listPeople = () => api.get<Person[]>("/api/people");

export const listInvites = () => api.get<Invite[]>("/api/people/invites");

export const createInvite = (body: {
  handle: string;
  display_name: string;
  library: string[];
  zones: string[];
}) => api.post<{ code: string; expires_in_days: number }>("/api/people/invites", body);

export const revokeInvite = (code: string) =>
  api.delete(`/api/people/invites/${encodeURIComponent(code)}`);

export const setRules = (userId: string, rules: { library?: string[]; zones?: string[] }) =>
  api.put(`/api/people/${userId}/rules`, rules);

export const removePerson = (userId: string) => api.delete(`/api/people/${userId}`);

/** The link an invited person opens on their own device.
 *
 * Their device, not yours: a passkey belongs to the authenticator that made it,
 * so registering someone else from your phone would enrol your fingerprint.
 */
export function inviteLink(code: string): string {
  return `${window.location.origin}/?invite=${encodeURIComponent(code)}`;
}

export function describeAccess(person: Person): string {
  if (person.is_admin) return "Everything — administrator";
  const parts: string[] = [];
  parts.push(
    person.library === null
      ? "the whole library"
      : `${person.library.length} folder${person.library.length === 1 ? "" : "s"}`,
  );
  parts.push(
    person.zones === null
      ? "any room"
      : person.zones.length === 0
        ? "no rooms"
        : person.zones.map((z) => z.name).join(", "),
  );
  return parts.join(" · ");
}
