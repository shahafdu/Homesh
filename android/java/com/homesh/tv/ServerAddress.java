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
     * Pages this app sends people to, which they then paste back in here.
     *
     * <p>Not a general path cleanup. These are the four addresses the app and
     * its documentation actually hand out — the download pages for the two
     * APKs, and the television interface — so pasting one back is the ordinary
     * mistake rather than an exotic one. Anything else somebody types after the
     * host is left alone, because a server behind a proxy really can live under
     * a path and this has no way to tell that apart from a typo.
     */
    private static final String[] LANDING_PAGES = {"/phone", "/apk", "/tv.apk", "/tv"};

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
        boolean saidScheme = false;
        if (s.startsWith("http://")) {
            s = s.substring(7);
            saidScheme = true;
        } else if (s.startsWith("https://")) {
            scheme = "https://";
            s = s.substring(8);
            saidScheme = true;
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

        // A tailnet name is always HTTPS on 443, whether or not that was typed.
        // Tailscale terminates TLS for the name itself, so there is no version
        // of one of these that wants a scheme of http or a port of 8080 — and
        // this is the address people are given for reaching the server from
        // outside the house, so getting it wrong fails exactly when they are
        // least able to investigate.
        if (!saidScheme && host.endsWith(".ts.net")) {
            scheme = "https://";
        }

        // Only http gets a port filled in. HTTPS means 443, and appending 8080
        // to it produced "https://<name>.ts.net:8080" — an address nothing has
        // ever answered on, reported back as though the server were down.
        if (host.indexOf(':') < 0 && scheme.equals("http://")) {
            host = host + ":" + DEFAULT_PORT;
        }

        path = withoutLandingPage(path);
        return scheme + host + path;
    }

    /** Drop a page this app told somebody to visit, leaving the server itself. */
    private static String withoutLandingPage(String path) {
        for (String page : LANDING_PAGES) {
            if (path.equalsIgnoreCase(page)) return "";
        }
        return path;
    }
}
