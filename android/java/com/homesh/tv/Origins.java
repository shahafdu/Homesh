package com.homesh.tv;

import java.net.URI;
import java.util.Locale;

/**
 * Whether an address belongs to a given server: scheme, host and port, parsed.
 *
 * <p>Both apps used to ask whether a URL began with the server's address.
 * {@code http://server@another.site/} begins with {@code http://server}, and
 * is a page on another site -- the part before the {@code @} is a user name.
 * Such a page would have loaded inside the app with its JavaScript bridge
 * attached. Parsed, the host is {@code another.site} and the answer is no.
 */
final class Origins {
    private Origins() {}

    /** True when {@code url} is on the same scheme, host and port as {@code base}. */
    static boolean same(String url, String base) {
        if (url == null || base == null) return false;
        try {
            URI a = new URI(url);
            URI b = new URI(base);
            if (a.getRawUserInfo() != null) return false;
            String sa = lower(a.getScheme());
            String sb = lower(b.getScheme());
            String ha = lower(a.getHost());
            String hb = lower(b.getHost());
            if (sa == null || ha == null || !sa.equals(sb) || !ha.equals(hb)) return false;
            return port(a) == port(b);
        } catch (Exception e) {
            return false;
        }
    }

    /** {@link #same}, and under the base's path too. */
    static boolean under(String url, String base) {
        return same(url, base) && url.startsWith(base);
    }

    private static String lower(String s) {
        return s == null ? null : s.toLowerCase(Locale.ROOT);
    }

    private static int port(URI u) {
        if (u.getPort() != -1) return u.getPort();
        return "https".equalsIgnoreCase(u.getScheme()) ? 443 : 80;
    }
}
