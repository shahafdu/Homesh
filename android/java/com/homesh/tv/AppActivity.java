package com.homesh.tv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Another app beside Homesh, kept on whichever machine is answering.
 *
 * <p>An app with a copy on the standby -- Travorg, say -- should follow the PC
 * the way the rest of the house does: on the PC when it is up, on the standby
 * when it is off or too loaded to answer, and back on the PC once it recovers,
 * without anybody choosing. That last part rules out the Custom Tab Homesh
 * itself opens in. Once the tab is showing, this app is behind it and Android
 * (15 onwards) will not let an app behind another one start anything, so a tab
 * can be chosen well when it opens and never corrected afterwards.
 *
 * <p>So these apps open here, in a WebView this activity owns, where moving to
 * the other machine is a loadUrl. What a WebView costs is what PhoneActivity
 * explains: no passkeys, its own cookie jar, and Google refusing to sign in
 * inside one. Homesh needs passkeys, so it stays in its tab. These apps sign in
 * with a PIN, once, and the WebView keeps the cookie; an app that one day
 * needs Google's sign-in will need a different answer.
 *
 * <p>Three things move it between machines:
 * <ul>
 * <li>Opening asks both at once and takes the PC if it answers within
 *     {@link #QUICK_MS}, the standby otherwise. A few seconds, never the sum of
 *     two timeouts.
 * <li>A page on the PC that fails to load, returns a server error, or is still
 *     loading after {@link #SLOW_PAGE_MS} moves to the standby, if it answers.
 * <li>Once a minute while it is on screen, a keepalive asks the PC. On the PC,
 *     a PC that does not answer quickly moves it to the standby; on the
 *     standby, a PC that answers moves it back.
 * </ul>
 * The page keeps its place: the same path is opened on the other machine.
 */
public final class AppActivity extends Activity {

    static final String EXTRA_NAME = "name";
    static final String EXTRA_PC = "pc";
    static final String EXTRA_STANDBY = "standby";

    private static final String TAG = "HomeshApp";

    /** How long a PC may take to answer before the standby is used instead. */
    private static final int QUICK_MS = 2500;
    /** The most any check waits, for either machine. */
    private static final int PATIENT_MS = 6000;
    private static final long KEEPALIVE_MS = 60_000;
    /** A page on the PC still not loaded after this is a PC too busy to use. */
    private static final long SLOW_PAGE_MS = 8000;
    private static final int PICK_FILE = 7;

    private final Handler ui = new Handler(Looper.getMainLooper());

    private String name;
    private String pc;
    private String standby;
    /** The machine showing the app now: pc, standby, or null before the first. */
    private String on;

    private WebView web;
    private TextView strip;
    private ValueCallback<Uri[]> picking;

    private volatile boolean checking;
    private boolean resumed;
    private long lastCheck;

    private final Runnable keepalive = new Runnable() {
        @Override
        public void run() {
            keepAlive();
            ui.postDelayed(this, KEEPALIVE_MS);
        }
    };

    private final Runnable slowPage = () -> {
        if (pc.equals(on)) moveToStandby("is slow to load");
    };

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        name = getIntent().getStringExtra(EXTRA_NAME);
        pc = trim(getIntent().getStringExtra(EXTRA_PC));
        standby = trim(getIntent().getStringExtra(EXTRA_STANDBY));
        if (name == null || pc == null || standby == null) {
            finish();
            return;
        }

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#17130F"));

        // Said in a strip rather than a toast: being on the standby is a state,
        // not an event, and it matters for as long as it lasts. Tapping it
        // checks the PC there and then rather than waiting for the minute.
        strip = new TextView(this);
        strip.setTextColor(Color.parseColor("#F2EBE2"));
        strip.setBackgroundColor(Color.parseColor("#3A2E22"));
        strip.setTextSize(13);
        strip.setGravity(Gravity.CENTER_VERTICAL);
        strip.setPadding(32, 18, 32, 18);
        strip.setOnClickListener(v -> keepAlive());
        say("Opening " + name + "…");

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#17130F"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        // A download is the browser's job; it knows where files go.
        web.setDownloadListener((url, agent, disposition, mime, length) -> external(url));

        root.addView(strip, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);

        if (state != null && web.restoreState(state) != null && web.getUrl() != null) {
            // Rebuilt by Android, not opened afresh: carry on where it was,
            // and let the keepalive judge whether that is still right.
            on = web.getUrl().startsWith(standby) ? standby : pc;
            if (standby.equals(on)) sayOnStandby();
            else strip.setVisibility(View.GONE);
        } else {
            open();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        web.onResume();
        // Back after a while away -- the phone slept, another app was used --
        // is when the PC is most likely to have changed, so ask now.
        long due = KEEPALIVE_MS - (SystemClock.elapsedRealtime() - lastCheck);
        if (on != null && due <= 0) keepAlive();
        ui.postDelayed(keepalive, Math.max(due, 1000));
    }

    @Override
    protected void onPause() {
        // Only while it is on screen. A check every minute for an app nobody
        // is looking at is battery spent on nothing.
        resumed = false;
        ui.removeCallbacks(keepalive);
        ui.removeCallbacks(slowPage);
        web.onPause();
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        web.saveState(out);
    }

    @Override
    protected void onDestroy() {
        ui.removeCallbacksAndMessages(null);
        if (web != null) web.destroy();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    /** Ask both machines at once and open the app on the one to use. */
    private void open() {
        checking = true;
        new Thread(() -> {
            String go = Reach.pick(new String[] {pc}, new String[] {standby},
                    url -> Reach.answers(url, PATIENT_MS), QUICK_MS, PATIENT_MS);
            lastCheck = SystemClock.elapsedRealtime();
            checking = false;
            ui.post(() -> {
                if (go == null) {
                    say(name + " is not answering, on the PC or on the standby. "
                            + "Tap to try again.");
                    strip.setOnClickListener(v -> {
                        strip.setOnClickListener(w -> keepAlive());
                        say("Opening " + name + "…");
                        open();
                    });
                    return;
                }
                show(go, null);
            });
        }).start();
    }

    /** The minute's check: is the app on the machine it should be on? */
    private void keepAlive() {
        if (checking || on == null) return;
        checking = true;
        final String was = on;
        new Thread(() -> {
            // The PC is the question either way. On it, a PC that cannot answer
            // quickly is one to leave; on the standby, one that can is one to
            // go back to.
            boolean pcUp = Reach.answers(pc, QUICK_MS);
            lastCheck = SystemClock.elapsedRealtime();
            if (pc.equals(was) && !pcUp) {
                boolean standbyUp = Reach.answers(standby, PATIENT_MS);
                checking = false;
                if (standbyUp) ui.post(() -> {
                    if (pc.equals(on)) move(standby, "Your PC is not answering, so "
                            + name + " moved to the standby.");
                });
                return;
            }
            checking = false;
            if (standby.equals(was) && pcUp) ui.post(this::returnToPc);
            else if (standby.equals(was)) ui.post(this::sayOnStandby);
        }).start();
    }

    /**
     * Back to the PC, unless somebody is typing.
     *
     * <p>Moving reloads the page, and a reload takes whatever was half typed
     * into a box with it. Waiting a minute costs nothing; losing a paragraph
     * of a trip plan is the kind of thing that makes an app feel unsafe.
     */
    private void returnToPc() {
        if (!standby.equals(on)) return;
        web.evaluateJavascript(
                "(function(){var e=document.activeElement;"
                        + "return !!e&&(e.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(e.tagName));})()",
                typing -> {
                    if ("true".equals(typing)) {
                        Log.i(TAG, "PC is back; waiting until nothing is being typed");
                        return;
                    }
                    if (standby.equals(on)) move(pc, "Your PC is back, so " + name + " moved back to it.");
                });
    }

    /** The PC failed a page; go to the standby if it answers. */
    private void moveToStandby(String why) {
        if (checking || !pc.equals(on)) return;
        checking = true;
        new Thread(() -> {
            boolean up = Reach.answers(standby, PATIENT_MS);
            checking = false;
            if (up) ui.post(() -> {
                if (pc.equals(on)) move(standby, "Your PC " + why + ", so " + name
                        + " moved to the standby.");
            });
        }).start();
    }

    /** The same page, on the other machine. */
    private void move(String to, String message) {
        String from = on;
        String here = web.getUrl();
        String path = here != null && from != null && here.startsWith(from)
                ? here.substring(from.length()) : null;
        Log.i(TAG, "moving " + name + (to.equals(pc) ? " to the PC" : " to the standby"));
        show(to, path);
        say(message);
        // The message is news for a few seconds; after that the strip says
        // only where the app is, which is what stays true.
        ui.postDelayed(() -> {
            if (to.equals(on)) {
                if (standby.equals(on)) sayOnStandby();
                else strip.setVisibility(View.GONE);
            }
        }, 6000);
    }

    private void show(String base, String path) {
        on = base;
        String url = path == null || path.isEmpty() ? base : base + path;
        web.loadUrl(url);
        if (standby.equals(base)) sayOnStandby();
        else strip.setVisibility(View.GONE);
    }

    private void sayOnStandby() {
        say(name + " is on the standby while your PC is not answering. "
                + "It moves back by itself; tap to check now.");
    }

    private void say(String text) {
        strip.setText(text);
        strip.setVisibility(View.VISIBLE);
    }

    /** Opened in the phone's browser: a link out of the app, or a download. */
    private void external(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (Exception e) {
            Log.i(TAG, "nothing would open " + url);
        }
    }

    private boolean ours(String url) {
        return url.startsWith(pc) || url.startsWith(standby);
    }

    private static String trim(String url) {
        if (url == null) return null;
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        return url.isEmpty() ? null : url;
    }

    private final class Client extends WebViewClient {
        @Override
        @SuppressWarnings("deprecation")
        public boolean shouldOverrideUrlLoading(WebView view, String url) {
            // The app's own pages stay here; anything else -- a map, a booking
            // site -- belongs in the browser, signed in as the person is there.
            if (ours(url)) return false;
            external(url);
            return true;
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap icon) {
            ui.removeCallbacks(slowPage);
            if (resumed && pc.equals(on)) ui.postDelayed(slowPage, SLOW_PAGE_MS);
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            ui.removeCallbacks(slowPage);
        }

        @Override
        @SuppressWarnings("deprecation")
        public void onReceivedError(WebView view, int code, String description, String url) {
            // Called for the page itself only; a missing image is not a reason
            // to change machines.
            Log.i(TAG, "page failed: " + description);
            if (pc.equals(on)) moveToStandby("is not answering");
            else if (standby.equals(on)) keepAlive();
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                        WebResourceResponse response) {
            if (request.isForMainFrame() && response.getStatusCode() >= 500 && pc.equals(on)) {
                moveToStandby("returned an error");
            }
        }
    }

    private final class Chrome extends WebChromeClient {
        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                         FileChooserParams params) {
            if (picking != null) picking.onReceiveValue(null);
            picking = callback;
            try {
                startActivityForResult(params.createIntent(), PICK_FILE);
            } catch (Exception e) {
                picking = null;
                return false;
            }
            return true;
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        if (request == PICK_FILE && picking != null) {
            picking.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result, data));
            picking = null;
            return;
        }
        super.onActivityResult(request, result, data);
    }
}
