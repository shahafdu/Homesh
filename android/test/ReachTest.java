package com.homesh.tv;

import java.util.HashMap;
import java.util.Map;

/**
 * Plain-java checks on choosing between the PC and the standby. Run by
 * tools/build-tv-apk.sh before it packages anything, like ServerAddressTest.
 *
 * <p>The probes here are fakes that answer yes or no after a set delay, which
 * is the whole of what the choice depends on.
 */
public class ReachTest {

    private static int failures = 0;

    public static void main(String[] args) {
        String[] pc = {"pc"};
        String[] standby = {"standby"};

        eq(pick(pc, standby, 500, 2000, "pc", 50, true, "standby", 10, true), "pc",
                "the PC is taken when it answers promptly, even after the standby");
        eq(pick(pc, standby, 500, 2000, "pc", 50, false, "standby", 10, true), "standby",
                "the standby when the PC says no");
        eq(pick(pc, standby, 300, 2000, "pc", 1500, true, "standby", 10, true), "standby",
                "the standby when the PC is too slow to wait for");
        eq(pick(pc, standby, 300, 2000, "pc", 600, true, "standby", 1000, true), "pc",
                "a slow PC still wins when the standby is slower");
        eq(pick(pc, standby, 300, 500, "pc", 5000, true, "standby", 5000, true), null,
                "nothing, once the time is up");
        eq(pick(pc, standby, 300, 2000, "pc", 10, false, "standby", 10, false), null,
                "nothing, as soon as everything has said no");
        eq(pick(new String[] {"origin", "lan"}, standby, 2000, 2000,
                "origin", 400, false, "lan", 50, true, "standby", 10, true), "lan",
                "either of the PC's addresses will do");
        eq(pick(new String[] {"origin", "lan"}, standby, 2000, 2000,
                "origin", 100, false, "lan", 300, false, "standby", 1000, true), "standby",
                "with no quick limit, the standby only once the PC has failed");
        eq(pick(new String[] {null, ""}, standby, 2000, 2000, "standby", 10, true), "standby",
                "addresses not known yet are skipped, not waited on");

        long t0 = System.nanoTime();
        pick(pc, standby, 300, 3000, "pc", 2900, false, "standby", 40, true);
        long took = (System.nanoTime() - t0) / 1_000_000;
        check(took < 1000, "a busy PC costs the quick limit, not the full wait (" + took + " ms)");

        if (failures > 0) {
            System.out.println(failures + " failed");
            System.exit(1);
        }
        System.out.println("ReachTest: all passed");
    }

    /** A fake probe per address: {address, delay ms, answer, ...}. */
    private static String pick(String[] first, String[] second, long quick, long giveUp,
                               Object... spec) {
        Map<String, Object[]> fakes = new HashMap<>();
        for (int i = 0; i < spec.length; i += 3) {
            fakes.put((String) spec[i], new Object[] {spec[i + 1], spec[i + 2]});
        }
        return Reach.pick(first, second, address -> {
            Object[] f = fakes.get(address);
            try {
                Thread.sleep((Integer) f[0]);
            } catch (InterruptedException e) {
                return false;
            }
            return (Boolean) f[1];
        }, quick, giveUp);
    }

    private static void eq(String actual, String expected, String what) {
        boolean ok = expected == null ? actual == null : expected.equals(actual);
        check(ok, what + " -- got " + actual + ", wanted " + expected);
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }
}
