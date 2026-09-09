import { Component, type ErrorInfo, type ReactNode } from "react";

/** Catches a React error, shows what happened, and tells the server.
 *
 * Written because "it crashes" is not a diagnosable report and there is no way
 * for me to reproduce one: the app runs on a phone in another house, a React
 * error blanks the screen with no message, and the console is somewhere nobody
 * is going to open. What comes back is the word "crashes", which is where the
 * guessing starts and the evenings go.
 *
 * The screen crash endpoint already exists for exactly this reason and takes
 * exactly this shape, so this uses it rather than inventing a second one. It is
 * unauthenticated on purpose there: something already broken may have no usable
 * credential, and the alternative is the report going nowhere.
 *
 * A class, because componentDidCatch has no hook. It is the one thing React
 * still has no functional equivalent for.
 */
export default class Boundary extends Component<
  {
    children: ReactNode;
    /** What this boundary wraps, so a report says which part failed. */
    what: string;
    /** Offered when there is somewhere to go back to — closing a slideshow
     *  should return to the folder rather than reload the whole app. */
    onClose?: () => void;
  },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Best effort, and deliberately silent about its own failure: a boundary
    // that throws while reporting a throw is worse than one that says nothing.
    void fetch("/api/renderers/crash", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({
        device: navigator.userAgent.slice(0, 120),
        thread: this.props.what.slice(0, 120),
        version: "web",
        trace: `${error.message}\n${error.stack ?? ""}\n${info.componentStack ?? ""}`.slice(
          0,
          9000,
        ),
      }),
    }).catch(() => undefined);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="crashed">
        <div className="card">
          <h1>Something broke</h1>
          <p className="sub">
            The {this.props.what} stopped working. It has been reported — the
            details are in <code>docker compose logs api</code>.
          </p>
          {/* Shown rather than hidden. Whoever is looking at this is the person
              who will describe it to me, and a message beats "it crashed". */}
          <pre className="crash-detail">{error.message}</pre>
          <button onClick={() => window.location.reload()}>Reload Homesh</button>
          {this.props.onClose && (
            <button className="secondary" onClick={this.props.onClose}>
              Go back
            </button>
          )}
        </div>
      </div>
    );
  }
}
