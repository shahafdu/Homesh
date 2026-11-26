package com.homesh.tv;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.List;

/**
 * Wake-on-LAN: the packet that starts a sleeping PC's network adapter.
 *
 * <p>The adapter, still powered, watches for six 0xFF bytes followed by its own
 * address sixteen times. Nothing answers it, so it is sent three times and the
 * only way to know it worked is that the PC starts answering. It is a local
 * broadcast: no router carries it, so it works only from the house network.
 *
 * <p>Plain Java, no Android, so it is tested on the build machine
 * (android/test/WakeTest.java). Which network to send on is the caller's.
 */
final class Wake {

    /** The "discard" port, which is the custom; the adapter reads any. */
    static final int PORT = 9;
    static final int TIMES = 3;
    private static final long GAP_MS = 100;

    /** Ties the socket to one network before it sends, where the phone can. */
    interface Link {
        void bind(DatagramSocket socket) throws IOException;
    }

    private Wake() {}

    /** The six bytes of "aa:bb:cc:dd:ee:ff" or "AA-BB-...", or null. */
    static byte[] address(String mac) {
        if (mac == null || mac.length() != 17) return null;
        char separator = mac.charAt(2);
        if (separator != ':' && separator != '-') return null;
        byte[] out = new byte[6];
        for (int i = 0; i < 6; i++) {
            int at = i * 3;
            if (i > 0 && mac.charAt(at - 1) != separator) return null;
            int high = hex(mac.charAt(at));
            int low = hex(mac.charAt(at + 1));
            if (high < 0 || low < 0) return null;
            out[i] = (byte) (high << 4 | low);
        }
        return out;
    }

    /** ASCII only: Character.digit would also take other scripts' digits. */
    private static int hex(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }

    /** The 102 bytes for an address from {@link #address}. */
    static byte[] packet(byte[] address) {
        byte[] out = new byte[6 + 16 * 6];
        for (int i = 0; i < 6; i++) out[i] = (byte) 0xFF;
        for (int copy = 0; copy < 16; copy++) {
            System.arraycopy(address, 0, out, 6 + copy * 6, 6);
        }
        return out;
    }

    /**
     * The broadcast address of an IPv4 network, from the phone's own address on
     * it and the prefix length. Null where there is none to speak of.
     */
    static byte[] broadcast(byte[] ipv4, int prefix) {
        if (ipv4 == null || ipv4.length != 4 || prefix < 1 || prefix > 30) return null;
        byte[] out = ipv4.clone();
        for (int bit = prefix; bit < 32; bit++) {
            out[bit / 8] |= (byte) (0x80 >>> (bit % 8));
        }
        return out;
    }

    /**
     * Send the packet to every target, three times, on a thread of its own.
     * False, with nothing sent, when the address is not one.
     */
    static boolean send(String mac, List<InetAddress> targets, int port, Link link) {
        byte[] address = address(mac);
        if (address == null) return false;
        byte[] bytes = packet(address);
        new Thread(() -> {
            try (DatagramSocket socket = new DatagramSocket()) {
                socket.setBroadcast(true);
                if (link != null) {
                    try {
                        link.bind(socket);
                    } catch (IOException e) {
                        // A VPN may forbid it. The directed broadcast still
                        // leaves by the house network's own route.
                    }
                }
                for (int i = 0; i < TIMES; i++) {
                    if (i > 0) Thread.sleep(GAP_MS);
                    for (InetAddress target : targets) {
                        try {
                            socket.send(new DatagramPacket(bytes, bytes.length, target, port));
                        } catch (IOException e) {
                            // One target refused is no reason to skip the others.
                        }
                    }
                }
            } catch (IOException e) {
                // No socket: the watch for the PC says it did not wake.
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }).start();
        return true;
    }
}
