import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import {
  getSettings,
  getStatus,
  getWork,
  listCalls,
  listModels,
  reason,
  refreshModels,
  retryWork,
  saveSettings,
  testModel,
  type AiSettings,
  type AiStatus,
  type Call,
  type Model,
  type SettingsPatch,
} from "./aiApi";
import {
  contextSize,
  kindTotal,
  modelRow,
  moved,
  OUTCOMES,
  PROVIDER_LABELS,
  providerLabel,
  purposeText,
  refreshedText,
  spendingText,
  stateSentence,
  testedText,
  testModelFor,
  withdrawnText,
  type ListedModel,
  type ProviderName,
  type TaskName,
  type Work,
  type WorkKind,
} from "./aiState";
import { useLockScroll } from "./useLockScroll";

const HOW_TO = "https://github.com/shahafdu/Homesh/blob/main/docs/AI.md";
const MAX_PER_TASK = 8;
const WORK_EVERY = 30_000;

const TASKS: { task: TaskName; title: string; needs?: string }[] = [
  {
    task: "chat",
    title: "Answering questions and doing what you ask",
    needs: "Needs a model that can use tools.",
  },
  { task: "bulk", title: "Background tagging" },
  { task: "translate", title: "Translating a search" },
];

const clock = (at: Date) =>
  at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

type Save = (patch: SettingsPatch) => Promise<void>;

/** The night work as last read: null while loading, "absent" where this server
 *  has none. */
interface WorkState {
  work: Work | "absent" | null;
  error: string | null;
  reload: () => void;
}

