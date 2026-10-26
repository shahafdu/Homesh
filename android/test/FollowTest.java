package com.homesh.tv;

/**
 * Plain-java checks on when an app changes machines. Run by
 * tools/build-tv-apk.sh before it packages anything, like ReachTest.
 */
public class FollowTest {

    private static int failures = 0;

    public static void main(String[] args) {
        Follow f = new Follow();
        check(!f.heard(true, false), "one missed check on the PC offers nothing");
        check(f.heard(true, false), "two in a row offer the standby");

        f = new Follow();
        boolean offered = false;
        for (int i = 0; i < 20; i++) offered |= f.heard(false, i % 2 == 0);
        check(!offered, "a PC that answers every other check is never offered back");

        f = new Follow();
        f.heard(false, true);
        check(!f.heard(false, false), "a miss in between starts the count again");
        f.heard(false, true);
        check(f.heard(false, true), "and two answers in a row then offer the PC");

        f = new Follow();
        check(!f.heard(true, true) && !f.heard(true, true), "a PC that answers keeps the app where it is");
        check(!f.heard(false, false) && !f.heard(false, false), "so does a standby while the PC is off");

        f = new Follow();
        check(f.mayMove(0), "the first move is allowed at once");
        f.heard(true, false);
        f.moved(1000);
        check(!f.mayMove(1000 + Follow.GAP_MS - 1), "a second one waits out the gap");
        check(f.mayMove(1000 + Follow.GAP_MS), "and is allowed after it");
        check(!f.heard(false, true), "a move starts the count again");

        if (failures > 0) {
            System.out.println(failures + " failed");
            System.exit(1);
        }
        System.out.println("FollowTest: all passed");
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            failures++;
            System.out.println("FAIL " + what);
        }
    }
}
