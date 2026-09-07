package com.homesh.tv;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

import android.app.Activity;

/**
 * Getting to Homesh from a phone, including when the way there is down.
 *
 * <p>A phone reaches this server over Tailscale when it is away from the house.
 * Android kills that VPN to save battery, and the failure is silent and total:
 * the address stops resolving, the browser shows its own cached page, and it
 * reads as the server being down. It has cost days. The fix each time was to
 * open the Tailscale app, which reconnects on its own — and a web page, which is
 * what could not be loaded, cannot open another app.
 *
 * <p>So this is small on purpose. It is not the interface; it is the way in.
 * It checks whether the server can be reached, offers the one action that fixes
 * it when it cannot, and then hands off to the browser.
 *
 * <p><b>Rendered in a Custom Tab, not a WebView, and not by throwing you into
 * Chrome.</b> All three were on the table and only one of them works.
 *
 * <p>A WebView is the obvious choice and the wrong one. It is a rendering
 * engine with its own cookie jar and no browser around it: WebAuthn does not
 * work there, so a passkey cannot be used, and the session is not the one
 * already signed in on this phone. An app that wrapped the site that way would
 * look like a real app and be impossible to sign into — the worst of both,
 * because it fails only at the point somebody has committed to using it.
 *
 * <p>Handing off with a plain ACTION_VIEW fixes the sign-in and loses the app:
 * Chrome opens as a separate task with its own address bar, this screen
 * disappears, and it stops feeling like anything was opened at all.
 *
 * <p>A Custom Tab is the same Chrome — same engine, same cookies, same
 * passkeys, so sign-in simply works — rendered inside this task with our own
 * toolbar and a close button that comes back here. It needs no library: the
 * protocol is an ACTION_VIEW intent carrying a few documented extras, which
 * suits an app that has managed to avoid every dependency so far.
 */
public final class PhoneActivity extends Activity {

    private static final String TAG = "HomeshPhone";
    private static final String PREFS = "homesh.phone";
    private static final String KEY_ORIGIN = "origin";

    /** Tailscale's own package. Declared in <queries> so it can be seen at all. */
    private static final String TAILSCALE = "com.tailscale.ipn";

    private TextView message;
    private Button openHomesh;
    private Button openTailscale;
    private Button searchAgain;
    private Button changeAddress;
    private LinearLayout root;

    /** The address that answered, kept so the button can reopen without asking again. */
    private String reachedAt;

    /** Set while a check is running, so returning to the app does not start a second. */
    private volatile boolean checking;

    /**
     * Whether Homesh has been opened over this screen already.
     *
     * <p>A Custom Tab lives in this task, so closing it lands back here and
     * onResume fires again. Without this the check would run and open a second
     * tab, and closing that one would open a third: an app that cannot be shut.
     * Coming back is a deliberate act, so what should be waiting is the screen
     * and a button, not another tab.
     */
    private boolean handedOff;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setPadding(64, 64, 64, 64);
        root.setBackgroundColor(Color.parseColor("#17130F"));

        TextView brand = new TextView(this);
        brand.setText("Homesh");
        brand.setTextColor(Color.parseColor("#F2EBE2"));
        brand.setTextSize(28);
        brand.setPadding(0, 0, 0, 24);

        message = new TextView(this);
        message.setText("Looking for your server…");
        message.setTextColor(Color.parseColor("#A79B8D"));
        message.setTextSize(16);
        message.setPadding(0, 0, 0, 28);

        openHomesh = button("Open Homesh", v -> {
            if (reachedAt != null) open(reachedAt);
            else check(false);
        });
        openTailscale = button("Open Tailscale", v -> launchTailscale());
        searchAgain = button("Search on this network", v -> check(true));
        changeAddress = button("Change the address",
                v -> startActivity(new Intent(this, SetupActivity.class)));

        root.addView(brand);
        root.addView(message);
        root.addView(openHomesh);
        root.addView(openTailscale);
        root.addView(searchAgain);
        root.addView(changeAddress);
        setContentView(root);

