package com.homesh.tv;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.util.Log;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Keeping the television's copy current.
 *
 * <p>Without this, every change to the app means walking to each screen, opening
 * a downloader, and typing a URL with a remote control. The server already hands
 * out the APK; it can hand out its version number too, and the app can check on
 * the way in.
 *
 * <p>Android will still show its own installer prompt — that confirmation cannot
 * be skipped by an ordinary app, and should not be. What this removes is the
 * typing, the downloader, and having to know an update exists at all.
 */
final class Updater {

    private static final String TAG = "HomeshUpdater";
    private static final int TIMEOUT_MS = 8000;

    private Updater() {}

    /** What the server is offering, or null if it could not be asked. */
    static Integer availableVersion(String server) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(server + "/tv.json").openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            if (conn.getResponseCode() != 200) return null;

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0) body.append(new String(buf, 0, n, "UTF-8"));
            }
            return new JSONObject(body.toString()).optInt("versionCode", -1);
        } catch (Exception e) {
            // An older server has no /tv.json. That is not a failure worth
            // showing anybody — it simply means there is nothing to offer.
            Log.i(TAG, "no update information: " + e.getMessage());
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    static int installedVersion(Context context) {
        try {
            return context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return Integer.MAX_VALUE;  // never offer an update we cannot compare
        }
    }

    /**
     * Download the APK and hand it to the system installer.
     *
     * <p>Into the app's own cache with a content URI: a file:// URI to a package
     * has been refused since Android 7, and external storage would need a
     * permission this app has no other reason to hold.
     */
    static void downloadAndInstall(Context context, String server) {
        HttpURLConnection conn = null;
        try {
            File target = new File(context.getCacheDir(), UpdateProvider.FILENAME);
            conn = (HttpURLConnection) new URL(server + "/tv.apk").openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(60000);
            if (conn.getResponseCode() != 200) return;

            try (InputStream in = conn.getInputStream();
                 FileOutputStream out = new FileOutputStream(target)) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }

            Uri uri = UpdateProvider.uriFor(context);

            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(uri, "application/vnd.android.package-archive");
            install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(install);
        } catch (Exception e) {
            Log.w(TAG, "update failed", e);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
