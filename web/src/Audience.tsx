import { useState } from "react";
import type { Audience, Person } from "./people";

export type Choice = Exclude<Audience, null>;

export const AUDIENCE_OPTIONS: { value: Choice; label: string; hint: string }[] = [
  { value: "everyone", label: "Everyone", hint: "Anyone with an account on this server" },
  { value: "admins", label: "Administrators only", hint: "Nobody else, including now and later" },
  { value: "selected", label: "Only certain people", hint: "Choose below" },
];

/** Who a folder or room is for.
 *
 * Asked when the thing appears rather than left to be discovered later. A folder
 * shared with the server arrives on its own, and the moment it arrives is the
 * moment somebody knows what is in it.
 *
 * "Administrators only" is the default because it is the answer you can change
 * without having exposed anything in the meantime. The opposite default cannot
 * be undone — by the time you notice, it has been visible.
 */
export default function AudiencePicker(props: {
  value: Choice;
  users: string[];
  people: Person[];
  onChange: (value: Choice, users: string[]) => void;
}) {
  const { value, users, people } = props;

  // Administrators reach everything by definition, so offering to tick them
  // would suggest it made a difference.
  const pickable = people.filter((p) => !p.is_admin);

  const toggle = (id: string) =>
    props.onChange(
      "selected",
      users.includes(id) ? users.filter((u) => u !== id) : [...users, id],
    );

  return (
    <>
      <label>Who can see this</label>
      <div className="audience">
        {AUDIENCE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            className="tick wide"
            aria-pressed={value === opt.value}
            onClick={() => props.onChange(opt.value, opt.value === "selected" ? users : [])}
          >
            <span>
              {value === opt.value ? "◉" : "○"} {opt.label}
            </span>
            <span className="muted small">{opt.hint}</span>
          </button>
        ))}
      </div>

      {value === "selected" && (
        <>
          <div className="ticks">
            {pickable.map((p) => (
              <button
                key={p.id}
                className="tick"
                aria-pressed={users.includes(p.id)}
                onClick={() => toggle(p.id)}
              >
                {users.includes(p.id) ? "☑" : "☐"} {p.display_name}
              </button>
            ))}
            {pickable.length === 0 && (
              <span className="muted small">No other accounts yet.</span>
            )}
          </div>
          {users.length === 0 && (
            <p className="muted small">
              Nobody ticked — this stays with administrators until you choose someone.
            </p>
          )}
        </>
      )}
    </>
  );
}

/** The picker plus its own Save button, for changing something that exists. */
export function AudienceEditor(props: {
  initial: Choice;
  initialUsers: string[];
  people: Person[];
  busy?: boolean;
  onSave: (value: Choice, users: string[]) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState<Choice>(props.initial);
  const [users, setUsers] = useState<string[]>(props.initialUsers);

  return (
    <>
      <AudiencePicker
        value={value}
        users={users}
        people={props.people}
        onChange={(v, u) => {
          setValue(v);
          setUsers(u);
        }}
      />
      <div className="zone-controls" style={{ marginTop: 12 }}>
        <button
          className="compact primary"
          disabled={props.busy}
          onClick={() => props.onSave(value, users)}
        >
          {props.busy ? "Saving…" : "Save"}
        </button>
        <button className="compact" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </>
  );
}
