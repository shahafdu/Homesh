import { useCallback, useEffect, useState } from "react";
import { useLockScroll } from "./useLockScroll";
import {
  ago,
  beside,
  listMessages,
  markRead,
  messageHref,
  type BesideApp,
  type Message,
} from "./notifyApi";

/** The messages Homesh and the apps beside it have left for this account. */
export default function Inbox(props: { onClose: () => void; onRead: () => void }) {
  useLockScroll();
  const { onClose, onRead } = props;
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [apps, setApps] = useState<BesideApp[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listMessages()
      .then((list) => {
        setMessages(list);
        setError(null);
        const newest = list.reduce((n, m) => Math.max(n, m.id), 0);
        if (list.some((m) => !m.read)) {
          markRead(newest).then(onRead).catch(() => undefined);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [onRead]);

  useEffect(() => {
    load();
    beside().then(setApps).catch(() => undefined);
    const timer = window.setInterval(load, 60000);
    return () => window.clearInterval(timer);
  }, [load]);

  return (
    <div
      className="sheet"
      role="dialog"
      aria-modal="true"
      aria-label="Messages"
      onClick={onClose}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
    >
      <div className="sheet-inner" onClick={(e) => e.stopPropagation()}>
        <h2>Messages</h2>

        {error && (
          <p className="muted small" role="status">
            {error}{" "}
            <button className="compact" onClick={load}>
              Try again
            </button>
          </p>
        )}
        {!error && messages === null && <p className="muted small">Loading...</p>}
        {!error && messages !== null && messages.length === 0 && (
          <p className="muted small">No messages.</p>
        )}

        {messages?.map((m) => {
          const href = messageHref(m, apps);
          const inner = (
            <>
              <div className="msg-head">
                <b dir="auto">{m.title}</b>
                <span className="muted small">{ago(m.at)}</span>
              </div>
              {m.body && <div dir="auto">{m.body}</div>}
              <div className="muted small" dir="auto">
                {m.sender}
              </div>
            </>
          );
          return href ? (
            <a key={m.id} className="msg" href={href}>
              {inner}
            </a>
          ) : (
            <div key={m.id} className="msg">
              {inner}
            </div>
          );
        })}

        <button className="compact" style={{ marginTop: 12 }} onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
