package com.homesh.tv;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Fetching the mailbox and showing what is in it as notifications.
 *
 * <p>There is no push service here: Google's would need a library the build
 * cannot take, an outside account, and every message passing through a third
 * party. The phone asks the server instead -- every fifteen minutes in the
 * background (NotifyJob), or with a request held open for fifty seconds at a
 * time while the person has Instant on (NotifyService).
 *
 * <p>The token and the cursors live in the Vault, encrypted. A credential or
 * the text of a message is never logged.
 */
final class Mailbox {

    static final String ACTION_OPEN_MESSAGE = "com.homesh.tv.OPEN_MESSAGE";
    static final String EXTRA_SENDER = "sender";
    static final String EXTRA_PATH = "path";

    static final String KEY_TOKEN = "notify.token";
    static final String KEY_WHO = "notify.who";
    static final String KEY_INSTANT = "notify.instant";
    static final String KEY_LOCKSCREEN = "notify.lockscreen";

    private static final String TAG = "HomeshMailbox";
    private static final String SYSTEM_CHANNEL = "system";
    private static final int TURNED_OFF_ID = 1;

    /** Where the PC and the standby are, as PhoneActivity keeps them. */
    private static final String KEY_ORIGIN = "origin";
    private static final String KEY_LAN = "lan";
    private static final String KEY_STANDBY = "standby";

    private static final Object LOCK = new Object();
    /** The address that answered last, tried first so a long poll is not preceded by probes. */
    private static volatile String lastGood;

    private Mailbox() {}

    /** What one answer from the inbox held. */
    private static final class Reply {
        int status;
        String role;
        long cursor;
        JSONArray messages = new JSONArray();
    }

    static boolean on(Context context) {
        return Vault.get(context, KEY_TOKEN) != null;
    }

    static boolean instant(Context context) {
        return "1".equals(Vault.get(context, KEY_INSTANT));
    }

    /**
     * Fetch once from whichever machine answers. Returns how many arrived, or
     * -1 when nothing could be reached (so a loop can back off).
     */
    static int check(Context context, int waitSeconds) {
        synchronized (LOCK) {
            String token = Vault.get(context, KEY_TOKEN);
            if (token == null) return 0;
            try {
                return fetchAll(context, token, waitSeconds);
            } catch (IOException | JSONException e) {
                lastGood = null;
                Log.i(TAG, "could not check: " + e.getClass().getSimpleName());
                return -1;
            }
        }
    }

    private static int fetchAll(Context context, String token, int waitSeconds)
            throws IOException, JSONException {
        String address = lastGood;
        if (address == null) address = pick(context);
        if (address == null) return -1;

        String standby = Vault.get(context, KEY_STANDBY);
        String role = standby != null && Origins.same(address, standby) ? "standby" : "primary";

        Reply reply = fetch(address, token, cursor(context, role), waitSeconds);
        if (reply.status == 200 && reply.role != null && !reply.role.equals(role)) {
            // Asked with the other machine's cursor: ids are per machine.
            role = reply.role;
            reply = fetch(address, token, cursor(context, role), waitSeconds);
        }
        lastGood = address;

        if (reply.status == 401) {
            // Only the PC's refusal means this phone was removed. The standby
            // learns of a new phone with the next backup; deleting the token on
            // its word would switch notifications off for a phone that is fine.
            if (NotifyRules.removedByPc(reply.role)) turnOff(context, true);
            return 0;
        }
        if (reply.status != 200) throw new IOException("status " + reply.status);

        String cursorKey = NotifyRules.cursorKey(reply.role);
        // A machine never asked before: catch up silently rather than
        // replaying a week of what it holds.
        boolean baseline = Vault.get(context, cursorKey) == null;
        int shown = 0;
        if (!baseline) {
            for (int i = 0; i < reply.messages.length(); i++) {
                JSONObject m = reply.messages.getJSONObject(i);
                show(context, m.getLong("id"), m.optString("sender", ""),
                        m.optString("title", ""), m.optString("body", ""),
                        m.optString("path", "/"),
                        m.isNull("tag") ? null : m.optString("tag", null));
                shown++;
            }
        }
        Vault.put(context, cursorKey, Long.toString(reply.cursor));
        if (baseline && reply.messages.length() >= 50) {
            // More than one page: keep catching up, still silently.
            return fetchAll(context, token, 0);
        }
        return shown;
    }

