package com.homesh.tv;

import android.app.Activity;

import java.util.function.Supplier;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.widget.VideoView;

/**
 * The half of playback a WebView cannot do.
 *
 * <p>A WebView carries a narrow set of codecs — roughly H.264 and VP8/9 in MP4
 * and WebM. The box it runs on carries far more: MPEG-2 from a DVD or an HDV
 * camcorder, MKV, AVI, all decoded in hardware. That gap is why a wedding tape
 * sent to a television opened a player and sat at 0:00.
 *
 * <p>So video goes to the platform's own player and everything else — pairing,
 * the socket, the queue, what is on screen between films — stays in the web app,
 * where it is shared with every other screen. The bridge is deliberately small:
 * start, stop, and where it has got to.
 */
public final class NativeVideo {

    private static final String TAG = "HomeshVideo";

    private final Activity activity;

    /**
     * The player, fetched when it is needed rather than captured now.
     *
     * <p>This class was handed the VideoView itself, from a line that ran before
     * the field holding it was assigned. It captured null and kept it: every
     * call through the bridge then dereferenced null on the UI thread, inside a
     * posted runnable where nothing catches it. Sending a video to a television
     * killed the app, and so did pressing stop.
     *
     * <p>A supplier reads the field at the moment of use, so the order the
     * activity happens to build its views in cannot break this again.
     */
    private final Supplier<VideoView> player;

    NativeVideo(Activity activity, Supplier<VideoView> player) {
        this.activity = activity;
        this.player = player;
    }

    /** The player, or null before the activity has built one. */
    private VideoView view() {
        try {
            return player.get();
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** Whether the web app should hand video over rather than play it itself. */
    @JavascriptInterface
    public boolean available() {
        return true;
    }

    /**
     * Which build of the app this is, for the screen to show.
     *
     * <p>The app updates itself, which is the point — but it also means nobody
     * can tell by looking whether a given television took the update. Reading
     * the version off the screen it is already on beats walking to it with a
     * laptop and a cable.
     */
    @JavascriptInterface
    public String appVersion() {
        try {
            return activity.getPackageManager()
                    .getPackageInfo(activity.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "";
        }
    }

    @JavascriptInterface
    public void play(String url, int positionMs) {
        activity.runOnUiThread(() -> {
            final VideoView view = view();
            if (view == null) {
                Log.w(TAG, "asked to play before there is a player: " + url);
                return;
            }
            try {
                view.setVisibility(android.view.View.VISIBLE);
                view.setVideoPath(url);
                view.requestFocus();

                // Seek only once the player exists.
                //
                // setVideoPath is asynchronous: it asks for a player and returns
                // long before there is one. Calling seekTo straight afterwards
                // reaches a MediaPlayer in no state to be seeked, which throws —
                // on the UI thread, from inside a posted runnable, where nothing
                // catches it and the app simply dies. It only bit once the
                // server started sending a position with the content: an
                // interrupted film resumes where it stopped, so the first press
                // of "send to this room" crashed the television.
                view.setOnPreparedListener(player -> {
                    try {
                        if (positionMs > 0) player.seekTo(positionMs);
                        player.start();
                    } catch (IllegalStateException gone) {
                        Log.w(TAG, "the player went away before it could start", gone);
                    }
                });
            } catch (RuntimeException e) {
                // Hidden again, quietly. This handler used to be the crash
                // rather than the recovery: it touched the same null view that
                // had just thrown, and the second throw had nothing to catch it.
                Log.w(TAG, "could not start " + url, e);
                quietly(() -> view.setVisibility(android.view.View.GONE));
            }
        });
    }

    @JavascriptInterface
    public void pause() {
        // The call is wrapped rather than the method reference: a bound
        // reference on a null receiver throws as it is created, which is
        // outside quietly() and so outside anything that would catch it.
        activity.runOnUiThread(() -> quietly(() -> {
            VideoView v = view();
            if (v != null) v.pause();
        }));
    }

    @JavascriptInterface
    public void resume() {
        // The call is wrapped rather than the method reference: a bound
        // reference on a null receiver throws as it is created, which is
        // outside quietly() and so outside anything that would catch it.
        activity.runOnUiThread(() -> quietly(() -> {
            VideoView v = view();
            if (v != null) v.start();
        }));
    }

    @JavascriptInterface
    public void stop() {
        activity.runOnUiThread(() -> quietly(() -> {
            VideoView v = view();
            if (v == null) return;
            v.stopPlayback();
            // Hidden as well as stopped: a VideoView left visible keeps a black
            // rectangle over the web app that nothing else can be seen through.
            v.setVisibility(android.view.View.GONE);
        }));
    }

    /** Run something on the player, treating a bad state as nothing to do.
     *
     * <p>Every one of these reaches a MediaPlayer whose state belongs to the
     * platform, not to us: pausing something already stopped, or starting
     * something still opening, is a normal consequence of a command arriving
     * from another room at an awkward moment. None of it is worth a crash.
     */
    private void quietly(Runnable action) {
        try {
            action.run();
        } catch (IllegalStateException | NullPointerException e) {
            Log.w(TAG, "the player was not in a state for that", e);
        }
    }

    /** Polled by the web app so the server's idea of position stays true. */
    @JavascriptInterface
    public int positionMs() {
        try {
            VideoView v = view();
            return v == null ? 0 : v.getCurrentPosition();
        } catch (RuntimeException e) {
            return 0;
        }
    }

    @JavascriptInterface
    public int durationMs() {
        try {
            VideoView v = view();
            if (v == null) return 0;
            int d = v.getDuration();
            return d > 0 ? d : 0;
        } catch (RuntimeException e) {
            return 0;
        }
    }

    @JavascriptInterface
    public boolean isPlaying() {
        try {
            VideoView v = view();
            return v != null && v.isPlaying();
        } catch (RuntimeException e) {
            return false;
        }
    }
}
