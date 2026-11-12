package com.homesh.tv;

/** Plain-java checks, run by tools/build-tv-apk.sh before it packages anything. */
public class OriginsTest {
    private static int failures = 0;

    public static void main(String[] args) {
        String home = "http://192.0.2.5:8080";
        check(Origins.same("http://192.0.2.5:8080/tv", home), "a page of ours");
        check(Origins.same("HTTP://192.0.2.5:8080/", home), "case does not matter");
        check(!Origins.same("http://192.0.2.5:8080@evil.example/", home),
                "a user name that looks like the server is another site");
        check(!Origins.same("http://192.0.2.5:80800/", home), "a longer port is another port");
        check(!Origins.same("http://192.0.2.5.evil.example:8080/", home),
                "a longer host is another host");
        check(!Origins.same("https://192.0.2.5:8080/", home), "another scheme");
        check(Origins.same("https://box.example.ts.net/x", "https://box.example.ts.net:443"),
                "the default port is the same port");
        check(!Origins.same("javascript:alert(1)", home), "not a page at all");
        check(!Origins.same(null, home), "nothing");
        check(Origins.under("https://a.example:9101/app/x", "https://a.example:9101/app"),
                "under the base path");
        check(!Origins.under("https://a.example:9101@b.example/app", "https://a.example:9101"),
                "under, with the same trick");

        if (failures > 0) {
            System.err.println(failures + " origin check(s) failed");
            System.exit(1);
        }
        System.out.println("origins: all passed");
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            failures++;
            System.err.println("FAIL: " + what);
        }
    }
}
