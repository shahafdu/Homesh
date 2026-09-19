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
 * Keeping the phone app current, the way the television app already is.
 *
 * <p>A separate class from {@link Updater} rather than a parameter added to it.
 * The television's version guard hashes the sources that go into its APK, so
 * touching Updater would have offered every screen in the house an update to
 * code that changes nothing on a screen. This file is compiled into the phone
 * app only, and the television never sees it.
 *
 * <p>The APK is handed to the installer through the same {@link UpdateProvider},
 * unchanged: it builds its address from the package name, so on the phone it is
 * already the phone's own.
 */
final class PhoneUpdates {

    private static final String TAG = "HomeshPhoneUpdates";
    private static final int TIMEOUT_MS = 8000;

    private PhoneUpdates() {}

    /** A build the server is offering. */
    static final class Offer {
        final int code;
        final String name;

        Offer(int code, String name) {
            this.code = code;
            this.name = name;
        }
    }

    /** What the server offers, or null if it could not be asked. */
    static Offer offered(String server) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(server + "/phone.json").openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setUseCaches(false);
            if (conn.getResponseCode() != 200) return null;

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0 && body.length() < 4096) {
                    body.append(new String(buf, 0, n, "UTF-8"));
                }
            }
            JSONObject json = new JSONObject(body.toString());
            return new Offer(json.optInt("versionCode", -1), json.optString("versionName", "?"));
        } catch (Exception e) {
            Log.i(TAG, "could not ask for the phone version: " + e.getMessage());
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    static int installedCode(Context context) {
        try {
            return context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionCode;
        } catch (Exception e) {
            return Integer.MAX_VALUE;  // never offer an update that cannot be compared
        }
    }

    static String installedName(Context context) {
        try {
            return context.getPackageManager()
                    .getPackageInfo(context.getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    /**
     * Download the phone APK and hand it to the system installer.
     *
     * <p>Returns false when the download failed, so the screen can say so rather
     * than sitting on "Downloading" for ever. Android's own install prompt is
     * shown either way and cannot be skipped -- nor should it be.
     */
    static boolean downloadAndInstall(Context context, String server) {
        HttpURLConnection conn = null;
        try {
            File target = new File(context.getCacheDir(), UpdateProvider.FILENAME);
            conn = (HttpURLConnection) new URL(server + "/phone").openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(60000);
            if (conn.getResponseCode() != 200) return false;

            try (InputStream in = conn.getInputStream();
                 FileOutputStream out = new FileOutputStream(target)) {
                byte[] buf = new byte[64 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }

            Intent install = new Intent(Intent.ACTION_VIEW);
            install.setDataAndType(UpdateProvider.uriFor(context),
                    "application/vnd.android.package-archive");
            install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(install);
            return true;
        } catch (Exception e) {
            Log.w(TAG, "phone update failed", e);
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