        showActions(false);
    }

    @Override
    protected void onResume() {
        super.onResume();

        if (handedOff) {
            // Back from the tab, on purpose. Offer to open it again rather than
            // doing it unasked, which would make closing it impossible.
            handedOff = false;
            message.setText("Homesh was open. Closed it?");
            openHomesh.setVisibility(View.VISIBLE);
            showActions(true);
            return;
        }

        // The whole point of the Tailscale button: coming back from it should
        // simply continue, not ask to be told again that it worked.
        check(false);
    }

    private Button button(String label, View.OnClickListener onClick) {
        Button b = new Button(this);
        b.setText(label);
        b.setOnClickListener(onClick);
        return b;
    }

    private void showActions(boolean show) {
        int visibility = show ? View.VISIBLE : View.GONE;
        openTailscale.setVisibility(
                show && tailscaleInstalled() ? View.VISIBLE : View.GONE);
        searchAgain.setVisibility(visibility);
        changeAddress.setVisibility(visibility);
        // Only offered once something has answered; otherwise it is a button
        // whose only outcome is the error already on the screen.
        if (!show || reachedAt == null) openHomesh.setVisibility(View.GONE);
    }

    /**
     * Find an address that answers, and go there.
     *
     * <p>Both are tried because they answer different questions. The house
     * address is right at home and unreachable anywhere else; the tailnet
     * address is the reverse. Which one applies is a property of where the phone
     * is standing, which the phone can simply find out rather than ask about.
     */
    private void check(boolean sweep) {
        if (checking) return;
        checking = true;

        new Thread(() -> {
            SharedPreferences prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            String lan = Prefs.server(this);
            String origin = prefs.getString(KEY_ORIGIN, null);

            String reachable = firstThatAnswers(origin, lan);

            if (reachable == null && sweep) {
                runOnUiThread(() -> message.setText("Searching this network…"));
                String found = Discovery.find();
                if (found != null) {
                    Prefs.setServer(this, found);
                    reachable = found;
                }
            }

            if (reachable != null) {
                // Learned while it is possible to learn it, so the next time the
                // phone is away from home it already knows where to look.
                rememberOrigin(reachable, prefs);
                String go = reachable;
                runOnUiThread(() -> open(go));
            } else {
                runOnUiThread(this::cannotReach);
            }
            checking = false;
        }).start();
    }

    private String firstThatAnswers(String... addresses) {
        for (String address : addresses) {
            if (address != null && !address.isEmpty() && Server.reachable(address)) {
                Log.i(TAG, "reached " + address);
                return address;
            }
        }
        return null;
    }

    /** Ask the server where else it can be reached, and keep the answer. */
    private void rememberOrigin(String base, SharedPreferences prefs) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(base + "/tv.address").openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0 && body.length() < 4096) {
                    body.append(new String(buf, 0, n, "UTF-8"));
                }
            }

            JSONObject json = new JSONObject(body.toString());
            String origin = json.optString("origin", null);
            String lan = json.optString("lan", null);
            if (origin != null && !origin.isEmpty() && !"null".equals(origin)) {
                prefs.edit().putString(KEY_ORIGIN, origin).apply();
            }
            if (lan != null && !lan.isEmpty() && !"null".equals(lan)) {
                Prefs.setServer(this, lan);
            }
        } catch (Exception e) {
            // Knowing the other address is a convenience, not a requirement.
            Log.i(TAG, "could not learn the other address: " + e.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** Open Homesh over this screen, in a tab that belongs to this app. */
    private void open(String base) {
        reachedAt = base;
        message.setText("Opening Homesh…");
        showActions(false);
        try {
            startActivity(browserIntent(Uri.parse(base)));
            handedOff = true;
        } catch (Exception e) {
            message.setText("No browser on this phone would open " + base + ".");
            showActions(true);
        }
    }

    /**
     * A Custom Tab where one is available, an ordinary browser where it is not.
     *
     * <p>The extras are the Custom Tabs protocol, which is a published intent
     * contract rather than a library: naming a session binder is what marks the
     * request as a tab at all, and the rest is how it should look. A browser
     * that does not understand them ignores them and opens the page normally,
     * so this degrades to what it did before instead of failing.
     */
    private Intent browserIntent(Uri uri) {
        Intent intent = new Intent(Intent.ACTION_VIEW, uri);

        String browser = customTabsBrowser();
        if (browser == null) return intent;

        intent.setPackage(browser);

        // A null session: this app is not connecting to the service for
        // warm-up or navigation callbacks, it only wants the tab. The extra
        // must be present regardless -- it is what distinguishes a Custom Tab
        // request from a plain "open this in a browser".
        Bundle session = new Bundle();
        session.putBinder("android.support.customtabs.extra.SESSION", null);
        intent.putExtras(session);

        // Homesh's own colour on the toolbar, so the tab reads as part of this
        // app rather than as somebody else's browser that opened over it.
        intent.putExtra("android.support.customtabs.extra.TOOLBAR_COLOR", 0xFF17130F);
        intent.putExtra("android.support.customtabs.extra.TITLE_VISIBILITY", 1);
        // 2 is dark. The app is dark, and a white toolbar over it is a seam.
        intent.putExtra("androidx.browser.customtabs.extra.COLOR_SCHEME", 2);
        return intent;
    }

    /**
     * A browser that can show a Custom Tab, preferring the default one.
     *
     * <p>The default matters: it is where the passkey and the signed-in session
     * live. Choosing a different installed browser because it happened to come
     * first would open Homesh somewhere nobody is signed in, which is the exact
     * failure a WebView would have caused.
     */
    private String customTabsBrowser() {
        PackageManager pm = getPackageManager();

        // What would ordinarily open a web page -- the user's own choice.
        ResolveInfo preferred = pm.resolveActivity(
                new Intent(Intent.ACTION_VIEW, Uri.parse("https://example.com")),
                PackageManager.MATCH_DEFAULT_ONLY);
        if (preferred != null && supportsCustomTabs(preferred.activityInfo.packageName)) {
            return preferred.activityInfo.packageName;
        }

        for (ResolveInfo candidate : pm.queryIntentActivities(
                new Intent(Intent.ACTION_VIEW, Uri.parse("https://example.com")), 0)) {
            if (supportsCustomTabs(candidate.activityInfo.packageName)) {
                return candidate.activityInfo.packageName;
            }
        }
        return null;
    }

    private boolean supportsCustomTabs(String pkg) {
        Intent service = new Intent("android.support.customtabs.action.CustomTabsService");
        service.setPackage(pkg);
        return getPackageManager().resolveService(service, 0) != null;
    }

    private void cannotReach() {
        message.setText(
                tailscaleInstalled()
                        ? "Cannot reach your server.\n\nAway from home, Homesh is reached "
                          + "over Tailscale, and Android stops it to save battery. Opening "
                          + "Tailscale reconnects it — then come back here."
                        : "Cannot reach your server.\n\nAt home, check it is switched on. "
                          + "Away from home, Homesh is reached over Tailscale.");
        showActions(true);
    }

    private boolean tailscaleInstalled() {
        return getPackageManager().getLaunchIntentForPackage(TAILSCALE) != null;
    }

    /**
     * Open Tailscale, which is all it takes.
     *
     * <p>An app cannot switch on another app's VPN — Android reserves that for
     * the app that owns it, and rightly. But opening Tailscale is enough: it
     * reconnects by itself, and onResume then finds the server without anybody
     * having to come back and press anything.
     */
    private void launchTailscale() {
        Intent go = getPackageManager().getLaunchIntentForPackage(TAILSCALE);
        if (go == null) {
            message.setText("Tailscale is not installed on this phone.");
            return;
        }
        message.setText("Opening Tailscale. Come back here once it says Connected.");
        startActivity(go);
    }
}
