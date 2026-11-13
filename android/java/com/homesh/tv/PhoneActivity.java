package com.homesh.tv;

import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
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
    private static final String KEY_ORIGIN = "origin";
    /** The standby on Oracle, tried when neither of the PC's addresses answers. */
    private static final String KEY_STANDBY = "standby";
    /**
     * Other web apps to offer beside Homesh, as the PC last listed them: a JSON
     * array of {name, url, standby}, the last the same app's address on the
     * standby, or null. Configured on the PC (PHONE_APPS), never in here.
     */
    private static final String KEY_APPS = "apps";
    /** The PC's address on the house network, learned or typed. */
    private static final String KEY_LAN = "lan";

    /** Tailscale's own package. Declared in <queries> so it can be seen at all. */
    private static final String TAILSCALE = "com.tailscale.ipn";

    /** The longest the opening check waits: Server's own timeout, plus slack. */
    private static final long PATIENT_MS = 5000;

    private TextView message;
    private Button openHomesh;
    private Button openTailscale;
    private Button searchAgain;
    private Button changeAddress;
    private LinearLayout root;
    /** One button per app in KEY_APPS, rebuilt whenever the PC lists them again. */
    private LinearLayout apps;

    /** Which version this is, and whether a newer one is waiting. */
    private TextView about;
    private Button updates;

    /** Notifications: what state they are in, and the controls that change it. */
    private TextView notifyState;
    private Button notifyTurnOn;
    private Button notifyTurnOff;
    private Switch notifyInstant;
    private Switch notifyLockScreen;
    private boolean settingSwitches;
    private static final int ASK_NOTIFICATIONS = 11;

    /** What the server offered at the last check, once there is something newer. */
    private PhoneUpdates.Offer newer;
    private volatile boolean checkingUpdates;

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

    /**
     * Whether one of the other apps was opened over this screen. Coming back
     * from it is coming back to this screen as it was -- not a reason to check
     * and open Homesh, which is what returning otherwise does.
     */
    private boolean inApp;

    /**
     * Whether finding the server should open Homesh by itself. Only on a fresh
     * start, and only when Homesh is the one thing here: with other apps beside
     * it, this screen is the menu, and opening one of them is a choice to make
     * on it rather than one made for the person.
     *
     * <p>Never on the way back. Both flags above live in this object, and
     * Android drops the object while another app is in front whenever the
     * phone is short of memory -- a WebView is a lot of it. Rebuilt with the
     * flags forgotten, returning looked like starting, and closing an app
     * opened Homesh. So they are kept in the saved state as well, and a screen
     * that is rebuilt rather than started never opens anything unasked.
     */
    private boolean autoOpen;
    private static final String SAVED_IN_APP = "inApp";
    private static final String SAVED_HANDED_OFF = "handedOff";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) {
            inApp = state.getBoolean(SAVED_IN_APP, false);
            handedOff = state.getBoolean(SAVED_HANDED_OFF, false);
        }
        autoOpen = state == null;
        // Opened by tapping a notification or by Homesh's settings page: the
        // person wants that and not the menu, or Homesh opening by itself.
        if (state == null && getIntent() != null
                && (Mailbox.ACTION_OPEN_MESSAGE.equals(getIntent().getAction())
                || getIntent().getData() != null)) {
            autoOpen = false;
        }

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
            else check(false, true);
        });
        openTailscale = button("Open Tailscale", v -> launchTailscale());
        apps = new LinearLayout(this);
        apps.setOrientation(LinearLayout.VERTICAL);
        showApps();
        searchAgain = button("Search on this network", v -> check(true, true));
        changeAddress = button("Change the address", v -> {
            // The setup screen is the television's too, and reads the address
            // from the plain store. Put there for as long as that screen is
            // open, so the box shows the current address and backing out
            // returns here; lan() takes it back into the vault on return.
            String current = lan();
            if (current != null) Prefs.setServer(this, current);
            startActivity(new Intent(this, SetupActivity.class));
        });

        // Which build is installed, said on the screen rather than buried in
        // Android's app settings -- it is the first question when something
        // behaves differently on one phone than on another.
        about = new TextView(this);
        about.setText("Homesh Connect " + PhoneUpdates.installedName(this));
        about.setTextColor(Color.parseColor("#A79B8D"));
        about.setTextSize(13);
        about.setPadding(0, 56, 0, 8);
        updates = button("Check for updates", v -> onUpdatesPressed());

        notifyState = new TextView(this);
        notifyState.setTextColor(Color.parseColor("#A79B8D"));
        notifyState.setTextSize(14);
        notifyState.setPadding(0, 40, 0, 8);
        notifyTurnOn = button("Turn on notifications", v -> openNotificationSettings());
        notifyInstant = toggle("Instant: a message arrives within seconds", (v, on) -> {
            if (!settingSwitches) setInstant(on);
        });
        notifyLockScreen = toggle("Show message text on the lock screen", (v, on) -> {
            if (!settingSwitches) {
                Vault.put(this, Mailbox.KEY_LOCKSCREEN, on ? "1" : null);
            }
        });
        notifyTurnOff = button("Turn off on this phone", v -> {
            Mailbox.turnOff(this, false);
            showNotifyRow();
        });

        root.addView(brand);
        root.addView(message);
        root.addView(openHomesh);
        root.addView(openTailscale);
        root.addView(apps);
        root.addView(searchAgain);
        root.addView(changeAddress);
        root.addView(notifyState);
        root.addView(notifyTurnOn);
        root.addView(notifyInstant);
        root.addView(notifyLockScreen);
        root.addView(notifyTurnOff);
        root.addView(about);
        root.addView(updates);
        setContentView(root);

        showActions(false);
        showNotifyRow();
        if (state == null) handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(new Intent(this, PhoneActivity.class));
        handleIntent(intent);
    }

    private Switch toggle(String label, android.widget.CompoundButton.OnCheckedChangeListener l) {
        Switch s = new Switch(this);
        s.setText(label);
        s.setTextColor(Color.parseColor("#F2EBE2"));
        s.setPadding(0, 16, 0, 16);
        s.setOnCheckedChangeListener(l);
        return s;
    }

    // -- Notifications -------------------------------------------------------

    /** The row's state, and only the controls that make sense for it. */
    private void showNotifyRow() {
        boolean on = Mailbox.on(this);
        String who = Vault.get(this, Mailbox.KEY_WHO);
        notifyState.setText("Notifications: " + (!on ? "off"
                : (Mailbox.instant(this) ? "on, instant" : "on")
                + (who != null && !who.isEmpty() ? " for " + who : "")));
        notifyTurnOn.setVisibility(on ? View.GONE : View.VISIBLE);
        notifyInstant.setVisibility(on ? View.VISIBLE : View.GONE);
        notifyLockScreen.setVisibility(on ? View.VISIBLE : View.GONE);
        notifyTurnOff.setVisibility(on ? View.VISIBLE : View.GONE);
        settingSwitches = true;
        notifyInstant.setChecked(Mailbox.instant(this));
        notifyLockScreen.setChecked("1".equals(Vault.get(this, Mailbox.KEY_LOCKSCREEN)));
        settingSwitches = false;
    }

    /**
     * Turning it on starts in Homesh's own settings, in the tab: only there is
     * the person signed in, and only there can a single-use code be made. The
     * page then sends it back as a homeshconnect:// link.
     */
    private void openNotificationSettings() {
        String at = reachedAt != null ? reachedAt : Vault.get(this, KEY_ORIGIN);
        if (at == null) at = lan();
        if (at == null) {
            message.setText("Find your server first, then open Homesh's settings.");
            return;
        }
        openAt(at, "");
        message.setText("Open Settings in Homesh, then press Turn on for this phone.");
    }

    private void setInstant(boolean on) {
        if (!on) {
            Vault.put(this, Mailbox.KEY_INSTANT, null);
            NotifyService.stop(this);
            showNotifyRow();
            return;
        }
        Vault.put(this, Mailbox.KEY_INSTANT, "1");
        try {
            NotifyService.start(this);
        } catch (RuntimeException e) {
            Vault.put(this, Mailbox.KEY_INSTANT, null);
            message.setText("Android would not start Instant. The 15-minute checks carry on.");
            showNotifyRow();
            return;
        }
        // Android suspends the network for a sleeping app unless it is exempt
        // from battery saving. Say why, then open the screen where that is done.
        message.setText("So messages arrive at once, allow Homesh Connect to run in the "
                + "background. Without it Instant works while the phone is awake, and "
                + "checks every 15 minutes while it sleeps.");
        try {
            startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
        } catch (RuntimeException e) {
            Log.i(TAG, "no battery screen to open");
        }
        showNotifyRow();
    }

    /** A link from Homesh's settings page, or a tapped notification. */
    private void handleIntent(Intent intent) {
        if (intent == null) return;
        Uri data = intent.getData();
        if (Intent.ACTION_VIEW.equals(intent.getAction()) && data != null
                && "homeshconnect".equals(data.getScheme()) && "notify".equals(data.getHost())) {
            offerToTurnOn(data.getQueryParameter("claim"), data.getQueryParameter("origin"));
        } else if (Mailbox.ACTION_OPEN_MESSAGE.equals(intent.getAction())) {
            openMessage(intent.getStringExtra(Mailbox.EXTRA_SENDER),
                    intent.getStringExtra(Mailbox.EXTRA_PATH));
        }
    }

    private void offerToTurnOn(String claim, String origin) {
        // Any web page can produce this link. It is honoured only for an
        // address this app already holds for the PC.
        String address = NotifyRules.match(origin, lan(), Vault.get(this, KEY_ORIGIN));
        if (address == null || !NotifyRules.validClaim(claim)) {
            message.setText("This link is not from your Homesh.");
            return;
        }
        new AlertDialog.Builder(this)
                .setMessage("Turn on Homesh notifications on this phone?")
                .setPositiveButton("Turn on", (d, w) -> turnOn(address, claim))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void turnOn(String address, String claim) {
        // Asked for now, at the moment it is first needed.
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"},
                    ASK_NOTIFICATIONS);
        }
        message.setText("Turning on notifications...");
        new Thread(() -> {
            String[] got = Mailbox.claim(address, claim);
            runOnUiThread(() -> {
                if (got == null) {
                    message.setText("That did not work. Try again from Homesh's settings.");
                    return;
                }
                Mailbox.turnOff(this, false);
                Vault.put(this, Mailbox.KEY_TOKEN, got[0]);
                Vault.put(this, NotifyRules.cursorKey("primary"), got[1]);
                Vault.put(this, Mailbox.KEY_WHO, got[2]);
                NotifyJob.schedule(this);
                message.setText("Notifications are on" + (got[2].isEmpty() ? "." : " for " + got[2] + "."));
                showNotifyRow();
            });
        }).start();
    }

    /**
     * A tapped notification. The path is checked again here, and only an app
     * from the stored list is ever opened: an address is never taken from a
     * message.
     */
    private void openMessage(String sender, String path) {
        if (!NotifyRules.validPath(path) || path.equals("/")) path = "";
        final String page = path;

        if (sender != null && sender.equals(NotifyRules.HOMESH)) {
            inApp = false;
            new Thread(() -> {
                String reachable = Reach.pick(new String[] {lan(), Vault.get(this, KEY_ORIGIN)},
                        standbyList(), Server::reachable, PATIENT_MS, PATIENT_MS);
                runOnUiThread(() -> {
                    if (reachable == null) cannotReach();
                    else openAt(reachable, page);
                });
            }).start();
            return;
        }
        try {
            String stored = Vault.get(this, KEY_APPS);
            JSONArray list = new JSONArray(stored == null ? "[]" : stored);
            for (int i = 0; i < list.length(); i++) {
                JSONObject app = list.getJSONObject(i);
                if (!app.optString("name", "").equals(sender)) continue;
                String url = trimSlash(app.optString("url", ""));
                String standby = app.isNull("standby") ? null : trimSlash(app.optString("standby", ""));
                if (!isWeb(url) || !Origins.under(url + page, url)) break;
                inApp = true;
                if (isWeb(standby)) {
                    startActivity(new Intent(this, AppActivity.class)
                            .putExtra(AppActivity.EXTRA_NAME, sender)
                            .putExtra(AppActivity.EXTRA_PC, url)
                            .putExtra(AppActivity.EXTRA_STANDBY, standby)
                            .putExtra(AppActivity.EXTRA_SAME, app.optBoolean("same", false))
                            .putExtra(AppActivity.EXTRA_PATH, page));
                } else {
                    launchApp(sender, url + page);
                }
                return;
            }
        } catch (Exception e) {
            Log.i(TAG, "could not open a message's app: " + e.getClass().getSimpleName());
        }
        // Not an app this phone knows any more: this screen, nothing else.
        message.setText("");
    }

    private String[] standbyList() {
        String standby = Vault.get(this, KEY_STANDBY);
        return standby == null ? new String[0] : new String[] {standby};
    }

    private static String trimSlash(String url) {
        while (url != null && url.endsWith("/")) url = url.substring(0, url.length() - 1);
        return url;
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == ASK_NOTIFICATIONS && results.length > 0
                && results[0] != PackageManager.PERMISSION_GRANTED) {
            message.setText("Notifications are blocked for Homesh Connect. Allow them in "
                    + "Android's settings for this app, and they will start showing.");
        }
    }

    @Override
    protected void onResume() {
        super.onResume();

        if (Mailbox.on(this)) {
            NotifyJob.schedule(this);
            final Context context = getApplicationContext();
            new Thread(() -> Mailbox.check(context, 0)).start();
        }
        showNotifyRow();

        if (inApp) {
            inApp = false;
            message.setText("");
            if (reachedAt != null) openHomesh.setVisibility(View.VISIBLE);
            showActions(true);
            return;
        }

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
        boolean go = autoOpen && !hasApps();
        autoOpen = false;
        check(false, go);
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putBoolean(SAVED_IN_APP, inApp);
        out.putBoolean(SAVED_HANDED_OFF, handedOff);
    }

    /** Whether the PC has listed apps beside Homesh, so this screen is a menu. */
    private boolean hasApps() {
        return apps.getChildCount() > 0;
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
        apps.setVisibility(visibility);
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
    private void check(boolean sweep, boolean go) {
        if (checking) return;
        checking = true;

        new Thread(() -> {
            String lan = lan();
            String origin = Vault.get(this, KEY_ORIGIN);
            String standby = Vault.get(this, KEY_STANDBY);

            // The PC first, by either of its addresses: it is the stronger
            // machine, it holds every file, and it is where changes belong. The
            // standby only when neither answers -- the PC is off, not merely
            // slow -- so being at home with the PC on never touches Oracle.
            // All three are asked at once, so a PC that is off costs one
            // timeout before the standby opens, not three in a row.
            boolean standbyNow = !sweep && standby != null;
            String reachable = Reach.pick(new String[] {origin, lan},
                    standbyNow ? new String[] {standby} : new String[0],
                    Server::reachable, PATIENT_MS, PATIENT_MS);

            // A search of this network looks for the PC, so it comes before the
            // standby: somebody who pressed it expects the PC to be here.
            if (reachable == null && sweep) {
                runOnUiThread(() -> message.setText("Searching this network…"));
                String found = Discovery.find();
                if (found != null) {
                    Vault.put(this, KEY_LAN, found);
                    reachable = found;
                }
                if (reachable == null && standby != null) {
                    runOnUiThread(() -> message.setText(
                            "Your PC is not answering. Trying the standby…"));
                    if (Server.reachable(standby)) reachable = standby;
                }
            }

            if (reachable != null) {
                // Learned while it is possible to learn it, so the next time the
                // phone is away from home it already knows where to look.
                rememberOrigin(reachable);
                String at = reachable;
                boolean onStandby = reachable.equals(standby);
                runOnUiThread(() -> {
                    if (!go) {
                        // Found, and left on the screen to open: the menu.
                        reachedAt = at;
                        message.setText(onStandby
                                ? "Your PC is not answering, so Homesh will open on the standby."
                                : "");
                        showActions(true);
                        openHomesh.setVisibility(View.VISIBLE);
                        return;
                    }
                    open(at);
                    // Said on this screen as well as in the app's own status
                    // bar: it is the moment somebody decides whether to wait
                    // for the PC or carry on.
                    if (onStandby) {
                        message.setText("Your PC is off, so Homesh opened on the standby. "
                                + "Files on Drive play; files kept only on the PC do not.");
                    }
                });
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
    private void rememberOrigin(String base) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(base + "/tv.address").openConnection();
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            // The apps beside Homesh, the standby and the outside address are
            // told only to someone signed in, so this sends the session the
            // app's own web view holds for that address. Before the first
            // sign-in there is none, and the PC says only its house address.
            String cookie = android.webkit.CookieManager.getInstance().getCookie(base);
            if (cookie != null && !cookie.isEmpty()) conn.setRequestProperty("Cookie", cookie);

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0 && body.length() < 4096) {
                    body.append(new String(buf, 0, n, "UTF-8"));
                }
            }

            JSONObject json = new JSONObject(body.toString());
            // Learned from the PC only. The standby answers this too, and what
            // it calls "origin" is itself: taken as the PC's address, the phone
            // would go on opening Oracle long after the PC was back.
            if ("standby".equals(json.optString("role", "primary"))) return;

            String origin = json.optString("origin", null);
            String lan = json.optString("lan", null);
            String standby = json.optString("standby", null);
            if (origin != null && !origin.isEmpty() && !"null".equals(origin)) {
                Vault.put(this, KEY_ORIGIN, origin);
            }
            if (lan != null && !lan.isEmpty() && !"null".equals(lan)) {
                Vault.put(this, KEY_LAN, lan);
            }
            if (standby != null && !standby.isEmpty() && !"null".equals(standby)) {
                Vault.put(this, KEY_STANDBY, standby);
            }
            // Replaced wholesale, an empty list included: an app removed on the
            // PC should disappear here, not linger from an older answer.
            JSONArray listed = json.optJSONArray("apps");
            if (listed != null) {
                Vault.put(this, KEY_APPS, listed.toString());
                runOnUiThread(this::showApps);
            }
        } catch (Exception e) {
            // Knowing the other address is a convenience, not a requirement.
            Log.i(TAG, "could not learn the other address: " + e.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * The PC's house address, from the vault.
     *
     * <p>Setup, which is shared with the television, writes what was typed to
     * the plain store. It is moved into the vault here and removed from there,
     * so it stays in the clear only while the setup screen is open.
     */
    private String lan() {
        String typed = Prefs.server(this);
        if (typed != null) {
            Vault.put(this, KEY_LAN, typed);
            Prefs.of(this).edit().clear().apply();
        }
        return Vault.get(this, KEY_LAN);
    }

    /** A button for each app the PC listed, in the order it listed them. */
    private void showApps() {
        apps.removeAllViews();
        String stored = Vault.get(this, KEY_APPS);
        if (stored == null) stored = "[]";
        try {
            JSONArray list = new JSONArray(stored);
            for (int i = 0; i < list.length(); i++) {
                JSONObject app = list.getJSONObject(i);
                String name = app.optString("name", "");
                String url = app.optString("url", "");
                if (name.isEmpty() || !isWeb(url)) continue;
                String standby = app.isNull("standby") ? null : app.optString("standby", null);
                String fallback = isWeb(standby) ? standby : null;
                boolean same = fallback != null && app.optBoolean("same", false);
                apps.addView(button("Open " + name, v -> openApp(name, url, fallback, same)));
            }
        } catch (Exception e) {
            Log.i(TAG, "could not read the app list: " + e.getMessage());
        }
    }

    private static boolean isWeb(String url) {
        return url != null && (url.startsWith("https://") || url.startsWith("http://"));
    }

    /**
     * Another app beside Homesh.
     *
     * <p>One with a copy on the standby opens in AppActivity, which keeps it on
     * whichever machine is answering and moves it while it is open -- a Custom
     * Tab cannot be moved once it is showing (AppActivity says why). One with
     * no copy has nowhere else to go, so it opens straight away in the same
     * kind of tab as Homesh, signed in as the browser is.
     *
     * <p>{@code same}: the PC declared the two copies interchangeable, so each
     * request may go to either (AppActivity's relay) instead of the page being
     * moved between them.
     */
    private void openApp(String name, String url, String standby, boolean same) {
        inApp = true;
        if (standby == null) {
            launchApp(name, url);
            return;
        }
        startActivity(new Intent(this, AppActivity.class)
                .putExtra(AppActivity.EXTRA_NAME, name)
                .putExtra(AppActivity.EXTRA_PC, url)
                .putExtra(AppActivity.EXTRA_STANDBY, standby)
                .putExtra(AppActivity.EXTRA_SAME, same));
    }

    private void launchApp(String name, String url) {
        try {
            startActivity(browserIntent(Uri.parse(url)));
        } catch (Exception e) {
            inApp = false;
            message.setText("No browser on this phone would open " + name + ".");
        }
    }

    /** Open Homesh over this screen, in a tab that belongs to this app. */
    private void open(String base) {
        openAt(base, "");
    }

    /** {@code path} has been through NotifyRules.validPath, or is empty. */
    private void openAt(String base, String path) {
        reachedAt = base;
        message.setText("Opening Homesh…");
        showActions(false);
        try {
            startActivity(browserIntent(Uri.parse(base + path)));
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
     * Check, or install what the last check found.
     *
     * <p>Asked of whichever address answers, the same way opening Homesh is: at
     * home that is the house address, away it is the tailnet one, and the server
     * hands out the same build either way.
     */
    private void onUpdatesPressed() {
        if (checkingUpdates) return;
        checkingUpdates = true;
        updates.setEnabled(false);
        final PhoneUpdates.Offer install = newer;
        about.setText(install != null ? "Downloading " + install.name + "…" : "Checking…");

        new Thread(() -> {
            String server = reachedAt;
            if (server == null) {
                server = firstThatAnswers(Vault.get(this, KEY_ORIGIN), lan());
            }
            final String at = server;
            final String mine = PhoneUpdates.installedName(this);

            if (at == null) {
                runOnUiThread(() -> finishUpdates(
                        "Homesh Connect " + mine + " · cannot reach the server to check",
                        "Check for updates"));
                return;
            }

            if (install != null) {
                boolean started = PhoneUpdates.downloadAndInstall(this, at);
                runOnUiThread(() -> finishUpdates(
                        started
                                ? "Homesh Connect " + mine + " · installing " + install.name
                                : "Homesh Connect " + mine + " · the download failed",
                        started ? "Install " + install.name : "Try again"));
                return;
            }

            PhoneUpdates.Offer offer = PhoneUpdates.offered(at);
            int installed = PhoneUpdates.installedCode(this);
            runOnUiThread(() -> {
                if (offer == null) {
                    finishUpdates("Homesh Connect " + mine + " · the server has no phone build to offer",
                            "Check for updates");
                } else if (offer.code > installed) {
                    newer = offer;
                    finishUpdates("Homesh Connect " + mine + " · " + offer.name + " is available",
                            "Install " + offer.name);
                } else {
                    finishUpdates("Homesh Connect " + mine + " · up to date", "Check for updates");
                }
            });
        }).start();
    }

    private void finishUpdates(String status, String label) {
        about.setText(status);
        updates.setText(label);
        updates.setEnabled(true);
        checkingUpdates = false;
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