    private static long cursor(Context context, String role) {
        String stored = Vault.get(context, NotifyRules.cursorKey(role));
        try {
            return stored == null ? 0 : Long.parseLong(stored);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /** The PC's addresses first, the standby only when neither answers. */
    private static String pick(Context context) {
        String lan = Vault.get(context, KEY_LAN);
        String origin = Vault.get(context, KEY_ORIGIN);
        String standby = Vault.get(context, KEY_STANDBY);
        return Reach.pick(new String[] {lan, origin},
                standby == null ? new String[0] : new String[] {standby},
                Server::reachable, 4000, 5000);
    }

    private static Reply fetch(String address, String token, long after, int waitSeconds)
            throws IOException, JSONException {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(address + "/api/notify/inbox?after=" + after + "&wait=" + waitSeconds);
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout((waitSeconds + 10) * 1000);
            conn.setUseCaches(false);
            conn.setRequestProperty("Authorization", "Bearer " + token);

            Reply reply = new Reply();
            reply.status = conn.getResponseCode();
            InputStream in = reply.status < 400 ? conn.getInputStream() : conn.getErrorStream();
            String text = in == null ? "" : read(in);
            if (reply.status == 200) {
                JSONObject json = new JSONObject(text);
                reply.role = json.optString("role", "primary");
                reply.cursor = json.optLong("cursor", after);
                JSONArray list = json.optJSONArray("messages");
                if (list != null) reply.messages = list;
            } else if (reply.status == 401) {
                // An unreadable body, or one naming no role, stays null: never
                // treated as the PC's word.
                try {
                    reply.role = new JSONObject(text).optString("role", null);
                } catch (JSONException e) {
                    reply.role = null;
                }
            }
            return reply;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String read(InputStream in) throws IOException {
        StringBuilder body = new StringBuilder();
        try (InputStream stream = in) {
            byte[] buf = new byte[4096];
            int n;
            while ((n = stream.read(buf)) > 0 && body.length() < 256 * 1024) {
                body.append(new String(buf, 0, n, "UTF-8"));
            }
        }
        return body.toString();
    }

    /** Claim a code for a device token. Returns {token, cursor, who} or null. */
    static String[] claim(String address, String claim) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(address + "/api/notify/devices/claim").openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            byte[] body = new JSONObject().put("claim", claim).toString().getBytes("UTF-8");
            conn.getOutputStream().write(body);
            if (conn.getResponseCode() != 200) return null;
            JSONObject json = new JSONObject(read(conn.getInputStream()));
            return new String[] {json.getString("token"), Long.toString(json.getLong("cursor")),
                    json.optString("who", "")};
        } catch (Exception e) {
            Log.i(TAG, "claim failed: " + e.getClass().getSimpleName());
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** Forget this phone's registration and stop everything that ran for it. */
    static void turnOff(Context context, boolean tellThePerson) {
        Vault.put(context, KEY_TOKEN, null);
        Vault.put(context, KEY_WHO, null);
        Vault.put(context, KEY_INSTANT, null);
        Vault.put(context, NotifyRules.cursorKey("primary"), null);
        Vault.put(context, NotifyRules.cursorKey("standby"), null);
        lastGood = null;
        NotifyJob.cancel(context);
        context.stopService(new Intent(context, NotifyService.class));
        if (tellThePerson) {
            post(context, SYSTEM_CHANNEL, "Messages from Homesh", TURNED_OFF_ID,
                    "Homesh notifications were turned off for this phone.", "", null, true, null);
        }
    }

    static void show(Context context, long id, String sender, String title, String body,
                     String path, String tag) {
        if (sender.isEmpty() || title.isEmpty()) return;
        int nid = NotifyRules.notificationId(sender, tag, id);

        Intent open = new Intent(context, PhoneActivity.class)
                .setAction(ACTION_OPEN_MESSAGE)
                .putExtra(EXTRA_SENDER, sender)
                .putExtra(EXTRA_PATH, path);
        // Opens an activity directly: from Android 12 a notification that starts
        // one through a service or a receiver is refused. The id is also the
        // request code, since extras do not tell two pending intents apart.
        PendingIntent tap = PendingIntent.getActivity(context, nid, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        // A channel for each sender, so one app can be silenced in Android's own
        // settings without losing the rest.
        post(context, "app." + sender, sender, nid, title, body, tap,
                "1".equals(Vault.get(context, KEY_LOCKSCREEN)), "New message from " + sender);
    }

    private static void post(Context context, String channel, String channelName, int id,
                             String title, String body, PendingIntent tap,
                             boolean showOnLock, String publicText) {
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager == null) return;
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            if (manager.getNotificationChannel(channel) == null) {
                manager.createNotificationChannel(new NotificationChannel(channel, channelName,
                        NotificationManager.IMPORTANCE_DEFAULT));
            }
            builder = new Notification.Builder(context, channel);
        } else {
            builder = new Notification.Builder(context);
        }
        builder.setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(title)
                .setContentText(body)
                .setAutoCancel(true);
        if (tap != null) builder.setContentIntent(tap);
        if (!showOnLock && publicText != null) {
            builder.setVisibility(Notification.VISIBILITY_PRIVATE);
            Notification.Builder hidden = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? new Notification.Builder(context, channel) : new Notification.Builder(context);
            hidden.setSmallIcon(R.drawable.ic_launcher).setContentTitle(publicText);
            builder.setPublicVersion(hidden.build());
        }
        try {
            manager.notify(id, builder.build());
        } catch (SecurityException e) {
            Log.i(TAG, "notifications are not allowed");
        }
    }
}
