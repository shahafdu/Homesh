package com.homesh.tv;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.VideoView;

/**
 * The screen itself.
 *
 * <p>A deliberately thin shell around the same /tv page a browser would load.
 * The renderer logic — pairing, the socket, playback reporting — lives in the
 * web app and is shared with every other screen, so there is one implementation
 * to keep correct rather than two that drift.
 *
 * <p>What the shell adds is the handful of things a browser tab cannot do: an
 * entry in the TV launcher, a screen that never sleeps mid-film, playback that
 * starts without somebody pressing something first, and somewhere to put the
 * server address.
 */
public class MainActivity extends Activity {

    private WebView web;
    private VideoView video;
    private TextView problem;
    private String server;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        server = Prefs.server(this);
        if (server == null) {
            startActivity(new Intent(this, SetupActivity.class));
            finish();
            return;
        }

        // A film is a long stretch with no input at all, which is exactly what
        // the screen timeout is waiting for.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        web = new WebView(this);
        configure(web.getSettings());
        web.setBackgroundColor(Color.BLACK);
        web.setWebChromeClient(new WebChromeClient());
        // Named for what it is on the page. Only the methods marked
        // @JavascriptInterface are reachable, and the page is served by this
        // household's own server.
        web.addJavascriptInterface(new NativeVideo(this, video), "HomeshVideo");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Everything this app shows is served by the house server. A link
                // anywhere else is not something a TV should be following.
                String url = request.getUrl().toString();
                return !url.startsWith(server);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request,
                                        WebResourceError error) {
                // Only the main document matters. A failed thumbnail should not
                // replace a working screen with an error.
                if (request.isForMainFrame()) {
                    showProblem(getString(R.string.cannot_reach, server));
                }
            }
        });

        // Above the web app and hidden until there is something to play. The
        // platform player decodes what the WebView cannot, which on a television
        // is most of what a home library actually contains.
        video = new VideoView(this);
        video.setVisibility(View.GONE);
        video.setOnCompletionListener(mp -> {
            video.setVisibility(View.GONE);
            // Told rather than discovered: the server owns the queue, so the end
            // of a film is news it needs in order to start the next one.
            web.evaluateJavascript("window.homeshVideoEnded && window.homeshVideoEnded()", null);
        });
        video.setOnErrorListener((mp, what, extra) -> {
            video.setVisibility(View.GONE);
            web.evaluateJavascript(
                    "window.homeshVideoFailed && window.homeshVideoFailed(" + what + ")", null);
            return true;
        });

        problem = new TextView(this);
        problem.setTextColor(Color.WHITE);
        problem.setTextSize(18);
        problem.setPadding(64, 64, 64, 64);
        problem.setVisibility(View.GONE);

        root.addView(web);
        root.addView(video);
        root.addView(problem);
        setContentView(root);

        immersive();
        web.loadUrl(server + "/tv");

        verifyAddress();
        checkForUpdate();
    }

    /**
     * Check the saved address still leads somewhere, and go looking if it does not.
     *
     * <p>Every address in this house comes from DHCP, so the server's can change
     * — after a router restart, a lease expiring, a power cut. A screen that had
     * been set up once would then sit showing "cannot reach" until somebody
     * walked over and typed the new address in with a remote control, which is
     * exactly the chore discovery exists to remove. So it runs on every launch,
     * not only the first.
     *
     * <p>In the background, after the page has already been asked for: when the
     * address is still right — the ordinary case — nothing is delayed.
     */
    private void verifyAddress() {
        new Thread(() -> {
            // Keeps looking rather than giving up after one try.
            //
            // A single attempt at launch is enough when the server has merely
            // moved, and no use at all in the case that actually happens: the
            // power comes back, every device boots at once, and the television
            // is asking before the server has finished starting. It then sat
            // showing an address that had been wrong for a minute until
            // somebody walked over with the remote — which is the chore all of
            // this exists to remove.
            for (int attempt = 0; ; attempt++) {
                if (Server.reachable(server)) return;

                String found = Discovery.find();
                if (found != null && !found.equals(server)) {
                    Log.i("Homesh", "server moved to " + found);
                    Prefs.setServer(this, found);
                    server = found;

                    runOnUiThread(() -> {
                        problem.setVisibility(View.GONE);
                        web.setVisibility(View.VISIBLE);
                        web.loadUrl(server + "/tv");
                    });
                    return;
                }

                // Backing off to half a minute: brisk while somebody is standing
                // there watching it fail, quiet once the room has been given up
                // on for the evening.
                try {
                    Thread.sleep(attempt < 5 ? 3000 : 30000);
                } catch (InterruptedException stop) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
        }).start();
    }

    /**
     * Ask the server whether it has a newer build, and install it if so.
     *
     * <p>On every launch, in the background, after the screen is already
     * working: an update is not worth delaying the thing somebody just opened.
     * Without this, every change means walking to each television and typing a
     * URL with a remote control.
     */
    private void checkForUpdate() {
        new Thread(() -> {
            Integer offered = Updater.availableVersion(server);
            if (offered == null) return;

            int installed = Updater.installedVersion(this);
            if (offered <= installed) return;

            Log.i("Homesh", "update available: " + installed + " -> " + offered);
            Updater.downloadAndInstall(this, server);
        }).start();
    }

    private void configure(WebSettings s) {
        s.setJavaScriptEnabled(true);

        // The TV page keeps its pairing credential and its device identity in
        // localStorage, exactly as it does in a browser. Without this the screen
        // would forget which room it is on every restart and re-pair.
        s.setDomStorageEnabled(true);

        // Nobody is going to tap a TV to permit the video it just asked for.
        s.setMediaPlaybackRequiresUserGesture(false);

        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
    }

    private void showProblem(String message) {
        problem.setText(message);
        problem.setVisibility(View.VISIBLE);
        web.setVisibility(View.GONE);
    }

    /** Nothing but the picture: no status bar, no navigation bar. */
    private void immersive() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
        }
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
    }

    @Override
    public boolean onKeyDown(int code, KeyEvent event) {
        // The only way back to the address field once the app is set up. MENU is
        // on most remotes; the error screen offers the same route for the case
        // where the address is wrong and the page never loads.
        if (code == KeyEvent.KEYCODE_MENU) {
            startActivity(new Intent(this, SetupActivity.class));
            return true;
        }
        return super.onKeyDown(code, event);
    }

    @Override
    public void onBackPressed() {
        // A renderer has nowhere to go back to, so back leaves rather than
        // stranding the screen on a blank page.
        finish();
    }

    @Override
    protected void onDestroy() {
        if (video != null) {
            video.stopPlayback();
        }
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