/** Read the night work now and every half minute while the page is open. */
function useWork(on: boolean): WorkState {
  const [work, setWork] = useState<Work | "absent" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState(0);
  const absent = work === "absent";

  useEffect(() => {
    if (!on || absent) return;
    let live = true;
    const read = () =>
      getWork()
        .then((w) => {
          if (!live) return;
          setWork(w);
          setError(null);
        })
        .catch((e) => {
          if (!live) return;
          // No such route: there is nothing to ask again about.
          if (e instanceof ApiError && e.status === 404) setWork("absent");
          else setError(reason(e));
        });
    void read();
    const timer = window.setInterval(read, WORK_EVERY);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [on, absent, turn]);

  const reload = useCallback(() => setTurn((t) => t + 1), []);
  return { work, error, reload };
}

/** What a save is doing, said beside the control that started it. */
function useSaver() {
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const run = async (work: () => Promise<void>) => {
    window.clearTimeout(timer.current);
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      await work();
      setSaved(true);
      timer.current = window.setTimeout(() => setSaved(false), 2000);
      return true;
    } catch (e) {
      setError(reason(e));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const note = (
    <>
      <span className="muted small" role="status">
        {saved ? "Saved" : ""}
      </span>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
    </>
  );
  return { busy, run, note };
}

/** The administrator's page for the assistant: who answers, what it may cost,
 *  when slow work runs, and what has been asked of it. */
export default function Ai(props: { onClose: () => void; onStandby?: boolean }) {
  useLockScroll();
  const { onClose } = props;
  const onStandby = !!props.onStandby;
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after anything that asks a provider, so the record is read again.
  const [asked, setAsked] = useState(0);
  const latest = useRef<AiSettings | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const close = useRef<HTMLButtonElement>(null);
  const night = useWork(!onStandby);

  const apply = useCallback((next: AiSettings) => {
    latest.current = next;
    setSettings(next);
  }, []);

  // One at a time, so that an older answer never overwrites a newer one.
  const inTurn = useCallback((work: () => Promise<void>) => {
    const next = queue.current.then(work);
    queue.current = next.catch(() => undefined);
    return next;
  }, []);

  const load = useCallback(() => {
    setError(null);
    inTurn(async () => {
      const [s, set] = await Promise.all([getStatus(), getSettings()]);
      setStatus(s);
      apply(set);
    }).catch((e) => setError(reason(e)));
  }, [apply, inTurn]);

  useEffect(load, [load]);

  const save = useCallback<Save>(
    (patch) =>
      inTurn(async () => {
        apply(await saveSettings(patch, latest.current?.timezone ?? "UTC"));
        // Whether the assistant is on can change with its list. Failing to hear
        // that is not a failed save.
        await getStatus()
          .then(setStatus)
          .catch(() => undefined);
      }),
    [apply, inTurn],
  );

  const changed = useCallback(() => {
    setAsked((n) => n + 1);
    load();
  }, [load]);

  // On the window: a button that has just been disabled no longer holds focus,
  // and Escape must still close the page.
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  useEffect(() => close.current?.focus(), []);

  const ready = status && settings;
  const sentence = ready
    ? stateSentence({
        onStandby,
        assistant: status.features.assistant,
        providers: settings.providers,
        paused: settings.paused,
        night: settings.night,
        work: night.work,
      })
    : null;
  const anyKey = !!settings && Object.values(settings.providers).some((p) => p.key);
  // The standby shows what the settings were, whatever keys it has itself.
  const showChoices = anyKey || onStandby;

  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Assistant and AI"
      onClick={onClose}
    >
      <div className="sheet-inner wide ai-page" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Assistant and AI</h2>
          <button ref={close} className="compact" onClick={onClose}>
            Close
          </button>
        </div>

        {onStandby && (
          <p className="ai-standby" role="status">
            These settings are changed on the main server. This is what they were at the last backup.
          </p>
        )}

        {!ready && !error && <p className="muted">Loading...</p>}
        {error && (
          <div className="error" role="alert">
            {error}{" "}
            <button className="compact" onClick={load}>
              Try again
            </button>
          </div>
        )}

        {ready && sentence && (
          <>
            <div className="setting">
              <p className="ai-state" role="status">
                {sentence.text}
                {sentence.howTo && (
                  <>
                    {" "}
                    (
                    <a href={HOW_TO} target="_blank" rel="noreferrer">
                      How to set one
                    </a>
                    )
                  </>
                )}
              </p>
            </div>

            <Providers
              settings={settings}
              defaultModel={status.model}
              onStandby={onStandby}
              onAsked={changed}
            />

            {showChoices && (
              <Models
                settings={settings}
                defaultModel={status.model}
                onStandby={onStandby}
                save={save}
                onAsked={changed}
                onRefreshed={load}
              />
            )}

            {showChoices && (
              <NightWork settings={settings} state={night} onStandby={onStandby} save={save} />
            )}

            <Calls
              reloadKey={asked}
              settings={settings}
              defaultModel={status.model}
              onStandby={onStandby}
              onAsked={changed}
            />
          </>
        )}
      </div>
    </div>
  );
}

/** Send one small request to a provider and say how it went. Only ever to a
 *  free model; a list that has never been read is read first. */
async function testProvider(
  provider: ProviderName,
  settings: AiSettings,
  defaultModel: string,
): Promise<string> {
  const listed = TASKS.flatMap((t) => settings.tasks[t.task]);
  const pick = async () =>
    testModelFor(provider, listed, defaultModel, await listModels({ free: true }));
  let id = await pick();
  if (!id) {
    await refreshModels();
    id = await pick();
  }
  if (!id) return `No free model is known for ${providerLabel(provider)}.`;
  return testedText(await testModel(id));
}

function Providers(props: {
  settings: AiSettings;
  defaultModel: string;
  onStandby: boolean;
  onAsked: () => void;
}) {
  const [busy, setBusy] = useState<ProviderName | null>(null);
  const [results, setResults] = useState<Partial<Record<ProviderName, string>>>({});
  const { settings, onStandby } = props;

  const test = async (provider: ProviderName) => {
    setBusy(provider);
    setResults((r) => ({ ...r, [provider]: undefined }));
    let said: string;
    try {
      said = await testProvider(provider, settings, props.defaultModel);
    } catch (e) {
      said = reason(e);
    }
    setResults((r) => ({ ...r, [provider]: said }));
    setBusy(null);
    props.onAsked();
  };

  return (
    <div className="setting">
      <h3>Providers</h3>
      {(Object.keys(PROVIDER_LABELS) as ProviderName[]).map((name) => {
        const p = settings.providers[name];
        return (
          <div key={name} className="ai-provider">
            <div className="ai-line">
              <b className="ai-name">{PROVIDER_LABELS[name]}</b>
              <span className="muted small">{p.key ? "Key present" : "No key"}</span>
              {p.key && p.usable && (
                <button
                  className="compact"
                  disabled={onStandby || busy !== null}
                  aria-label={`Test ${PROVIDER_LABELS[name]}`}
                  onClick={() => void test(name)}
                >
                  {busy === name ? "Testing..." : "Test"}
                </button>
              )}
              <span className="small" role="status">
                {results[name] ?? ""}
              </span>
            </div>
            {p.key && !p.usable && (
              <p className="muted small">
                {name === "gemini" ? (
                  <>
                    Not used: the server has not been told this key's Google project has no billing
                    account. Set <code>GEMINI_NO_BILLING=true</code> in .env once you have checked.
                  </>
                ) : (
                  <>
                    Not used: set <code>AI_PROVIDER=openrouter</code> in .env.
                  </>
                )}
              </p>
            )}
          </div>
        );
      })}
      <p className="muted small">Keys are set in the .env file on the PC. This page never shows them.</p>
      <p className="ai-spending">
        <b>Spending</b>{" "}
        <span className="muted small">
          {spendingText(settings.cap_micros, settings.spent_micros)}
        </span>
      </p>
    </div>
  );
}

function Models(props: {
  settings: AiSettings;
  defaultModel: string;
  onStandby: boolean;
  save: Save;
  onAsked: () => void;
  onRefreshed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  // Bumped after a refresh, so an open picker reads the new list.
  const [catalogue, setCatalogue] = useState(0);

  const refresh = async () => {
    setBusy(true);
    setSaid("");
    try {
      setSaid(refreshedText(await refreshModels()));
      setCatalogue((n) => n + 1);
      props.onRefreshed();
    } catch (e) {
      setSaid(reason(e));
    }
    setBusy(false);
  };

  return (
    <div className="setting">
      <h3>Which model does what</h3>
      {TASKS.map((t) => (
        <TaskBlock key={t.task} {...t} {...props} catalogue={catalogue} refresh={refresh} />
      ))}
      <div className="ai-line ai-refresh">
        <button
          className="compact"
          disabled={props.onStandby || busy}
          onClick={() => void refresh()}
        >
          {busy ? "Reading the lists..." : "Refresh the model list"}
        </button>
        <span className="muted small" role="status">
          {said}
        </span>
      </div>
    </div>
  );
}

function TaskBlock(props: {
  task: TaskName;
  title: string;
  needs?: string;
  settings: AiSettings;
  defaultModel: string;
  onStandby: boolean;
  save: Save;
  onAsked: () => void;
  catalogue: number;
  refresh: () => Promise<void>;
}) {
  const { task, settings, onStandby } = props;
  // The list as just changed, shown until the server answers. If it refuses,
  // dropping this puts the stored list back.
  const [pending, setPending] = useState<string[] | null>(null);
  const [added, setAdded] = useState<Record<string, ListedModel>>({});
  const [picking, setPicking] = useState(false);
  const [sure, setSure] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, string>>({});
  const saver = useSaver();

  const ids = pending ?? settings.tasks[task];
  const now = new Date();
  const rows = ids.map((id) =>
    modelRow(id, {
      task,
      listed: { ...added, ...settings.listed },
      providers: settings.providers,
      capMicros: settings.cap_micros,
      onStandby,
      now,
      time: clock,
    }),
  );

  const change = async (next: string[]) => {
    if (saver.busy) return;
    setSure(null);
    setPending(next);
    await saver.run(() => props.save({ tasks: { [task]: next } }));
    setPending(null);
  };

  const test = async (id: string) => {
    setTesting(id);
    setResults((r) => ({ ...r, [id]: "" }));
    let said: string;
    try {
      said = testedText(await testModel(id));
    } catch (e) {
      said = reason(e);
    }
    setResults((r) => ({ ...r, [id]: said }));
    setTesting(null);
    props.onAsked();
  };

  const openRouter = settings.providers.openrouter?.usable;

  return (
    <div className="ai-task" role="group" aria-label={props.title}>
      <div className="group-head">{props.title}</div>
      {props.needs && <p className="muted small">{props.needs}</p>}

      {rows.length === 0 && (
        <p className="muted small">
          {openRouter || onStandby ? (
            <>
              Using the model from the server's settings file (
              <span className="ai-id">{props.defaultModel}</span>). Add a model here to choose
              others.
            </>
          ) : (
            "No model is listed, and OpenRouter is not set up, so nothing answers here. Add a model."
          )}
        </p>
      )}

      {rows.length > 0 && (
        <ol className="ai-models">
          {rows.map((row, i) => (
            <li key={row.id} className={row.withdrawn ? "ai-model withdrawn" : "ai-model"}>
              <div className="ai-line">
                <span className="muted small">{i + 1}.</span>
                <span className="ai-name" dir="auto">
                  {row.label}
                </span>
                {!row.withdrawn && row.free !== null && (
                  <span className="badge">{row.free ? "free" : "paid"}</span>
                )}
              </div>
              {row.withdrawn ? (
                <p className="ai-warning small">
                  {/* The standby asks nobody, so it cannot say who is answering. */}
                  {onStandby ? "This model is no longer offered." : withdrawnText(rows, i)}
                </p>
              ) : (
                row.note && <p className="muted small">{row.note}</p>
              )}
              <div className="ai-line">
                {!row.withdrawn && (
                  <>
                    {/* aria-disabled at the ends and while a change is saved, so
                        the button keeps focus when a model arrives there, and a
                        press that would be ignored looks it. */}
                    <button
                      className="compact"
                      disabled={onStandby}
                      aria-disabled={i === 0 || saver.busy}
                      aria-label={`Move ${row.label} up`}
                      onClick={() => i > 0 && void change(moved(ids, i, -1))}
                    >
                      Up
                    </button>
                    <button
                      className="compact"
                      disabled={onStandby}
                      aria-disabled={i === rows.length - 1 || saver.busy}
                      aria-label={`Move ${row.label} down`}
                      onClick={() => i < rows.length - 1 && void change(moved(ids, i, 1))}
                    >
                      Down
                    </button>
                    <button
                      className="compact"
                      disabled={onStandby || testing !== null}
                      aria-label={`Test ${row.label}`}
                      onClick={() => void test(row.id)}
                    >
                      {testing === row.id ? "Testing..." : "Test"}
                    </button>
                  </>
                )}
                {sure === row.id ? (
                  <span className="ai-line">
                    <span className="small">Sure?</span>
                    <button
                      className="compact danger"
                      disabled={saver.busy}
                      onClick={() => void change(ids.filter((id) => id !== row.id))}
                    >
                      Yes, remove
                    </button>
                    <button className="compact" onClick={() => setSure(null)}>
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    className="compact"
                    disabled={onStandby}
                    aria-label={`Remove ${row.label}`}
                    onClick={() => setSure(row.id)}
                  >
                    Remove
                  </button>
                )}
                <span className="small" role="status">
                  {results[row.id] ?? ""}
                </span>
              </div>
            </li>
          ))}
        </ol>
      )}

      {picking ? (
        <ModelPicker
          task={task}
          taken={ids}
          paidHidden={settings.cap_micros <= 0}
          busy={saver.busy}
          catalogue={props.catalogue}
          refresh={props.refresh}
          onClose={() => setPicking(false)}
          onPick={(m) => {
            setPicking(false);
            setAdded((a) => ({ ...a, [m.id]: { ...m, gone: false } }));
            void change([...ids, m.id]);
          }}
        />
      ) : (
        <div className="ai-line">
          <button
            className="compact"
            disabled={onStandby || saver.busy || ids.length >= MAX_PER_TASK}
            onClick={() => setPicking(true)}
          >
            Add a model...
          </button>
          {ids.length >= MAX_PER_TASK && (
            <span className="muted small">A list holds at most {MAX_PER_TASK} models.</span>
          )}
        </div>
      )}
      {saver.note}
      {rows.length > 0 && (
        <p className="muted small">
          If the first is busy or over its free limit, the next one answers.
        </p>
      )}
    </div>
  );
}

function ModelPicker(props: {
  task: TaskName;
  taken: string[];
  paidHidden: boolean;
  /** A change to the list is being saved: choosing now would be lost. */
  busy: boolean;
  catalogue: number;
  refresh: () => Promise<void>;
  onPick: (m: Model) => void;
  onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [models, setModels] = useState<Model[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const tools = props.task === "chat";

  useEffect(() => {
    let live = true;
    // A pause, so that typing a name is one request rather than one per letter.
    const timer = window.setTimeout(
      () => {
        listModels({ tools, q: q.trim() })
          .then((list) => {
            if (!live) return;
            setModels(list);
            setError(null);
          })
          .catch((e) => live && setError(reason(e)));
      },
      q ? 250 : 0,
    );
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [q, tools, turn, props.catalogue]);

  const offered = models?.filter((m) => !props.taken.includes(m.id));

  return (
    <div
      className="ai-picker"
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        // The picker closes, not the page behind it.
        e.stopPropagation();
        props.onClose();
      }}
    >
      <div className="ai-line">
        <input
          type="search"
          aria-label="Search models"
          placeholder="Search models"
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="compact" onClick={props.onClose}>
          Cancel
        </button>
      </div>

      {error && (
        <div className="error" role="alert">
          {error}{" "}
          <button className="compact" onClick={() => setTurn((t) => t + 1)}>
            Try again
          </button>
        </div>
      )}
      {!error && !offered && <p className="muted small">Loading...</p>}
      {!error && offered?.length === 0 && (
        <div>
          <p className="muted small" role="status">
            {q.trim()
              ? "No model matches that search."
              : "No other model is known here. Reading the providers' lists may find some."}
          </p>
          {!q.trim() && (
            <button
              className="compact"
              disabled={refreshing}
              onClick={async () => {
                setRefreshing(true);
                await props.refresh();
                setRefreshing(false);
              }}
            >
              {refreshing ? "Reading the lists..." : "Refresh the model list"}
            </button>
          )}
        </div>
      )}
      {!error && !!offered?.length && (
        <ul className="ai-offered">
          {offered.map((m) => (
            <li key={m.id}>
              <button disabled={props.busy} onClick={() => props.onPick(m)}>
                <span className="ai-name" dir="auto">
                  {m.label}
                </span>
                <span className="muted small">
                  {[providerLabel(m.provider), m.free ? "free" : "paid", contextSize(m.context)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {props.paidHidden && (
        <p className="muted small">Paid models are hidden because no monthly limit is set.</p>
      )}
    </div>
  );
}

function NightWork(props: {
  settings: AiSettings;
  state: WorkState;
  onStandby: boolean;
  save: Save;
}) {
  const { settings, onStandby } = props;
  const { work, error, reload } = props.state;
  const [start, setStart] = useState(settings.night.start);
  const [end, setEnd] = useState(settings.night.end);
  // Two, so that each says how it went beside its own control.
  const times = useSaver();
  const pause = useSaver();

  useEffect(() => {
    setStart(settings.night.start);
    setEnd(settings.night.end);
  }, [settings.night.start, settings.night.end]);

  const saveTimes = async () => {
    if (start === settings.night.start && end === settings.night.end) return;
    const revert = () => {
      setStart(settings.night.start);
      setEnd(settings.night.end);
    };
    // A half-typed time reads as empty.
    if (!start || !end) return revert();
    if (!(await times.run(() => props.save({ night: { start, end } })))) revert();
  };

  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const willMove = !onStandby && settings.timezone === "UTC" && browser && browser !== "UTC";
  const known = work && work !== "absent" && work.worker !== "not installed" ? work : null;

  return (
    <div className="setting">
      <h3>Night work</h3>
      <p>Slow jobs run at night so the PC stays free in the day.</p>

      {!onStandby && known?.worker === "not answering" && (
        <p className="ai-alarm" role="alert">
          The night worker is not running. Start Homesh again on the PC.
        </p>
      )}

      <div className="ai-line ai-times">
        <label>
          From
          <input
            type="time"
            value={start}
            disabled={onStandby}
            onChange={(e) => setStart(e.target.value)}
            onBlur={() => void saveTimes()}
          />
        </label>
        <label>
          to
          <input
            type="time"
            value={end}
            disabled={onStandby}
            onChange={(e) => setEnd(e.target.value)}
            onBlur={() => void saveTimes()}
          />
        </label>
      </div>
      <p className="muted small">
        {willMove
          ? `Times are in UTC until a setting is saved here, and in ${browser} from then on.`
          : `Times are in ${settings.timezone}.`}
      </p>
      {times.note}

      <div className="ai-line">
        {settings.paused && <span className="small">Paused.</span>}
        <button
          className="compact"
          disabled={onStandby || pause.busy}
          onClick={() => void pause.run(() => props.save({ paused: !settings.paused }))}
        >
          {settings.paused ? "Resume" : "Pause"}
        </button>
        {pause.note}
      </div>

      {/* The standby does no night work, so there is no progress to show. */}
      {!onStandby && (
        <div className="ai-work">
          {error && (
            <div className="error" role="alert">
              {error}{" "}
              <button className="compact" onClick={reload}>
                Try again
              </button>
            </div>
          )}
          {!error && work === null && <p className="muted small">Loading...</p>}
          {(work === "absent" || (work && work.worker === "not installed")) && (
            <p className="muted small">
              Nothing runs at night on this server yet. These settings are kept for when it does.
            </p>
          )}
          {known && known.kinds.length === 0 && (
            <p className="muted small">
              Nothing to do at night yet. Jobs appear here once there is something to work on.
            </p>
          )}
          {known?.kinds.map((k) => <Kind key={k.kind} kind={k} onRetried={reload} />)}
        </div>
      )}
    </div>
  );
}

function Kind(props: { kind: WorkKind; onRetried: () => void }) {
  const k = props.kind;
  const [why, setWhy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  const retry = async () => {
    setBusy(true);
    setSaid("");
    try {
      const { requeued } = await retryWork(k.kind);
      setSaid(`${requeued.toLocaleString()} will be tried again.`);
      props.onRetried();
    } catch (e) {
      setSaid(reason(e));
    }
    setBusy(false);
  };

  return (
    <div className="ai-kind">
      <b>{k.label}</b>
      {!k.installed ? (
        <p className="muted small">
          Not installed. Run <code>tools\fetch-models.ps1</code> on the PC to install it.
        </p>
      ) : (
        <>
          <div className="muted small">
            {k.done.toLocaleString()} of {kindTotal(k).toLocaleString()} done
            {!!k.nights_left &&
              ` · about ${k.nights_left} night${k.nights_left === 1 ? "" : "s"} left`}
          </div>
          {k.waiting > 0 && (
            <div className="muted small">
              {k.waiting.toLocaleString()} {k.waiting === 1 ? "is" : "are"} waiting for a drive that
              is off.
            </div>
          )}
          {k.failed > 0 && (
            <>
              <div className="ai-line">
                <span className="small">{k.failed.toLocaleString()} could not be done.</span>
                {k.last_error && (
                  <button className="compact" aria-expanded={why} onClick={() => setWhy(!why)}>
                    {why ? "Hide why" : "Show why"}
                  </button>
                )}
                <button className="compact" disabled={busy} onClick={() => void retry()}>
                  {busy ? "Putting them back..." : "Try those again"}
                </button>
              </div>
              {why && k.last_error && (
                <p className="ai-why small" dir="auto">
                  {k.last_error}
                </p>
              )}
            </>
          )}
          <div className="muted small" role="status">
            {said}
          </div>
        </>
      )}
    </div>
  );
}

/** Every request sent to a provider. The questions themselves are not in it. */
function Calls(props: {
  reloadKey: number;
  settings: AiSettings;
  defaultModel: string;
  onStandby: boolean;
  onAsked: () => void;
}) {
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState(0);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  useEffect(() => {
    let live = true;
    listCalls()
      .then((c) => {
        if (!live) return;
        setCalls(c);
        setError(null);
      })
      .catch((e) => live && setError(reason(e)));
    return () => {
      live = false;
    };
  }, [props.reloadKey, turn]);

  const usable = (Object.keys(PROVIDER_LABELS) as ProviderName[]).find(
    (p) => props.settings.providers[p]?.usable,
  );

  return (
    <div className="setting">
      <h3>What was done</h3>
      <p>
        The last 50 requests to a provider, newest first, refusals included. What was asked and
        answered is not kept here.
      </p>

      {error && (
        <div className="error" role="alert">
          {error}{" "}
          <button className="compact" onClick={() => setTurn((t) => t + 1)}>
            Try again
          </button>
        </div>
      )}
      {!error && calls === null && <p className="muted small">Loading...</p>}
      {!error && calls?.length === 0 && (
        <>
          <p className="muted small">
            Nothing yet. A line appears here each time a provider is asked something, a test
            included.
          </p>
          {usable && !props.onStandby && (
            <div className="ai-line">
              <button
                className="compact"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    setSaid(await testProvider(usable, props.settings, props.defaultModel));
                  } catch (e) {
                    setSaid(reason(e));
                  }
                  setBusy(false);
                  props.onAsked();
                }}
              >
                {busy ? "Testing..." : `Test ${PROVIDER_LABELS[usable]}`}
              </button>
              <span className="small" role="status">
                {said}
              </span>
            </div>
          )}
        </>
      )}
      {!error && !!calls?.length && (
        <ul className="ai-calls">
          {calls.map((c, i) => (
            <li key={`${c.at}-${i}`}>
              <div>
                <b dir="auto">{c.who ?? "Homesh itself"}</b> · {purposeText(c.purpose)}
              </div>
              <div className="muted small">
                {new Date(c.at).toLocaleString(undefined, {
                  day: "numeric",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
                {" · "}
                <span className="ai-id">{c.model}</span>
              </div>
              <div className="small">
                <span className={c.outcome === "ok" ? "ai-ok" : "ai-bad"}>
                  {OUTCOMES[c.outcome] ?? c.outcome}
                </span>
                {" · "}
                {c.tokens.toLocaleString()} tokens
              </div>
              {c.outcome !== "ok" && c.detail && (
                <div className="muted small" dir="auto">
                  {c.detail}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
