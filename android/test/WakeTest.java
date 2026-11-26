package com.homesh.tv;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Plain-java checks, run by tools/build-tv-apk.sh before it packages anything. */
public class WakeTest {
    private static int failures = 0;

    /** From the block RFC 7042 sets aside for documentation. */
    private static final String MAC = "00:00:5e:00:53:af";

    public static void main(String[] args) throws Exception {
        byte[] address = {0x00, 0x00, 0x5E, 0x00, 0x53, (byte) 0xAF};
        check(Arrays.equals(Wake.address(MAC), address), "colons");
        check(Arrays.equals(Wake.address("00-00-5E-00-53-AF"), address), "dashes and capitals");
        for (String bad : new String[] {null, "", "00:00:5e:00:53", "00:00:5e:00:53:af:01",
                "00005e0053af", "00:00-5e:00:53:af", "00.00.5e.00.53.af", "00:00:5e:00:53:ag",
                "+0:00:5e:00:53:af"}) {
            check(Wake.address(bad) == null, "refused: " + bad);
        }

        byte[] packet = Wake.packet(address);
        // Written out rather than built the way the code builds it.
        StringBuilder expected = new StringBuilder("ffffffffffff");
        for (int i = 0; i < 16; i++) expected.append("00005e0053af");
        check(packet.length == 102, "102 bytes");
        check(hex(packet).equals(expected.toString()), "the packet, byte for byte");

        byte[] phone = {(byte) 192, 0, 2, 77};
        check(Arrays.equals(Wake.broadcast(phone, 24), new byte[] {(byte) 192, 0, 2, (byte) 255}),
                "broadcast of a /24");
        check(Arrays.equals(Wake.broadcast(phone, 25), new byte[] {(byte) 192, 0, 2, 127}),
                "broadcast of a /25");
        check(Arrays.equals(Wake.broadcast(phone, 20),
                new byte[] {(byte) 192, 0, 15, (byte) 255}), "broadcast of a /20");
        check(Wake.broadcast(phone, 32) == null, "one host has no broadcast");
        check(Wake.broadcast(phone, 0) == null, "no prefix, no broadcast");
        check(Wake.broadcast(new byte[16], 24) == null, "not IPv4");

        try (DatagramSocket pc = new DatagramSocket(0, InetAddress.getLoopbackAddress())) {
            List<InetAddress> here = Collections.singletonList(InetAddress.getLoopbackAddress());

            check(!Wake.send("not an address", here, pc.getLocalPort(), null),
                    "a malformed address is refused");
            check(receive(pc, 500) == null, "and sends nothing");

            // A socket that cannot be tied to a network still sends.
            check(Wake.send(MAC, here, pc.getLocalPort(), socket -> {
                throw new java.io.IOException("not allowed");
            }), "sent");
            for (int i = 0; i < Wake.TIMES; i++) {
                check(Arrays.equals(receive(pc, 2000), packet), "copy " + (i + 1) + " arrived");
            }
            check(receive(pc, 500) == null, "three copies and no more");
        }

        if (failures > 0) {
            System.err.println(failures + " wake check(s) failed");
            System.exit(1);
        }
        System.out.println("wake: all passed");
    }

    private static byte[] receive(DatagramSocket socket, int waitMs) throws Exception {
        socket.setSoTimeout(waitMs);
        DatagramPacket got = new DatagramPacket(new byte[512], 512);
        try {
            socket.receive(got);
        } catch (SocketTimeoutException e) {
            return null;
        }
        return Arrays.copyOf(got.getData(), got.getLength());
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format("%02x", b & 0xFF));
        return out.toString();
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            failures++;
            System.err.println("FAIL: " + what);
        }
    }
}
