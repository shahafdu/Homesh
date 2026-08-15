package com.homesh.tv;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
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

        problem = new TextView(this);
        problem.setTextColor(Color.WHITE);
        problem.setTextSize(18);
        problem.setPadding(64, 64, 64, 64);
        problem.setVisibility(View.GONE);

        root.addView(web);
        root.addView(problem);
        setContentView(root);

        immersive();
        web.loadUrl(server + "/tv");
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
        if (web != null) {
            web.destroy();
        }
        super.onDestroy();
    }
}
