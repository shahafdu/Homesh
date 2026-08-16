package com.homesh.tv;

import android.util.Log;

import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;

/**
 * Finding the server without anybody typing its address.
 *
 * <p>Entering a URL on a television is the worst part of setting this up: an
 * on-screen keyboard, a remote control, and thirty-odd presses to spell out
 * something the server already knows about itself. The two machines are on the
 * same network, so the television can simply ask.
 *
 * <p>A broadcast on a fixed port, answered only by a server that recognises the
 * probe. No library, no service discovery framework, no dependency — which is
 * what keeps this app buildable without Gradle.
 */
final class Discovery {

    private static final String TAG = "HomeshDiscovery";
    private static final int PORT = 45877;
    private static final String PROBE = "HOMESH-DISCOVER-V1";

    /** Total time to wait for an answer. Long enough for a sleepy Wi-Fi link. */
    private static final int TIMEOUT_MS = 2500;

    private Discovery() {}

    /**
     * Broadcast, and return the first server that answers.
     *
     * <p>Blocking, so it belongs on a background thread. Returns null when
     * nothing answers, which is a normal outcome — the server may be off, or on
     * another network — and the caller falls back to asking.
     */
    static String find() {
        DatagramSocket socket = null;
        try {
            socket = new DatagramSocket();
            socket.setBroadcast(true);
            socket.setSoTimeout(TIMEOUT_MS);

            byte[] probe = PROBE.getBytes(StandardCharsets.UTF_8);
            // 255.255.255.255 rather than the subnet's own broadcast address: it
            // needs no knowledge of the netmask, and every server on the wire
            // hears it.
            socket.send(new DatagramPacket(probe, probe.length,
                    InetAddress.getByName("255.255.255.255"), PORT));

            byte[] buffer = new byte[1024];
            DatagramPacket reply = new DatagramPacket(buffer, buffer.length);
            socket.receive(reply);

            String body = new String(reply.getData(), 0, reply.getLength(), StandardCharsets.UTF_8);
            JSONObject json = new JSONObject(body);
            if (!"homesh".equals(json.optString("service"))) return null;

            String url = json.optString("url", null);
            Log.i(TAG, "found server at " + url);
            return ServerAddress.normalise(url);
        } catch (IOException e) {
            // A timeout is the ordinary "nothing there" case, not a fault.
            Log.i(TAG, "no server answered: " + e.getMessage());
            return null;
        } catch (Exception e) {
            Log.w(TAG, "discovery failed", e);
            return null;
        } finally {
            if (socket != null) socket.close();
        }
    }
}
