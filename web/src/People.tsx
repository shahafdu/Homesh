import { useCallback, useEffect, useState } from "react";
import { ApiError } from "./api";
import { browse, type DirEntry } from "./library";
import {
  createInvite,
  describeAccess,
  inviteLink,
  listInvites,
  listPeople,
  removePerson,
  revokeInvite,
  setRules,
  type Invite,
  type Person,
} from "./people";
import { listZones, type Zone } from "./zones";

/** Who has an account, and what they can reach.
 *
 * Access is expressed as "everything unless told otherwise", so the adults need
 * no configuration and only the accounts that need scoping have any.
 */
export default function People(props: { onClose: () => void }) {
  const [people, setPeople] = useState<Person[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [roots, setRoots] = useState<DirEntry[]>([]);
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<Person | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [p, i, z, r] = await Promise.all([
        listPeople(),
        listInvites(),
        listZones(),
        browse("/"),
      ]);
      setPeople(p);
      setInvites(i);
      setZones(z);
      setRoots(r.dirs);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
              {person.is_admin && <span className="badge">admin</span>}
            </div>
            <div className="muted small">{describeAccess(person)}</div>
            <div className="muted small">
              {person.passkeys} passkey{person.passkeys === 1 ? "" : "s"}
            </div>
            {!person.is_admin && (
              <div className="zone-controls">
                <button className="compact" onClick={() => setEditing(person)}>
                  Change access
                </button>
                <button
                  className="compact"
                  onClick={async () => {
                    await removePerson(person.id).catch((e) =>
                      setError(e instanceof ApiError ? e.message : String(e)),
                    );
                    await refresh();
                  }}
                >
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
                  <button
                    className="compact"
                    onClick={() => void navigator.clipboard?.writeText(inviteLink(invite.code))}
                  >
                    Copy link
                  </button>
                  <button
                    className="compact"
                    onClick={async () => {
                      await revokeInvite(invite.code);
                      await refresh();
                    }}
                  >
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

        <button className="compact" style={{ marginTop: 16 }} onClick={props.onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

/** Folder and room pickers, shared by inviting and editing.
 *
 * Nothing ticked means no restriction, which is stated rather than implied —
 * an empty list of ticks reading as "no access" would be the opposite of true.
 */
function Scope(props: {
  roots: DirEntry[];
  zones: Zone[];
  library: string[];
  chosenZones: string[];
  onLibrary: (next: string[]) => void;
  onZones: (next: string[]) => void;
}) {
  const toggle = (list: string[], value: string) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

  return (
    <>
      <label>Folders</label>
      <div className="ticks">
        {props.roots.map((root) => (
          <button
            key={root.path}
            className="tick"
            aria-pressed={props.library.includes(root.path)}
            onClick={() => props.onLibrary(toggle(props.library, root.path))}
          >
            {props.library.includes(root.path) ? "☑" : "☐"} {root.name}
          </button>
        ))}
      </div>
      <p className="muted small">
        {props.library.length === 0
          ? "Nothing ticked — this person will see the whole library."
          : "Only the ticked folders will exist for this person."}
      </p>

      <label>Rooms</label>
      <div className="ticks">
        {props.zones.map((zone) => (
          <button
            key={zone.id}
            className="tick"
            aria-pressed={props.chosenZones.includes(zone.id)}
            onClick={() => props.onZones(toggle(props.chosenZones, zone.id))}
          >
            {props.chosenZones.includes(zone.id) ? "☑" : "☐"} {zone.name}
          </button>
        ))}
        {props.zones.length === 0 && <span className="muted small">No rooms set up yet.</span>}
      </div>
      <p className="muted small">
        {props.chosenZones.length === 0
          ? "Nothing ticked — this person can play in any room."
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
  const [library, setLibrary] = useState<string[]>([]);
  const [zones, setZones] = useState<string[]>([]);
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
        library,
        zones,
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
          <button className="compact primary" onClick={() => void navigator.clipboard?.writeText(link)}>
            Copy link
          </button>
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

      <Scope
        roots={props.roots}
        zones={props.zones}
        library={library}
        chosenZones={zones}
        onLibrary={setLibrary}
        onZones={setZones}
      />

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

function AccessForm(props: {
  person: Person;
  roots: DirEntry[];
  zones: Zone[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [library, setLibrary] = useState<string[]>(props.person.library ?? []);
  const [zones, setZones] = useState<string[]>((props.person.zones ?? []).map((z) => z.id));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await setRules(props.person.id, { library, zones });
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

      <Scope
        roots={props.roots}
        zones={props.zones}
        library={library}
        chosenZones={zones}
        onLibrary={setLibrary}
        onZones={setZones}
      />

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
