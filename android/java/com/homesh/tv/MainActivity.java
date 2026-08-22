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
import android.widget.Button;
import android.widget.LinearLayout;
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
    private LinearLayout trouble;
    private Button changeAddress;
    private Button searchAgain;
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

        // The error screen: what went wrong, and two buttons that do something
        // about it.
        //
        // It used to be a line of text naming a key on the remote, which is a
        // poor way to offer a control: the key does not exist on every remote,
        // and nothing on screen looked like it could be pressed. A button that
        // is visible and takes focus needs no instructions.
        problem = new TextView(this);
        problem.setTextColor(Color.WHITE);
        problem.setTextSize(18);
        problem.setPadding(0, 0, 0, 32);
        problem.setVisibility(View.GONE);

        changeAddress = new Button(this);
        changeAddress.setText(R.string.change_address);
        changeAddress.setOnClickListener(v ->
                startActivity(new Intent(this, SetupActivity.class)));

        searchAgain = new Button(this);
        searchAgain.setText(R.string.search_again);
        searchAgain.setOnClickListener(v -> {
            searchAgain.setText(R.string.searching);
            new Thread(() -> {
                String found = Discovery.find();
                runOnUiThread(() -> {
                    searchAgain.setText(R.string.search_again);
                    if (found == null) return;
                    Prefs.setServer(this, found);
                    server = found;
                    trouble.setVisibility(View.GONE);
                    web.setVisibility(View.VISIBLE);
                    web.loadUrl(found + "/tv");
                });
            }).start();
        });

        trouble = new LinearLayout(this);
        trouble.setOrientation(LinearLayout.VERTICAL);
        trouble.setPadding(64, 64, 64, 64);
        trouble.setVisibility(View.GONE);
        // Filling the frame rather than wrapping its contents, which is what a
        // FrameLayout child does by default — a panel sized to its text sits in
        // the corner of a television and reads as a glitch.
        trouble.setLayoutParams(new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        trouble.addView(problem);
        trouble.addView(changeAddress);
        trouble.addView(searchAgain);

        root.addView(web);
        root.addView(video);
        root.addView(trouble);
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
    /** This build's version name, for a screen that has to be read across a room. */
    private String appVersion() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

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
                        trouble.setVisibility(View.GONE);
                        web.setVisibility(View.VISIBLE);
                        web.loadUrl(server + "/tv");
                    });
                    // Now that there is a server to ask. A screen that has just
                    // rediscovered its server is the most likely one in the
                    // house to be running an old build.
                    checkForUpdate();
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
    /**
     * Ask the server for a newer build, and install it if there is one.
     *
     * <p>Called at launch and again the moment a working address is found. The
     * launch call uses whatever address was stored, so on a screen whose server
     * has moved it fails — and if that were the only call, the one build that
     * could fix the screen would be the one it could never fetch. That is not
     * hypothetical: it is exactly how a box ended up stranded.
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
        // With the version on it.
        //
        // A photograph of this screen is how the state of a box across the
        // house gets reported, and without a version it cannot answer the first
        // question worth asking: is this even the build we think it is? A day
        // was lost to an install that silently kept the old app, and this
        // screen said nothing either way.
        problem.setText(message + "\n\nHomesh TV " + appVersion());
        // Also reachable by touch, for a box driven by a phone remote app where
        // the key codes are whatever that app decides to send.
        problem.setClickable(true);
        problem.setFocusable(true);
        problem.setOnClickListener(v -> startActivity(new Intent(this, SetupActivity.class)));
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
        // Any of these, not only MENU.
        //
        // MENU alone was a trap: plenty of remotes do not have that key at all,
        // and the one screen that needs it is the one showing an address that no
        // longer works — so there was no way in from the only place it mattered,
        // and no way out but uninstalling the app. OK, the centre button and the
        // media keys all lead to the same place, and the error screen says so.
        boolean wantsSettings =
                code == KeyEvent.KEYCODE_MENU
                        || code == KeyEvent.KEYCODE_SETTINGS
                        || code == KeyEvent.KEYCODE_PROG_RED
                        || code == KeyEvent.KEYCODE_INFO;

        // While the error is showing, the ordinary buttons mean "fix this" —
        // there is nothing else on that screen for them to do.
        if (trouble.getVisibility() == View.VISIBLE) {
            wantsSettings = wantsSettings
                    || code == KeyEvent.KEYCODE_DPAD_CENTER
                    || code == KeyEvent.KEYCODE_ENTER
                    || code == KeyEvent.KEYCODE_NUMPAD_ENTER
                    || code == KeyEvent.KEYCODE_BUTTON_A
                    || code == KeyEvent.KEYCODE_SPACE;
        }

        if (wantsSettings) {
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
