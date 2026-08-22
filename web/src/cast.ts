/** Casting to a Chromecast, from the phone in your hand.
 *
 * The other half of "send to a room". A room runs our own app on a set-top box,
 * which is what makes wmv and avi playable and what keeps a screen under the
 * same access rules as everything else. Casting asks nothing of the receiving
 * device beyond Chromecast, which every Android TV box has built in — nothing
 * to install, nothing to pair.
 *
 * The cost is that a Chromecast plays what Google says it plays, and this
 * library is mostly formats it does not: of 820 videos here, 152 are MP4 and
 * the rest are wmv, avi, mpg, vob, 3gp and m2t. So the button is offered
 * honestly — lit for what will work, and explaining itself for what will not,
 * rather than starting something that fails on the television.
 *
 * Two things have to be true and are easy to miss. The sender API exists only
 * in Chrome and only in a secure context, so on plain http at a LAN address it
 * is simply absent. And the URL handed to the Chromecast is fetched *by the
 * Chromecast*, on the house network — never the ts.net address this page may
 * itself be loaded from, which nothing outside the tailnet can resolve.
 */

import { api } from "./api";

/** What a Chromecast will play, by extension.
 *
 * From Google's supported media documentation: MP4, WebM, MP3, WAV, OGG and the
 * common image formats, with H.264, VP8/9, HEVC or AV1 video. MPEG-2 is absent,
 * which rules out mpg, vob and m2t however they are wrapped.
 */
const CASTABLE = new Set([
  // Video
  "mp4", "m4v", "webm",
  // Audio
  "mp3", "wav", "ogg", "oga", "m4a", "aac", "flac",
  // Images
  "jpg", "jpeg", "png", "gif", "webp", "bmp",
]);

const MEDIA_TYPE: Record<string, string> = {
  mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm",
  mp3: "audio/mpeg", wav: "audio/wav", ogg: "audio/ogg", oga: "audio/ogg",
  m4a: "audio/mp4", aac: "audio/aac", flac: "audio/flac",
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png",
  gif: "image/gif", webp: "image/webp", bmp: "image/bmp",
};

export function castable(ext: string | null): boolean {
  return CASTABLE.has((ext ?? "").toLowerCase());
}

/** Why a file cannot be cast, in words worth reading on a phone. */
export function whyNotCastable(ext: string | null): string {
  const name = ext ? `.${ext.toLowerCase()}` : "This kind of file";
  return (
    `A Chromecast cannot play ${name} files — Google's list is MP4, WebM, MP3, ` +
    "WAV, OGG and the usual images. Send it to a room instead: the app on a " +
    "screen plays it by converting as it goes."
  );
}

// ── The sender API ──────────────────────────────────────────────────────────

declare global {
  interface Window {
    __onGCastApiAvailable?: (available: boolean) => void;
    cast?: any;
    chrome?: any;
  }
}

const SDK = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";

/** Google's own receiver, which plays a media URL and needs no registration. */
const DEFAULT_RECEIVER = "CC1AD845";

let loading: Promise<boolean> | null = null;

/** Load the Cast framework once, and say whether this browser has it.
 *
 * Chrome and Chromium only, and only in a secure context — so this is false on
 * a television, false in Firefox, and false over plain http. Callers hide the
 * button rather than showing one that cannot work.
 */
export function loadCast(): Promise<boolean> {
  if (loading) return loading;

  loading = new Promise<boolean>((resolve) => {
    if (window.cast?.framework) return resolve(true);
    if (!window.isSecureContext) return resolve(false);

    // The SDK calls this when it is ready, so it must exist before the script.
    window.__onGCastApiAvailable = (available: boolean) => {
      if (!available || !window.cast?.framework) return resolve(false);
      window.cast.framework.CastContext.getInstance().setOptions({
        receiverApplicationId: DEFAULT_RECEIVER,
        autoJoinPolicy: window.chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED,
      });
      resolve(true);
    };

    const script = document.createElement("script");
    script.src = SDK;
    // Absent in a browser without Cast, which is an answer rather than a fault.
    script.onerror = () => resolve(false);
    document.head.appendChild(script);
    // The SDK never reporting back is the same as saying no.
    window.setTimeout(() => resolve(Boolean(window.cast?.framework)), 6000);
  });

  return loading;
}

export type CastOutcome = { ok: true } | { ok: false; detail: string };

/** Hand one file to a Chromecast the viewer picks. */
export async function castFile(
  itemId: string,
  filename: string,
  ext: string | null,
): Promise<CastOutcome> {
  if (!(await loadCast())) {
    return { ok: false, detail: "This browser cannot cast. Chrome can, over https." };
  }

  // The address the *Chromecast* has to fetch from, which is not necessarily
  // the one this page was loaded from: a phone on Tailscale sees a ts.net name
  // that nothing on the house network can resolve.
  let base: string;
  try {
    const where = await api.get<{ lan: string | null }>("/tv.address");
    base = (where.lan ?? window.location.origin).replace(/\/$/, "");
  } catch {
    base = window.location.origin;
  }

  const { url } = await api.get<{ url: string }>(`/api/items/${itemId}/url`);

  const context = window.cast.framework.CastContext.getInstance();
  try {
    // Opens Chrome's own device picker, and resolves once a screen is chosen.
    await context.requestSession();
  } catch (e) {
    // Dismissing the picker is a choice, not a failure.
    if (String(e).includes("cancel")) return { ok: true };
    return { ok: false, detail: `No screen was reached — ${e}` };
  }

  const session = context.getCurrentSession();
  if (!session) return { ok: false, detail: "No screen was reached." };

  const suffix = (ext ?? "").toLowerCase();
  const info = new window.chrome.cast.media.MediaInfo(
    `${base}${url}`,
    MEDIA_TYPE[suffix] ?? "video/mp4",
  );
  info.metadata = new window.chrome.cast.media.GenericMediaMetadata();
  info.metadata.title = filename;

  try {
    await session.loadMedia(new window.chrome.cast.media.LoadRequest(info));
    return { ok: true };
  } catch (e) {
    return { ok: false, detail: `The screen would not play it — ${e}` };
  }
}
