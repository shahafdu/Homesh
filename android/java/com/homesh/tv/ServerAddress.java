package com.homesh.tv;

/**
 * Turning what somebody typed on a remote control into a base URL.
 *
 * <p>Kept free of any Android import on purpose: this is the only real logic in
 * the app, it runs on input typed with a D-pad, and being plain Java means it
 * can be tested on the build machine rather than only on a television.
 */
public final class ServerAddress {

    private ServerAddress() {}

    static final int DEFAULT_PORT = 8080;

    /**
     * Accepts "192.0.2.5", "192.0.2.5:9000", "http://box.lan:8080" or
     * "box.lan/" and returns a base URL with no trailing slash. Returns null
     * when there is nothing usable in it.
     *
     * <p>The scheme and the port are optional because typing punctuation on an
     * on-screen keyboard is miserable and every character is a chance to get it
     * wrong on a screen you cannot easily correct.
     */
    public static String normalise(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        if (s.isEmpty()) return null;

        // Separate the scheme first and put it back at the end. Working on the
        // address alone is what keeps the tidying below from reaching into it:
        // stripping trailing slashes off the whole string turns "http://" into
        // "http:", which is not an address but looks enough like one to be saved.
        String scheme = "http://";
        if (s.startsWith("http://")) {
            s = s.substring(7);
        } else if (s.startsWith("https://")) {
            scheme = "https://";
            s = s.substring(8);
        }

        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        if (s.isEmpty()) return null;  // a scheme and nothing else

        // Only the host decides whether a port is missing, so a colon appearing
        // later in a path cannot be mistaken for one.
        int slash = s.indexOf('/');
        String host = slash < 0 ? s : s.substring(0, slash);
        String path = slash < 0 ? "" : s.substring(slash);
        if (host.isEmpty()) return null;

        if (host.indexOf(':') < 0) {
            host = host + ":" + DEFAULT_PORT;
        }
        return scheme + host + path;
    }
}
