package com.homesh.tv;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.HttpResponseCache;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.io.File;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;

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
 * <p>What moves it between machines:
 * <ul>
 * <li>Opening asks both at once and takes the PC if it answers within
 *     {@link #QUICK_MS}, the standby otherwise. A few seconds, never the sum of
 *     two timeouts.
 * <li>A page that fails to load, returns a server error, or (on the PC) is
 *     still loading after {@link #SLOW_PAGE_MS} moves to the other machine, if
 *     it answers. Nothing is on screen to lose, so this happens by itself --
 *     but no more than once per {@link Follow#GAP_MS}.
 * <li>Once a minute while it is on screen, a keepalive asks the PC. When the
 *     answer has held for {@link Follow#AGREE} checks, the other machine is
 *     <em>offered</em>, not loaded: the strip says so and a tap moves, and
 *     otherwise the move waits for the next page the person opens.
 * </ul>
 *
 * <p>A page being read is never reloaded unasked. The first version moved on
 * every keepalive that disagreed, and on a weak signal -- every answer late --
 * that meant a reload every few seconds, each one back at the top of the page.
 *
 * <p>An app whose two copies are the same (EXTRA_SAME) is not moved at all.
 * Its page stays at the PC's address and {@link Relay} answers each request
 * from whichever machine answers, so nothing reloads and nobody is asked: the
 * strip only says when the answers are coming from the standby. Everything
 * below about moving and offering is for the other apps, whose standby answers
 * differently and so must be shown as a different place.
 *
 * <p>Above the page is a bar of the kind the Custom Tab had, since these apps
 * once opened in one and the bar is what people learned: a close button back
 * to Homesh Connect's screen, the page's title and which machine it is on, and
 * a menu to reload, go forward, share the page or open it in the browser.
 *
 * <p>Wherever a move happens, the page keeps its place: the same path on the
 * other machine, scrolled to where it was, the inner panes too. The position is
 * kept here rather than left to the page, since the two machines are separate
 * origins and share none of the page's own storage.
 */
public final class AppActivity extends Activity {

    static final String EXTRA_NAME = "name";
    static final String EXTRA_PC = "pc";
    static final String EXTRA_STANDBY = "standby";
    /** The two copies are interchangeable: relay each request, never move the page. */
    static final String EXTRA_SAME = "same";

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
    /** The bar's two lines: the page's title, and which machine it is on. */
    private TextView title;
    private TextView where;
    private ProgressBar loading;
    private ValueCallback<Uri[]> picking;

    private volatile boolean checking;
    private boolean resumed;
    private long lastCheck;

    private final Follow follow = new Follow();
    /** The machine the keepalive says to be on instead, offered and not yet taken. */
    private String offered;
    /** The page showing is the WebView's error page, with no place to keep. */
    private boolean failed;
    /** The strip is saying the page would not load, and offering to try again. */
    private boolean troubled;
    /**
     * Where the page was scrolled, and at which path: captured before each move
     * and at each keepalive, so a page that has since failed still has one.
     */
    private String place;
    private String placePath;
    /** A place to put back once the page at that path has loaded. */
    private String restoring;
    private String restoringPath;

    /** Set for an app whose copies are the same: answers every request, so the page never moves. */
    private Relay relay;

    /** Android 13+: back to the previous page, held only while there is one. */
    private Object backCallback;
    private boolean backHeld;

    /**
     * Every scrolled thing on the page -- the page itself and any pane inside
     * it -- with a selector to find it again. The id where there is one, its
     * position under its parents where there is not.
     */
    private static final String CAPTURE = "(function(){"
            + "function p(e){if(e===document.body)return'body';if(e.id)return'#'+CSS.escape(e.id);"
            + "var q=e.parentElement;if(!q)return null;var s=p(q);if(s===null)return null;"
            + "return s+'>'+e.tagName.toLowerCase()+':nth-child('+(Array.prototype.indexOf.call(q.children,e)+1)+')';}"
            + "var top=document.scrollingElement||document.documentElement;"
            + "var out=[{p:null,x:scrollX,y:scrollY}],all=document.querySelectorAll('body *');"
            + "for(var i=0;i<all.length;i++){var e=all[i];"
            + "if(e!==top&&(e.scrollTop>0||e.scrollLeft>0)){var k=p(e);if(k)out.push({p:k,x:e.scrollLeft,y:e.scrollTop});}}"
            + "return JSON.stringify(out);})()";

    /**
     * Puts a captured place back. The page builds itself after it loads, so it
     * keeps trying for a while until each pane is long enough, then once more
     * in case something above it grew. A touch, a wheel or a key stops it: the
     * person has started reading and the page is theirs.
     */
    private static final String RESTORE = "(function(s){if(typeof s!=='string')return;"
            + "var a=JSON.parse(s),t=Date.now(),stop=false,again=false;"
            + "function quit(){stop=true;}"
            + "['touchstart','wheel','keydown','mousedown'].forEach(function(n){"
            + "addEventListener(n,quit,{once:true,passive:true,capture:true});});"
            + "function go(){if(stop)return;var left=0;a.forEach(function(r){if(r.ok)return;"
            + "if(r.p===null){var m=document.scrollingElement||document.documentElement;"
            + "if(m.scrollHeight-innerHeight>=r.y-1){scrollTo(r.x,r.y);r.ok=Math.abs(scrollY-r.y)<2;}}"
            + "else{var e=document.querySelector(r.p);"
            + "if(e&&e.scrollHeight-e.clientHeight>=r.y-1){e.scrollLeft=r.x;e.scrollTop=r.y;"
            + "r.ok=Math.abs(e.scrollTop-r.y)<2;}}"
            + "if(!r.ok)left++;});"
            + "if(left&&Date.now()-t<10000)setTimeout(go,250);"
            + "else if(!again){again=true;a.forEach(function(r){r.ok=false;});setTimeout(go,1000);}}"
            + "go();})(";

    private final Runnable keepalive = new Runnable() {
        @Override
        public void run() {
            keepAlive();
            ui.postDelayed(this, KEEPALIVE_MS);
        }
    };

    private final Runnable slowPage = () -> {
        if (pc.equals(on)) leave("is slow to load");
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
        strip.setOnClickListener(v -> tapped());
        say("Opening " + name + "…");

        web = new WebView(this);
        View bar = bar();
        web.setBackgroundColor(Color.parseColor("#17130F"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(true);
        CookieManager.getInstance().setAcceptCookie(true);
        if (getIntent().getBooleanExtra(EXTRA_SAME, false)) {
            relay = new Relay(pc, standby, fromStandby -> ui.post(this::sayWhereRelayed));
            web.addJavascriptInterface(new Bridge(), "HomeshRelay");
            // The WebView keeps no cache of what it is handed this way, so the
            // relay's connections keep one: a page's pictures are not fetched
            // again over a weak signal each time the page is drawn.
            try {
                if (HttpResponseCache.getInstalled() == null) {
                    HttpResponseCache.install(new File(getCacheDir(), "relay"), 64L * 1024 * 1024);
                }
            } catch (Exception e) {
                Log.i(TAG, "no cache for the relay: " + e.getMessage());
            }
        }
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        // A download is the browser's job; it knows where files go.
        web.setDownloadListener((url, agent, disposition, mime, length) -> external(url));

        root.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(56)));
        root.addView(loading, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));
        root.addView(strip, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(root);
        keepClearOfSystemBars(root);

        if (state != null && web.restoreState(state) != null && web.getUrl() != null) {
            // Rebuilt by Android, not opened afresh: carry on where it was,
            // and let the keepalive judge whether that is still right.
            on = web.getUrl().startsWith(standby) ? standby : pc;
            showWhere();
            if (relay != null) sayWhereRelayed();
            else if (standby.equals(on)) sayOnStandby();
            else strip.setVisibility(View.GONE);
            holdBack();
        } else {
            open();
        }
    }

    /**
     * The status bar, the navigation bar and the keyboard are kept off the
     * page here, as padding, and not passed on to it.
     *
     * <p>From Android 15 an app is drawn edge to edge, under the system bars,
     * and the WebView was told so: a page that pads its header by
     * env(safe-area-inset-top), as SpaXepedition's and Travorg's do for when
     * they are added to a home screen, padded it by the status bar's height
     * although the page sits under this activity's bar, well clear of it --
     * a header twice the height it needs. Consumed here, the page sees no
     * inset and lays out as it would in a browser tab. The keyboard is part of
     * it because edge to edge also ends the window resizing for it.
     */
    private void keepClearOfSystemBars(View root) {
        if (Build.VERSION.SDK_INT < 30) return;
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars()
                    | WindowInsets.Type.displayCutout());
            android.graphics.Insets ime = insets.getInsets(WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            return WindowInsets.CONSUMED;
        });
    }

    /**
     * The bar over the page: close, title and machine, menu. Built in code,
     * like the rest of this app, so it needs no layout or library.
     */
    private View bar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(Color.parseColor("#17130F"));

        ImageButton close = icon(android.R.drawable.ic_menu_close_clear_cancel, "Close " + name);
        // Back to Homesh Connect's screen, whatever the page's own history.
        close.setOnClickListener(v -> finish());

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        title = new TextView(this);
        title.setText(name);
        title.setTextColor(Color.parseColor("#F2EBE2"));
        title.setTextSize(16);
        title.setSingleLine(true);
        title.setEllipsize(android.text.TextUtils.TruncateAt.END);
        where = new TextView(this);
        where.setTextColor(Color.parseColor("#A79B8D"));
        where.setTextSize(12);
        where.setSingleLine(true);
        text.addView(title);
        text.addView(where);

        ImageButton more = icon(android.R.drawable.ic_menu_more, "More");
        more.setOnClickListener(this::menu);

        bar.addView(close, new LinearLayout.LayoutParams(dp(56), dp(56)));
        bar.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        bar.addView(more, new LinearLayout.LayoutParams(dp(56), dp(56)));

        loading = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        loading.setMax(100);
        loading.setVisibility(View.INVISIBLE);
        showWhere();
        return bar;
    }

    private ImageButton icon(int drawable, String label) {
        ImageButton b = new ImageButton(this);
        b.setImageResource(drawable);
        b.setColorFilter(Color.parseColor("#F2EBE2"));
        b.setBackgroundColor(Color.TRANSPARENT);
        b.setContentDescription(label);
        return b;
    }

    private void menu(View anchor) {
        PopupMenu m = new PopupMenu(this, anchor);
        if (web.canGoForward()) m.getMenu().add(0, 1, 0, "Forward");
        m.getMenu().add(0, 2, 0, "Reload");
        m.getMenu().add(0, 3, 0, "Share link");
        m.getMenu().add(0, 4, 0, "Open in browser");
        m.setOnMenuItemClickListener(item -> {
            String url = web.getUrl();
            switch (item.getItemId()) {
                case 1: web.goForward(); break;
                case 2: web.reload(); break;
                case 3:
                    if (url != null) {
                        Intent send = new Intent(Intent.ACTION_SEND).setType("text/plain")
                                .putExtra(Intent.EXTRA_TEXT, url);
                        try {
                            startActivity(Intent.createChooser(send, null));
                        } catch (Exception e) {
                            Log.i(TAG, "nothing would share " + url);
                        }
                    }
                    break;
                case 4: if (url != null) external(url); break;
                default: return false;
            }
            return true;
        });
        m.show();
    }

    /** The bar's second line: the machine the answers are coming from. */
    private void showWhere() {
        if (where == null) return;
        boolean fromStandby = relay != null ? relay.away() : standby.equals(on);
        where.setText(on == null && relay == null ? "Opening…"
                : fromStandby ? "On the standby" : "On your PC");
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
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
        remember();
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

    /**
     * Back as the previous page, the way a browser does it.
     *
     * <p>onBackPressed above is enough up to Android 15. An app built for
     * Android 16 is not sent it at all: back goes to whatever has registered
     * for it, and with nothing registered it closes the screen -- which is how
     * back came to leave SpaXepedition for the launcher instead of turning the
     * page back. So a callback is registered, and only while the page has
     * somewhere to go back to; on the first page back still closes the screen,
     * with the system's own animation.
     */
    private void holdBack() {
        if (Build.VERSION.SDK_INT < 33 || web == null) return;
        boolean want = web.canGoBack();
        if (want == backHeld) return;
        if (backCallback == null) {
            backCallback = (OnBackInvokedCallback) () -> {
                if (web.canGoBack()) web.goBack();
                holdBack();
            };
        }
        OnBackInvokedDispatcher d = getOnBackInvokedDispatcher();
        if (want) {
            d.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT,
                    (OnBackInvokedCallback) backCallback);
        } else {
            d.unregisterOnBackInvokedCallback((OnBackInvokedCallback) backCallback);
        }
        backHeld = want;
    }

    /** Ask both machines at once and open the app on the one to use. */
    private void open() {
        if (relay != null) {
            openRelayed();
            return;
        }
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
                        strip.setOnClickListener(w -> tapped());
                        say("Opening " + name + "…");
                        open();
                    });
                    return;
                }
                show(go, null);
            });
        }).start();
    }

    /**
     * Opening an app that is relayed: always at the PC's address. Asking both
     * first only decides where the first request goes, so a PC that is off or
     * swamped costs the couple of seconds of asking, not a timeout per request.
     */
    private void openRelayed() {
        checking = true;
        new Thread(() -> {
            String first = Reach.pick(new String[] {pc}, new String[] {standby},
                    url -> Reach.answers(url, PATIENT_MS), QUICK_MS, PATIENT_MS);
            lastCheck = SystemClock.elapsedRealtime();
            relay.setAway(standby.equals(first));
            checking = false;
            ui.post(() -> {
                on = pc;
                web.loadUrl(pc);
            });
        }).start();
    }

    /**
     * The minute's check for a relayed app. Nothing is reloaded whatever it
     * finds: it only changes where the next request goes first, and only once
     * the PC has answered the same way twice running (Follow).
     */
    private void keepAliveRelayed() {
        if (checking) return;
        checking = true;
        new Thread(() -> {
            boolean pcUp = Reach.answers(pc, QUICK_MS);
            lastCheck = SystemClock.elapsedRealtime();
            boolean onPc = !relay.away();
            if (follow.heard(onPc, pcUp)) {
                if (!onPc) relay.setAway(false);
                else if (Reach.answers(standby, PATIENT_MS)) relay.setAway(true);
                follow.moved(SystemClock.elapsedRealtime());
            }
            checking = false;
        }).start();
    }

    private void sayWhereRelayed() {
        showWhere();
        if (troubled) return;
        if (relay.away()) {
            say(name + " is answering from the standby while your PC is slow or off. "
                    + "It goes back by itself; tap to check now.");
        } else {
            strip.setVisibility(View.GONE);
        }
    }

    /** The page's writes, handed over by Relay.SHIM. */
    private final class Bridge {
        @JavascriptInterface
        public void send(int id, String method, String url, String headers, String body) {
            new Thread(() -> {
                String answer = relay.send(method, url, headers, body);
                ui.post(() -> {
                    if (web != null) {
                        web.evaluateJavascript("window.__relayDone&&__relayDone(" + id + "," + answer + ")", null);
                    }
                });
            }).start();
        }
    }

    /** The minute's check: is the app on the machine it should be on? */
    private void keepAlive() {
        if (relay != null) {
            keepAliveRelayed();
            return;
        }
        if (checking || on == null) return;
        checking = true;
        remember();
        final String was = on;
        new Thread(() -> {
            // The PC is the question either way. On it, a PC that cannot answer
            // quickly is one to leave; on the standby, one that can is one to
            // go back to. Either way only once it has said so more than once.
            boolean pcUp = Reach.answers(pc, QUICK_MS);
            lastCheck = SystemClock.elapsedRealtime();
            boolean onPc = pc.equals(was);
            boolean away = follow.heard(onPc, pcUp);
            boolean otherUp = !away || !onPc || Reach.answers(standby, PATIENT_MS);
            checking = false;
            ui.post(() -> {
                if (!was.equals(on)) return;
                if (away && otherUp) offer(onPc ? standby : pc);
                else withdraw();
            });
        }).start();
    }

    /**
     * The other machine is the better one now. Said, not done: a page being
     * read stays as it is until the person opens another or taps the strip.
     * Only a page that is not there -- the error page -- is replaced at once.
     */
    private void offer(String to) {
        offered = to;
        if (failed) {
            go(to, null);
            return;
        }
        if (standby.equals(to)) {
            say("Your PC is not answering. Tap to move " + name + " to the standby, "
                    + "or it moves when you open another page.");
        } else {
            say("Your PC is back. Tap to move " + name + " to it, "
                    + "or it moves when you open another page.");
        }
    }

    /** The offer no longer holds: the machine it is on is the right one again. */
    private void withdraw() {
        if (offered == null) return;
        offered = null;
        if (standby.equals(on)) sayOnStandby();
        else strip.setVisibility(View.GONE);
    }

    /** The strip: take what it offers, or check the PC there and then. */
    private void tapped() {
        if (offered != null) move(offered, null);
        else keepAlive();
    }

    /**
     * This page failed on the machine it is on; try the other one, if it
     * answers and the last move was not a moment ago. Two machines both
     * failing would otherwise hand the page back and forth for ever.
     */
    private void leave(String why) {
        if (on == null) return;
        if (checking) {
            // A keepalive is out; its answer may not cover this. Ask again
            // once it is back, rather than leave the error page standing.
            ui.postDelayed(() -> {
                if (failed) leave(why);
            }, 1000);
            return;
        }
        final String from = on;
        final String to = pc.equals(from) ? standby : pc;
        if (!follow.mayMove(SystemClock.elapsedRealtime())) {
            offered = null;
            troubled = true;
            say(name + " is having trouble loading. Tap to try again.");
            strip.setOnClickListener(v -> {
                strip.setOnClickListener(w -> tapped());
                web.reload();
            });
            return;
        }
        checking = true;
        new Thread(() -> {
            boolean up = Reach.answers(to, PATIENT_MS);
            checking = false;
            if (up) ui.post(() -> {
                if (!from.equals(on)) return;
                move(to, standby.equals(to)
                        ? "Your PC " + why + ", so " + name + " moved to the standby."
                        : "The standby " + why + ", so " + name + " moved back to your PC.");
            });
        }).start();
    }

    /**
     * Notes where the page is scrolled, while it is a real page. Called before
     * a move and at each keepalive, so that a page which then fails -- and
     * shows only the error page -- still has a place to go back to.
     */
    private void remember() {
        if (failed || on == null) return;
        final String path = pathOf(web.getUrl());
        if (path == null) return;
        web.evaluateJavascript(CAPTURE, json -> {
            if (json == null || "null".equals(json)) return;
            place = json;
            placePath = path;
        });
    }

    /** The same page, on the other machine, at the same place. */
    private void move(String to, String message) {
        final String path = pathOf(web.getUrl());
        if (failed || path == null) {
            go(to, message);
            return;
        }
        // The place first, then the load: the capture has to read the page
        // that is about to go.
        web.evaluateJavascript(CAPTURE, json -> {
            if (json != null && !"null".equals(json)) {
                place = json;
                placePath = path;
            }
            go(to, message);
        });
    }

    private void go(String to, String message) {
        String path = pathOf(web.getUrl());
        if (failed && placePath != null) path = placePath;
        Log.i(TAG, "moving " + name + (to.equals(pc) ? " to the PC" : " to the standby"));
        follow.moved(SystemClock.elapsedRealtime());
        offered = null;
        if (place != null && path != null && path.equals(placePath)) {
            restoring = place;
            restoringPath = path;
        }
        show(to, path);
        if (message == null) return;
        say(message);
        // The message is news for a few seconds; after that the strip says
        // only where the app is, which is what stays true.
        ui.postDelayed(() -> {
            if (to.equals(on) && offered == null) {
                if (standby.equals(on)) sayOnStandby();
                else strip.setVisibility(View.GONE);
            }
        }, 6000);
    }

    /** A relayed page neither machine would give: said, with a tap to ask again. */
    private void notAnswering() {
        troubled = true;
        say(name + " is not answering, on the PC or on the standby. Tap to try again.");
        strip.setOnClickListener(v -> {
            strip.setOnClickListener(w -> tapped());
            troubled = false;
            say("Opening " + name + "…");
            web.reload();
        });
    }

    /** The part of an address after the machine, or null for another site. */
    private String pathOf(String url) {
        if (url == null || on == null || !url.startsWith(on)) return null;
        return url.substring(on.length());
    }

    private void show(String base, String path) {
        on = base;
        showWhere();
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
            if (!ours(url)) {
                external(url);
                return true;
            }
            // A page the person asked for: the moment to take up an offered
            // move, since the view is changing anyway.
            String path = pathOf(url);
            if (offered != null && path != null) {
                String to = offered;
                follow.moved(SystemClock.elapsedRealtime());
                offered = null;
                show(to, path);
                return true;
            }
            return false;
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            return relay == null ? null : relay.fetch(request, request.isForMainFrame());
        }

        @Override
        public void onPageStarted(WebView view, String url, Bitmap icon) {
            failed = false;
            if (relay != null) return;  // (one address, whichever machine answers: nothing to follow)
            // Back past a move lands on a page from the other machine. Follow
            // it, so the strip and what a failure does are about the machine
            // actually showing the page.
            String there = url == null ? null
                    : url.startsWith(standby) ? standby : url.startsWith(pc) ? pc : null;
            if (there != null && !there.equals(on)) {
                on = there;
                showWhere();
                offered = null;
                if (standby.equals(on)) sayOnStandby();
                else strip.setVisibility(View.GONE);
            }
            ui.removeCallbacks(slowPage);
            if (resumed && pc.equals(on)) ui.postDelayed(slowPage, SLOW_PAGE_MS);
        }

        @Override
        public void doUpdateVisitedHistory(WebView view, String url, boolean reload) {
            // Every page added, hash links included: SpaXepedition moves
            // between its pages by #, so most of its history is made here.
            holdBack();
        }

        @Override
        public void onPageFinished(WebView view, String url) {
            holdBack();
            ui.removeCallbacks(slowPage);
            if (!failed && troubled) {
                troubled = false;
                strip.setOnClickListener(v -> tapped());
                if (relay == null && standby.equals(on)) sayOnStandby();
                else strip.setVisibility(View.GONE);
            }
            if (!failed && relay != null) sayWhereRelayed();
            if (failed || restoring == null) return;
            String path = pathOf(url);
            if (path != null && path.equals(restoringPath)) {
                web.evaluateJavascript(RESTORE + restoring + ")", null);
            }
            restoring = null;
            restoringPath = null;
        }

        @Override
        @SuppressWarnings("deprecation")
        public void onReceivedError(WebView view, int code, String description, String url) {
            // Called for the page itself only; a missing image is not a reason
            // to change machines.
            Log.i(TAG, "page failed: " + description);
            failed = true;
            ui.removeCallbacks(slowPage);
            if (relay != null) {
                // The relay already asked both: neither answered.
                notAnswering();
                return;
            }
            leave("is not answering");
        }

        @Override
        public void onReceivedHttpError(WebView view, WebResourceRequest request,
                                        WebResourceResponse response) {
            if (request.isForMainFrame() && response.getStatusCode() >= 500) {
                failed = true;
                if (relay != null) notAnswering();
                else leave("returned an error");
            }
        }
    }

    private final class Chrome extends WebChromeClient {
        @Override
        public void onReceivedTitle(WebView view, String t) {
            title.setText(t == null || t.isEmpty() || t.startsWith("http") ? name : t);
        }

        @Override
        public void onProgressChanged(WebView view, int progress) {
            loading.setProgress(progress);
            loading.setVisibility(progress < 100 ? View.VISIBLE : View.INVISIBLE);
        }

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
