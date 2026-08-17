package com.homesh.tv;

import android.app.Activity;
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

    @JavascriptInterface
    public void play(String url, int positionMs) {
        activity.runOnUiThread(() -> {
            view.setVisibility(android.view.View.VISIBLE);
            view.setVideoPath(url);
            view.requestFocus();
            if (positionMs > 0) view.seekTo(positionMs);
            view.start();
        });
    }

    @JavascriptInterface
    public void pause() {
        activity.runOnUiThread(view::pause);
    }

    @JavascriptInterface
    public void resume() {
        activity.runOnUiThread(view::start);
    }

    @JavascriptInterface
    public void stop() {
        activity.runOnUiThread(() -> {
            view.stopPlayback();
            // Hidden as well as stopped: a VideoView left visible keeps a black
            // rectangle over the web app that nothing else can be seen through.
            view.setVisibility(android.view.View.GONE);
        });
    }

    /** Polled by the web app so the server's idea of position stays true. */
    @JavascriptInterface
    public int positionMs() {
        return view.getCurrentPosition();
    }

    @JavascriptInterface
    public int durationMs() {
        int d = view.getDuration();
        return d > 0 ? d : 0;
    }

    @JavascriptInterface
    public boolean isPlaying() {
        return view.isPlaying();
    }
}
