package com.homesh.tv;

/** Plain-java checks, run by tools/build-tv-apk.sh before it packages anything. */
public class NotifyRulesTest {
    private static int failures = 0;

    public static void main(String[] args) {
        // The server's own cases (server/tests/test_notify.py), word for word.
        for (String ok : new String[] {"/", "/a/b?c=d#e", "/inbox", "/a-b_c/%20x"}) {
            check(NotifyRules.validPath(ok), "accepted: " + ok);
        }
        String[] bad = {"//evil.example", "/\\evil", "http://x", "/a//b", "/a\nb", "/a\rb", "",
                "inbox", "/" + "a".repeat(512), "/user@evil.example/x", "/javascript:alert(1)",
                "/a\u202Eb", "/a\u2028b"};
        for (String path : bad) check(!NotifyRules.validPath(path), "refused: " + path);
        check(!NotifyRules.validPath(null), "refused: nothing");
        check(NotifyRules.validPath("/" + "a".repeat(511)), "512 characters is the limit");
        check(NotifyRules.validPath("/a/b@c"), "an @ after the first segment is only text");

        check(NotifyRules.notificationId("AppOne", "pending", 1)
                == NotifyRules.notificationId("AppOne", "pending", 9),
                "same sender and tag, same notification");
        check(NotifyRules.notificationId("AppOne", "pending", 1)
                != NotifyRules.notificationId("AppTwo", "pending", 1),
                "another sender, another notification");
        check(NotifyRules.notificationId("AppOne", null, 5) != NotifyRules.notificationId("AppOne", null, 6),
                "no tag: one notification per message");
        check(NotifyRules.notificationId("AppOne", "", 7) == 7, "an empty tag is no tag");

        check(!NotifyRules.cursorKey("primary").equals(NotifyRules.cursorKey("standby")),
                "one cursor for each machine");
        check(NotifyRules.cursorKey("anything").equals(NotifyRules.cursorKey("primary")),
                "an unknown role is the PC's");

        String house = "http://192.0.2.5:8080";
        String outside = "https://box.example.ts.net";
        check(house.equals(NotifyRules.match("http://192.0.2.5:8080", house, outside)),
                "the house address");
        check(outside.equals(NotifyRules.match("https://box.example.ts.net/", null, outside)),
                "the outside address");
        check(NotifyRules.match("https://evil.example", house, outside) == null, "an unknown origin");
        check(NotifyRules.match("http://192.0.2.5:8080@evil.example", house, outside) == null,
                "a user name that looks like ours");
        check(NotifyRules.match(null, house) == null, "no origin");
        check(NotifyRules.match("http://192.0.2.5:8080") == null, "nothing held");

        check(NotifyRules.validClaim("abcDEF012345_-xyzABCDEF0123456789_-abcdefg"), "a claim code");
        check(!NotifyRules.validClaim("short"), "too short to be a claim");
        check(!NotifyRules.validClaim("abcDEF012345_-xyz/ABCDEF0123456789"), "a slash is not url-safe");
        check(!NotifyRules.validClaim(null), "no claim");

        check(NotifyRules.removedByPc("primary"), "the PC's 401 removes the phone");
        check(!NotifyRules.removedByPc("standby"), "the standby's 401 does not");
        check(!NotifyRules.removedByPc(null), "an unreadable 401 does not");
        check(!NotifyRules.removedByPc(""), "a 401 naming no role does not");

        if (failures > 0) {
            System.err.println(failures + " notify check(s) failed");
            System.exit(1);
        }
        System.out.println("notify rules: all passed");
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            failures++;
            System.err.println("FAIL: " + what);
        }
    }
}
