package com.homesh.tv;

import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Is this address a Homesh server?
 *
 * <p>Shared by setup, which asks before saving an address, and by every launch,
 * which asks whether the saved one still answers. Both need the same question
 * settled the same way, and a wrong-but-live address — something else on the
 * network holding that port — has to fail as clearly as a dead one.
 */
final class Server {

    private static final int TIMEOUT_MS = 4000;

    /** For sweeping a subnet, where almost every address is nothing at all.
     *
     * A machine on the same wire answers in single-digit milliseconds; four
     * seconds is a budget for a slow server, not for finding out that .37 is a
     * lightbulb. At the patient timeout a sweep of 254 addresses on two ports
     * could not finish inside its own deadline, so the server sat undiscovered
     * behind addresses nobody was home at. */
    private static final int SWEEP_TIMEOUT_MS = 700;

    private Server() {}

    static boolean reachable(String base) {
        return reachable(base, TIMEOUT_MS);
    }

    /** Whether an address is a Homesh server, waiting no longer than told. */
    static boolean sweepable(String base) {
        return reachable(base, SWEEP_TIMEOUT_MS);
    }

    static boolean reachable(String base, int timeoutMs) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(base + "/api/health").openConnection();
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setRequestMethod("GET");
            if (conn.getResponseCode() != 200) return false;

            StringBuilder body = new StringBuilder();
            try (InputStream in = conn.getInputStream()) {
                byte[] buf = new byte[512];
                int n;
                while ((n = in.read(buf)) > 0 && body.length() < 4096) {
                    body.append(new String(buf, 0, n, "UTF-8"));
                }
            }
            // Something else answering on that port is a wrong address, not a
            // working one, and saying so saves a black screen later.
            return body.indexOf("\"status\"") >= 0;
        } catch (IOException | RuntimeException e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
