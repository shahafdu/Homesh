package com.homesh.tv;

import android.util.Log;

import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Finding the server without anybody typing its address.
 *
 * <p>Entering a URL on a television is the worst part of setting this up: an
 * on-screen keyboard, a remote control, and thirty-odd presses to spell out
 * something the server already knows about itself. The two machines are on the
 * same network, so the television can simply ask.
 *
 * <p>Two ways, in order. A broadcast on a fixed port, answered only by a server
 * that recognises the probe — instant when it works. Then, when nothing answers,
 * every address on this screen's own subnet is asked directly.
 *
 * <p>The sweep exists because broadcast is not dependable and its failures are
 * silent. Wireless access points routinely drop broadcast between clients, and
 * a server behind a container's published UDP port may never see one at all. A
 * television that cannot find its server sits showing an address that has been
 * wrong for days, which is the entire chore this was written to remove — so it
 * is worth two hundred and fifty cheap requests to avoid.
 *
 * <p>No library, no service discovery framework, no dependency, which is what
 * keeps this app buildable without Gradle.
 */
final class Discovery {

    private static final String TAG = "HomeshDiscovery";
    private static final int PORT = 45877;
    private static final String PROBE = "HOMESH-DISCOVER-V1";

    /** How long the subnet sweep may take before giving up on this round. */
    private static final int SWEEP_SECONDS = 25;

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
        String byBroadcast = byBroadcast();
        if (byBroadcast != null) return byBroadcast;

        Log.i(TAG, "nothing answered the broadcast; asking the subnet directly");
        return bySweep();
    }

    /**
     * Ask every address on this screen's own subnet.
     *
     * <p>A /24, which every house network is, on the ports this server is
     * published at. Threads rather than sequence: two hundred and fifty four
     * addresses at two seconds each would be eight minutes in a row and is a
     * few seconds in parallel.
     */
    private static String bySweep() {
        String prefix = subnetPrefix();
        if (prefix == null) {
            Log.w(TAG, "no IPv4 address on this screen — cannot sweep");
            return null;
        }

        ExecutorService pool = Executors.newFixedThreadPool(48);
        AtomicReference<String> found = new AtomicReference<>(null);
        List<Future<?>> pending = new ArrayList<>();

        for (int host = 1; host <= 254; host++) {
            String address = prefix + host;
            pending.add(pool.submit(() -> {
                // Both, because port 80 is published for typing an address by
                // hand and 8080 is the one everything else uses.
                for (int port : new int[] {8080, 80}) {
                    if (found.get() != null) return;
                    String base = "http://" + address + (port == 80 ? "" : ":" + port);
                    if (Server.sweepable(base)) {
                        found.compareAndSet(null, base);
                        return;
                    }
                }
            }));
        }

        pool.shutdown();
        try {
            pool.awaitTermination(SWEEP_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException stop) {
            Thread.currentThread().interrupt();
        }
        pool.shutdownNow();
        for (Future<?> task : pending) task.cancel(true);

        String address = found.get();
        Log.i(TAG, address == null ? "the subnet holds no Homesh server" : "swept up " + address);
        return address;
    }

    /** "192.168.1." for this screen's own address, or null if it has none. */
    private static String subnetPrefix() {
        try {
            for (NetworkInterface nic : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (nic.isLoopback() || !nic.isUp()) continue;
                for (InetAddress address : Collections.list(nic.getInetAddresses())) {
                    if (address instanceof Inet4Address && address.isSiteLocalAddress()) {
                        String text = address.getHostAddress();
                        return text.substring(0, text.lastIndexOf('.') + 1);
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "could not read this screen's own address", e);
        }
        return null;
    }

    private static String byBroadcast() {
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
