package com.homesh.tv;

import android.util.Log;

import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * Sending a crash to the server instead of taking it to the grave.
 *
 * <p>A television has no console anybody is going to read. Diagnosing one has
 * meant carrying a laptop to it and attaching a cable, which in practice means
 * guessing instead — and a guess costs a round trip through somebody else's
 * evening. The stack trace is the one thing that would end that, and the server
 * is already on the same network.
 *
 * <p>Best effort by construction: a short timeout, everything swallowed, and the
 * platform's own handler called afterwards either way. A crash reporter that
 * delays or alters a crash is worse than none.
 */
final class CrashReport {

    private static final String TAG = "HomeshCrash";
    private static final int TIMEOUT_MS = 3000;

    private CrashReport() {}

    static void install(String server, String device, String version) {
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();

        Thread.setDefaultUncaughtExceptionHandler((thread, problem) -> {
            try {
                Log.e(TAG, "uncaught on " + thread.getName(), problem);
                send(server, device, version, thread.getName(), problem);
            } catch (Throwable ignored) {
                // Nothing here may prevent the crash being handled normally.
            }
            if (previous != null) previous.uncaughtException(thread, problem);
        });
    }

    private static void send(String server, String device, String version, String thread,
                             Throwable problem) {
        if (server == null || server.isEmpty()) return;

        StringWriter trace = new StringWriter();
        problem.printStackTrace(new PrintWriter(trace));

        // Hand-built rather than JSONObject: this runs while the process is
        // already dying and the fewer objects it needs, the likelier it lands.
        String body = "{\"device\":\"" + escape(device)
                + "\",\"thread\":\"" + escape(thread)
                + "\",\"version\":\"" + escape(version)
                + "\",\"trace\":\"" + escape(trace.toString()) + "\"}";

        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(server + "/api/renderers/crash").openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.getBytes(StandardCharsets.UTF_8));
            }
            conn.getResponseCode();
        } catch (Exception e) {
            Log.w(TAG, "could not report the crash", e);
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** JSON string escaping, by hand and bounded.
     *
     * <p>A stack trace is full of quotes and newlines, and this runs while the
     * process is already dying — so it builds the one string it needs rather
     * than asking a JSON library for help it may not live long enough to get.
     * Bounded at eight kilobytes: the top of a trace is the part worth having.
     */
    private static String escape(String text) {
        StringBuilder out = new StringBuilder(text.length() + 16);
        for (int i = 0; i < text.length() && out.length() < 8000; i++) {
            char c = text.charAt(i);
            if (c == '"') out.append("\\\"");
            else if (c == '\\') out.append("\\\\");
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') continue;
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(' ');
            else out.append(c);
        }
        return out.toString();
    }
}
