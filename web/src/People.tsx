import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { AudienceEditor, type Choice } from "./Audience";
import { copyText } from "./copy";
import { browse, type DirEntry } from "./library";
import {
  createInvite,
  describeAccess,
  describeAudience,
  inviteLink,
  listAudiences,
  listInvites,
  listPeople,
  removePerson,
  revokeInvite,
  setAdmin,
  setFolderAudience,
  setRoomAudience,
  setRules,
  type Grant,
  type Invite,
  type Person,
  type Place,
} from "./people";
import { listZones, type Zone } from "./zones";

const EMPTY: Grant = { library: [], zones: [], all_library: false, all_zones: false };

/** Who has an account, and what they can reach.
 *
 * Access is granted, never assumed: a new account reaches nothing until someone
 * says otherwise. Administration can be shared, but the owner is fixed — so
 * handing your partner the ability to manage the house is not a way to lose it.
 */
export default function People(props: { onClose: () => void }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [roots, setRoots] = useState<DirEntry[]>([]);
  const [folders, setFolders] = useState<Place[]>([]);
  const [rooms, setRooms] = useState<Place[]>([]);
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Person | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [p, i, z, r, a] = await Promise.all([
        listPeople(),
        listInvites(),
        listZones(),
        browse("/"),
        listAudiences(),
      ]);
      setPeople(p);
      setInvites(i);
      setZones(z);
      setRoots(r.dirs);
      setFolders(a.folders);
      setRooms(a.rooms);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
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
    <div className="sheet" role="dialog" aria-modal="true" aria-label="People" onClick={props.onClose}>
      <div className="sheet-inner wide" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>People</h2>
          <button className="compact" onClick={() => setInviting(true)}>＋ Invite</button>
        </div>

        {error && <div className="error">{error}</div>}
        {people === null && <p className="muted">Loading…</p>}

        {people?.map((person) => (
          <div key={person.id} className="zone-card">
            <div className="zone-head">
              <span className="zone-name">{person.display_name}</span>
              {person.is_owner && <span className="badge">owner</span>}
              {person.is_admin && !person.is_owner && <span className="badge">admin</span>}
            </div>
            <div className="muted small">{describeAccess(person)}</div>
            <div className="muted small">
              {person.passkeys} passkey{person.passkeys === 1 ? "" : "s"}
            </div>

            {person.is_owner ? (
              <div className="muted small">
                The owner cannot be restricted or removed, by anyone.
              </div>
            ) : (
              <div className="zone-controls">
                {!person.is_admin && (
                  <button className="compact" onClick={() => setEditing(person)}>
                    Change access
                  </button>
                )}
                <button
                  className="compact"
                  onClick={() => act(() => setAdmin(person.id, !person.is_admin))}
                >
                  {person.is_admin ? "Remove admin" : "Make admin"}
                </button>
                <button className="compact" onClick={() => act(() => removePerson(person.id))}>
                  Remove
                </button>
              </div>
            )}
          </div>
        ))}

        {invites.length > 0 && (
          <div className="zone-card">
            <div className="zone-head"><span className="zone-name">Waiting to be accepted</span></div>
            {invites.map((invite) => (
              <div key={invite.code} className="invite-row">
                <div>
                  <b>{invite.display_name}</b>
                  <div className="muted small nm-clip">{inviteLink(invite.code)}</div>
                </div>
                <div className="zone-controls">
                  <CopyButton value={inviteLink(invite.code)} />
                  <button className="compact" onClick={() => act(() => revokeInvite(invite.code))}>
                    Cancel
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {inviting && (
          <InviteForm
            roots={roots}
            zones={zones}
            onDone={async () => {
              setInviting(false);
              await refresh();
            }}
            onCancel={() => setInviting(false)}
          />
        )}

        {editing && (
          <AccessForm
            person={editing}
            roots={roots}
            zones={zones}
            onDone={async () => {
              setEditing(null);
              await refresh();
            }}
            onCancel={() => setEditing(null)}
          />
        )}

        <Audiences
          folders={folders}
          rooms={rooms}
          people={people ?? []}
          onSave={async (kind, id, choice, users) => {
            await act(() =>
              kind === "folder"
                ? setFolderAudience(id, choice, users)
                : setRoomAudience(id, choice, users),
            );
          }}
        />

        <button className="compact" style={{ marginTop: 16 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** Folder and room pickers, shared by inviting and editing.
 *
 * Ticking grants; nothing ticked grants nothing. "Everything" is its own control
 * rather than the absence of ticks, so the two states cannot be mistaken for one
 * another — and the summary line says which one is currently chosen, because
 * this is the screen where being wrong is least recoverable.
 */
function Scope(props: { roots: DirEntry[]; zones: Zone[]; grant: Grant; onChange: (g: Grant) => void }) {
  const { grant, onChange } = props;

  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <>
      <label>Folders</label>
      <div className="ticks">
        <button
          className="tick"
          aria-pressed={grant.all_library}
          onClick={() => onChange({ ...grant, all_library: !grant.all_library, library: [] })}
        >
          {grant.all_library ? "☑" : "☐"} Whole library
        </button>
        {!grant.all_library &&
          props.roots.map((root) => (
            <button
              key={root.path}
              className="tick"
              aria-pressed={grant.library.includes(root.path)}
              onClick={() => onChange({ ...grant, library: toggle(grant.library, root.path) })}
            >
              {grant.library.includes(root.path) ? "☑" : "☐"} {root.name}
            </button>
          ))}
      </div>
      <p className="muted small">
        {grant.all_library
          ? "Everything in the library, including folders added later."
          : grant.library.length === 0
            ? "Nothing ticked — this person will see an empty library."
            : "Only the ticked folders will exist for this person."}
      </p>

      <label>Rooms</label>
      <div className="ticks">
        <button
          className="tick"
          aria-pressed={grant.all_zones}
          onClick={() => onChange({ ...grant, all_zones: !grant.all_zones, zones: [] })}
        >
          {grant.all_zones ? "☑" : "☐"} Any room
        </button>
        {!grant.all_zones &&
          props.zones.map((zone) => (
            <button
              key={zone.id}
              className="tick"
              aria-pressed={grant.zones.includes(zone.id)}
              onClick={() => onChange({ ...grant, zones: toggle(grant.zones, zone.id) })}
            >
              {grant.zones.includes(zone.id) ? "☑" : "☐"} {zone.name}
            </button>
          ))}
        {props.zones.length === 0 && !grant.all_zones && (
          <span className="muted small">No rooms set up yet.</span>
        )}
      </div>
      <p className="muted small">
        {grant.all_zones
          ? "Any room, including rooms added later."
          : grant.zones.length === 0
            ? "Nothing ticked — this person cannot play in any room."
            : "Only the ticked rooms will be offered."}
      </p>
    </>
  );
}

function InviteForm(props: {
  roots: DirEntry[];
  zones: Zone[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [handle, setHandle] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [grant, setGrant] = useState<Grant>(EMPTY);
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const { code } = await createInvite({
        handle: handle.trim(),
        display_name: displayName.trim(),
        ...grant,
      });
      setLink(inviteLink(code));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (link) {
    return (
      <div className="zone-card">
        <div className="zone-head"><span className="zone-name">Send this link</span></div>
        <p className="muted small">
          They open it <b>on their own phone or tablet</b> and create a passkey there.
          A passkey belongs to the device that made it, so it cannot be set up from yours.
        </p>
        <div className="invite-link nm-clip">{link}</div>
        <div className="zone-controls">
          <CopyButton value={link} primary />
          <button className="compact" onClick={props.onDone}>Done</button>
        </div>
      </div>
    );
  }

  return (
    <div className="zone-card">
      <div className="zone-head"><span className="zone-name">Invite someone</span></div>

      <label htmlFor="inv-name">Their name</label>
      <input id="inv-name" value={displayName} placeholder="Noa"
             onChange={(e) => setDisplayName(e.target.value)} />

      <label htmlFor="inv-handle">Username</label>
      <input id="inv-handle" value={handle} placeholder="noa" autoCapitalize="none"
             onChange={(e) => setHandle(e.target.value)} />

      <Scope roots={props.roots} zones={props.zones} grant={grant} onChange={setGrant} />

      {error && <div className="error">{error}</div>}

      <div className="zone-controls" style={{ marginTop: 12 }}>
        <button
          className="compact primary"
          disabled={busy || !handle.trim() || !displayName.trim()}
          onClick={submit}
        >
          {busy ? "Creating…" : "Create invitation"}
        </button>
        <button className="compact" onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/** Access is revisable at any time — children grow up, and a guest who needed the
 *  photos folder for an evening should not keep it for a year. */
function AccessForm(props: {
  person: Person;
  roots: DirEntry[];
  zones: Zone[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [grant, setGrant] = useState<Grant>({
    library: props.person.library,
    zones: props.person.zones.map((z) => z.id),
    all_library: props.person.all_library,
    all_zones: props.person.all_zones,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await setRules(props.person.id, grant);
      props.onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="zone-card">
      <div className="zone-head">
        <span className="zone-name">{props.person.display_name} — access</span>
      </div>

      <Scope roots={props.roots} zones={props.zones} grant={grant} onChange={setGrant} />

      {error && <div className="error">{error}</div>}

      <div className="zone-controls" style={{ marginTop: 12 }}>
        <button className="compact primary" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button className="compact" onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}


/** Who each folder and room is for.
 *
 * Anything undecided is listed first and says so. Folders are not created by
 * anyone here — sharing a folder with the server is enough to make it appear —
 * so "nobody has ruled on this yet" is a normal state that needs answering
 * rather than an error, and until it is answered the folder stays with
 * administrators.
 */
function Audiences(props: {
  folders: Place[];
  rooms: Place[];
  people: Person[];
  onSave: (kind: "folder" | "room", id: string, choice: Choice, users: string[]) => Promise<void>;
}) {
  const [open, setOpen] = useState<string | null>(null);

  const undecided = [...props.folders, ...props.rooms].filter((p) => p.audience === null).length;

  const row = (place: Place, kind: "folder" | "room") => (
    <div key={place.id} className="invite-row column">
      <div className="place-head">
        <div>
          <b>{place.name}</b>
          {place.audience === null && <span className="badge warn">needs a decision</span>}
          <div className="muted small">{describeAudience(place)}</div>
        </div>
        {open !== place.id && (
          <button className="compact" onClick={() => setOpen(place.id)}>
            {place.audience === null ? "Decide" : "Change"}
          </button>
        )}
      </div>

      {open === place.id && (
        <AudienceEditor
          initial={place.audience ?? "admins"}
          initialUsers={place.selected.map((p) => p.id)}
          people={props.people}
          onSave={async (choice, users) => {
            await props.onSave(kind, place.id, choice, users);
            setOpen(null);
          }}
          onCancel={() => setOpen(null)}
        />
      )}
    </div>
  );

  return (
    <div className="zone-card">
      <div className="zone-head">
        <span className="zone-name">Folders and rooms</span>
        {undecided > 0 && <span className="badge warn">{undecided} to decide</span>}
      </div>
      <p className="muted small" style={{ marginTop: 0 }}>
        A folder shared with the server appears on its own. Until you say who it is
        for, only administrators can see it.
      </p>

      {props.folders.map((f) => row(f, "folder"))}
      {props.rooms.map((r) => row(r, "room"))}
    </div>
  );
}


/** Copy, and say whether it worked.
 *
 * Over plain http the clipboard API is unavailable, so this can genuinely fail.
 * A button that silently does nothing would leave someone convinced they had
 * sent an invitation they had not.
 */
function CopyButton(props: { value: string; primary?: boolean }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");

  return (
    <button
      className={`compact${props.primary ? " primary" : ""}`}
      onClick={async () => {
        setState((await copyText(props.value)) ? "done" : "failed");
        window.setTimeout(() => setState("idle"), 2500);
      }}
    >
      {state === "done" ? "Copied" : state === "failed" ? "Select it above" : "Copy link"}
    </button>
  );
}
