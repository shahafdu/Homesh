import { api } from "./api";

export interface Person {
  id: string;
  handle: string;
  display_name: string;
  is_admin: boolean;
  /** The account that set the server up. Cannot be demoted, restricted or removed. */
  is_owner: boolean;
  created_at: string;
  passkeys: number;
  library: string[];
  zones: { id: string; name: string }[];
  /** Whole-library and any-room access are stored facts, never inferred from an
   *  empty list — an empty list means empty. */
  all_library: boolean;
  all_zones: boolean;
}

export interface Invite {
  code: string;
  handle: string;
  display_name: string;
  expires_at: string;
}

export interface Grant {
  library: string[];
  zones: string[];
  all_library: boolean;
  all_zones: boolean;
}

export const listPeople = () => api.get<Person[]>("/api/people");

export const listInvites = () => api.get<Invite[]>("/api/people/invites");

export const createInvite = (body: Grant & { handle: string; display_name: string }) =>
  api.post<{ code: string; expires_in_days: number }>("/api/people/invites", body);

export const revokeInvite = (code: string) =>
  api.delete(`/api/people/invites/${encodeURIComponent(code)}`);

export const setRules = (userId: string, grant: Grant) =>
  api.put(`/api/people/${userId}/rules`, grant);

export const setAdmin = (userId: string, isAdmin: boolean) =>
  api.put(`/api/people/${userId}/admin`, { is_admin: isAdmin });

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
  if (person.is_owner) return "Everything — owner";
  if (person.is_admin) return "Everything — administrator";

  const folders = person.all_library
    ? "the whole library"
    : person.library.length === 0
      ? "no folders"
      : `${person.library.length} folder${person.library.length === 1 ? "" : "s"}`;

  const rooms = person.all_zones
    ? "any room"
    : person.zones.length === 0
      ? "no rooms"
      : person.zones.map((z) => z.name).join(", ");

  return `${folders} · ${rooms}`;
}

/** Who a folder or room is for.
 *
 * `null` means nobody has decided yet. Folders arrive by discovery — a Drive
 * folder shared with the service account simply appears — so this is a real
 * state, and it reads as admins-only until answered.
 */
export type Audience = "everyone" | "admins" | "selected" | null;

export interface Place {
  id: string;
  name: string;
  path?: string;
  audience: Audience;
  selected: { id: string; display_name: string }[];
}

export const listAudiences = () =>
  api.get<{ folders: Place[]; rooms: Place[] }>("/api/people/audiences");

export const setFolderAudience = (id: string, audience: Exclude<Audience, null>, users: string[]) =>
  api.put(`/api/people/audiences/folders/${id}`, { audience, users });

export const setRoomAudience = (id: string, audience: Exclude<Audience, null>, users: string[]) =>
  api.put(`/api/people/audiences/rooms/${id}`, { audience, users });

export function describeAudience(place: Place): string {
  switch (place.audience) {
    case "everyone":
      return "Everyone with an account";
    case "admins":
      return "Administrators only";
    case "selected":
      return place.selected.length === 0
        ? "Nobody yet — administrators only"
        : place.selected.map((p) => p.display_name).join(", ");
    default:
      return "Not decided yet — administrators only";
  }
}
