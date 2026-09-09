import { formatTime, type PlayerState, type Track } from "./player";

/** The playing bar, in three rows.
 *
 * It used to be one row: controls, then the title, then the scrubber, then the
 * volume, count, shuffle and close, all competing for the same line. On a phone
 * that wrapped into an unpredictable shape, and the order things landed in bore
 * no relation to how often they are reached for.
 *
 * Now it reads top to bottom the way it is used: what is playing and how to
 * leave, then the controls, then the position. The close button is where a
 * close button goes — the top right corner — rather than at the end of a queue
 * of controls where it sat beside Shuffle and could be hit by mistake.
 */
export default function Player(props: {
  state: PlayerState;
  current: Track | null;
  onToggle: () => void;
  onSkip: (delta: number) => void;
  onSeek: (seconds: number) => void;
  onVolume: (v: number) => void;
  onStop: () => void;
  shuffle: boolean;
  onShuffle: () => void;
  /** Open the folder or playlist this queue came from. */
  onOpenOrigin: () => void;
}) {
  const { state, current } = props;
  if (!current) return null;

  const { position, duration, playing, queue, index, volume } = state;
  const progress = duration > 0 ? (position / duration) * 100 : 0;

  return (
    <div className="player" role="region" aria-label="Now playing">
      {/* What is playing, and the way out. */}
      <div className="p-head">
        <div className="p-title">
          {/* The filename, again as the primary label. Consistent with the browser. */}
          <span className="p-name" title={current.filename}>
            {current.filename}
          </span>
          {/* The path, and — when the queue came from somewhere you can go back
              to — a way back to it. Starting a playlist used to mean losing it:
              you could hear it, but not see it or pick another track from it. */}
          {state.origin ? (
            <button
              className="p-where link"
              onClick={props.onOpenOrigin}
              title={`Open ${state.origin.label}`}
            >
              {state.origin.kind === "playlist" ? "≡ " : "▸ "}
              {state.origin.label || current.path}
            </button>
          ) : (
            <span className="p-where">{current.path}</span>
          )}
        </div>

        <button className="p-btn" onClick={props.onStop} aria-label="Close player" title="Close">
          ✕
        </button>
      </div>

      {/* Previous, play, next, shuffle — the order they are reached for, with
          the volume and the position in the queue kept to the side rather than
          between them. */}
      <div className="p-controls">
        <button
          className="p-btn"
          onClick={() => props.onSkip(-1)}
          disabled={queue.length < 2}
          aria-label="Previous track"
          title="Previous"
        >
          ◀◀
        </button>
        <button
          className="p-btn primary"
          onClick={props.onToggle}
          aria-label={playing ? "Pause" : "Play"}
          title={playing ? "Pause" : "Play"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <button
          className="p-btn"
          onClick={() => props.onSkip(1)}
          disabled={queue.length < 2}
          aria-label="Next track"
          title="Next"
        >
          ▶▶
        </button>
        <button
          className={`p-btn${props.shuffle ? " on" : ""}`}
          onClick={props.onShuffle}
          aria-pressed={props.shuffle}
          aria-label="Shuffle"
          title={props.shuffle ? "Shuffle on" : "Shuffle off"}
        >
          ⤨
        </button>

        <span className="p-spacer" />

        <span className="p-count">
          {index + 1}/{queue.length}
        </span>
        <input
          className="p-vol"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={volume}
          onChange={(e) => props.onVolume(Number(e.target.value))}
          aria-label="Volume"
        />
      </div>

      {/* Its own row. Sharing a line with the controls left it a few centimetres
          wide, and it is the control that most needs the width. */}
      <div className="p-scrub">
        <span className="p-time">{formatTime(position)}</span>
        <input
          className="p-range"
          type="range"
          min={0}
          max={duration || 0}
          step={0.1}
          value={Math.min(position, duration || 0)}
          onChange={(e) => props.onSeek(Number(e.target.value))}
          aria-label="Seek"
          style={{ ["--pct" as string]: `${progress}%` }}
        />
        <span className="p-time">{formatTime(duration)}</span>
      </div>

      {state.error && <div className="p-error">{state.error}</div>}
    </div>
  );
}
