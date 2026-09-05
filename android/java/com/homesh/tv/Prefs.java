package com.homesh.tv;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * Where this box thinks the server is.
 *
 * <p>The address is configuration, never a constant in the source: it is
 * assigned by DHCP and differs per house, and this file is published. Nothing
 * here ships knowing where anything lives.
 *
 * <p>The pairing credential is deliberately <em>not</em> stored here. It lives
 * in the WebView's own localStorage, written by the same TV page that runs in a
 * browser, so a screen paired as a browser tab and one paired through this shell
 * behave identically and there is only one copy of that logic.
 */
final class Prefs {
    private static final String FILE = "homesh";
    private static final String KEY_SERVER = "server";

    private Prefs() {}

    static SharedPreferences of(Context context) {
        return context.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    static String server(Context context) {
        return of(context).getString(KEY_SERVER, null);
    }

    static void setServer(Context context, String url) {
        of(context).edit().putString(KEY_SERVER, url).apply();
    }

    /** @see ServerAddress#normalise(String) */
    static String normalise(String raw) {
        return ServerAddress.normalise(raw);
    }
}
