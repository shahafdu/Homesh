import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { FileEntry } from "./library";

export interface Track {
  item_id: string;
  filename: string;
  /** Where it lives. Shown under the title, because the folder is often the album. */
  path: string;
}

export interface PlayerState {
  queue: Track[];
  index: number;
  playing: boolean;
  position: number;
  duration: number;
  volume: number;
  /** Set when playback fails for a reason worth telling the user about. */
  error: string | null;
}

const INITIAL: PlayerState = {
  queue: [],
  index: -1,
  playing: false,
  position: 0,
  duration: 0,
  volume: 1,
  error: null,
};

async function signedUrl(itemId: string): Promise<string> {
  const { url } = await api.get<{ url: string; expires_in: number }>(
    `/api/items/${itemId}/url`,
  );
  return url;
}

export function usePlayer() {
  const [state, setState] = useState<PlayerState>(INITIAL);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Kept in a ref as well as state so the media element's event handlers, which
  // close over their first render, can still see the current queue.
  const stateRef = useRef(state);
  stateRef.current = state;

  if (audioRef.current === null && typeof Audio !== "undefined") {
    audioRef.current = new Audio();
    audioRef.current.preload = "metadata";
    // Needed for the element to be allowed to play at all on iOS, and for the
    // lock-screen controls to attach to it.
    audioRef.current.setAttribute("playsinline", "");
  }

  // Phones only let an <audio> element play if a gesture started it, and they
  // judge that by whether play() was called *synchronously* inside the handler.
  // Ours cannot be: the signed URL has to be fetched first, and by the time it
  // arrives the gesture has expired — so playback was silently refused on every
  // phone while working perfectly on a desktop, where the rule does not apply.
  //
  // The fix is to spend one real gesture unlocking the element. After a single
  // play() inside a genuine touch, the phone treats it as user-initiated for the
  // rest of the page's life.
  const unlocked = useRef(false);
  useEffect(() => {
    const unlock = () => {
      const audio = audioRef.current;
      if (!audio || unlocked.current) return;
      unlocked.current = true;
      audio.play().then(
        () => audio.pause(),
        () => {
          // Refused before anything was loaded, which is expected and harmless;
          // the element is activated either way.
        },
      );
    };

    // Once, on the first touch anywhere. Capture, so a handler that stops
    // propagation cannot cost us the only gesture we need.
    const opts = { once: true, capture: true } as const;
    document.addEventListener("pointerdown", unlock, opts);
    document.addEventListener("keydown", unlock, opts);
    return () => {
      document.removeEventListener("pointerdown", unlock, opts);
      document.removeEventListener("keydown", unlock, opts);
    };
  }, []);

  // One renewal attempt per track. Without this, a genuinely broken file would
  // loop forever between the error handler and a fresh URL.
  const renewedFor = useRef<string | null>(null);

  const loadTrack = useCallback(async (index: number, resumeAt = 0) => {
    const audio = audioRef.current;
    const track = stateRef.current.queue[index];
    if (!audio || !track) return;

    // Move the cursor first. Setting it only on success made the UI lie about
    // which track was selected whenever loading failed.
    setState((s) => ({
      ...s,
      index,
      position: resumeAt,
      duration: 0,
      playing: false,
      error: null,
    }));
    stateRef.current = { ...stateRef.current, index };
    renewedFor.current = null;

    try {
      audio.src = await signedUrl(track.item_id);
      if (resumeAt > 0) audio.currentTime = resumeAt;
      await audio.play();
      setState((s) => ({ ...s, playing: true, error: null }));
    } catch (e) {
      // A play() rejection is usually the browser's autoplay policy, which is not
      // worth alarming the user about — they clicked, so it will succeed on retry.
      if (e instanceof Error && e.name === "NotAllowedError") {
        setState((s) => ({ ...s, playing: false }));
      } else {
        setState((s) => ({ ...s, error: `Could not play ${track.filename}` }));
      }
    }
  }, []);

  const play = useCallback(
    (files: FileEntry[], startIndex: number, folderPath: string) => {
      // Queue the whole folder's audio, so playing one track behaves like an album
      // rather than a single file.
      const audioFiles = files.filter((f) => f.kind === "audio" && f.available);
      const clicked = files[startIndex];
      const queue: Track[] = audioFiles.map((f) => ({
        item_id: f.item_id,
        filename: f.filename,
        path: folderPath,
      }));
      const index = Math.max(0, queue.findIndex((t) => t.item_id === clicked.item_id));

      setState((s) => ({ ...s, queue, index, error: null }));
      stateRef.current = { ...stateRef.current, queue, index };
      void loadTrack(index);
    },
    [loadTrack],
  );

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || stateRef.current.index < 0) return;
    if (audio.paused) {
      void audio.play();
      setState((s) => ({ ...s, playing: true }));
    } else {
      audio.pause();
      setState((s) => ({ ...s, playing: false }));
    }
  }, []);

  const skip = useCallback(
    (delta: number) => {
      const { index, queue } = stateRef.current;
      const next = index + delta;
      if (next < 0 || next >= queue.length) return;
      void loadTrack(next);
    },
    [loadTrack],
  );

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (audio) audio.currentTime = seconds;
  }, []);

  const setVolume = useCallback((v: number) => {
    const audio = audioRef.current;
    if (audio) audio.volume = v;
    setState((s) => ({ ...s, volume: v }));
  }, []);

  const stop = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    setState(INITIAL);
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onTime = () => setState((s) => ({ ...s, position: audio.currentTime }));
    const onMeta = () => setState((s) => ({ ...s, duration: audio.duration || 0 }));
    const onEnd = () => {
      const { index, queue } = stateRef.current;
      if (index + 1 < queue.length) void loadTrack(index + 1);
      else setState((s) => ({ ...s, playing: false, position: 0 }));
    };

    const onError = async () => {
      const { index, queue, position } = stateRef.current;
      if (index < 0 || !queue[index]) return;
      const track = queue[index];

      // The element's error code separates the two very different causes.
      // NETWORK means the fetch failed, which for us usually means the signed URL
      // expired mid-track — recoverable. DECODE and SRC_NOT_SUPPORTED mean the
      // file itself is unplayable, and re-fetching it would loop forever.
      const code = audio.error?.code;
      const recoverable =
        code === MediaError.MEDIA_ERR_NETWORK && renewedFor.current !== track.item_id;

      if (recoverable) {
        renewedFor.current = track.item_id;
        try {
          audio.src = await signedUrl(track.item_id);
          audio.currentTime = position;
          await audio.play();
          return;
        } catch {
          /* fall through to skipping */
        }
      }

      // Unplayable. Move on rather than stalling the queue on one bad file.
      if (index + 1 < queue.length) {
        setState((s) => ({ ...s, error: `Skipped ${track.filename} — cannot play it` }));
        void loadTrack(index + 1);
      } else {
        setState((s) => ({ ...s, playing: false, error: `Cannot play ${track.filename}` }));
      }
    };

    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("loadedmetadata", onMeta);
    audio.addEventListener("ended", onEnd);
    audio.addEventListener("error", onError);
    return () => {
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("loadedmetadata", onMeta);
      audio.removeEventListener("ended", onEnd);
      audio.removeEventListener("error", onError);
    };
  }, [loadTrack]);

  const current = state.index >= 0 ? state.queue[state.index] : null;
  return { state, current, play, toggle, skip, seek, setVolume, stop };
}

export function formatTime(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
