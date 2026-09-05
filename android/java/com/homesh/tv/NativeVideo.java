package com.homesh.tv;

import android.app.Activity;
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
    private final VideoView view;

    NativeVideo(Activity activity, VideoView view) {
        this.activity = activity;
        this.view = view;
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
                Log.w(TAG, "could not start " + url, e);
                view.setVisibility(android.view.View.GONE);
            }
        });
    }

    @JavascriptInterface
    public void pause() {
        activity.runOnUiThread(() -> quietly(view::pause));
    }

    @JavascriptInterface
    public void resume() {
        activity.runOnUiThread(() -> quietly(view::start));
    }

    @JavascriptInterface
    public void stop() {
        activity.runOnUiThread(() -> {
            quietly(view::stopPlayback);
            // Hidden as well as stopped: a VideoView left visible keeps a black
            // rectangle over the web app that nothing else can be seen through.
            view.setVisibility(android.view.View.GONE);
        });
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
            return view.getCurrentPosition();
        } catch (RuntimeException e) {
            return 0;
        }
    }

    @JavascriptInterface
    public int durationMs() {
        try {
            int d = view.getDuration();
            return d > 0 ? d : 0;
        } catch (RuntimeException e) {
            return 0;
        }
    }

    @JavascriptInterface
    public boolean isPlaying() {
        try {
            return view.isPlaying();
        } catch (RuntimeException e) {
            return false;
        }
    }
}
