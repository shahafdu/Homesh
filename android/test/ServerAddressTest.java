import com.homesh.tv.ServerAddress;

/**
 * Plain-java checks on the one piece of real logic in the TV shell.
 *
 * <p>No JUnit: adding a test framework would mean adding the first dependency
 * the app has managed to avoid, for eleven assertions. Run by
 * tools/build-tv-apk.sh before it packages anything.
 */
public class ServerAddressTest {

    private static int failures = 0;

    public static void main(String[] args) {
        // The forms someone will actually type on a remote.
        eq("192.0.2.5", "http://192.0.2.5:8080", "bare address gets scheme and port");
        eq("192.0.2.5:9000", "http://192.0.2.5:9000", "an explicit port is kept");
        eq("box.lan", "http://box.lan:8080", "a hostname works the same way");
        eq("http://box.lan:8080", "http://box.lan:8080", "a complete URL is unchanged");
        eq("https://homesh.example:443", "https://homesh.example:443", "https survives");

        // Sloppiness that should not become a black screen.
        eq("  192.0.2.5  ", "http://192.0.2.5:8080", "surrounding space is ignored");
        eq("box.lan/", "http://box.lan:8080", "a trailing slash is dropped");
        eq("box.lan///", "http://box.lan:8080", "several trailing slashes too");

        // A path must not be mistaken for a port, and the port still lands on
        // the host rather than at the end of the string.
        eq("box.lan/homesh", "http://box.lan:8080/homesh", "a path keeps its place");

        // Nothing usable.
        eq(null, null, "null in, null out");
        eq("", null, "empty is not an address");
        eq("   ", null, "whitespace is not an address");
        eq("http://", null, "a scheme alone is not an address");

        if (failures > 0) {
            System.out.println("\n" + failures + " failed");
            System.exit(1);
        }
        System.out.println("ServerAddress: all checks passed");
    }

    private static void eq(String input, String expected, String why) {
        String actual = ServerAddress.normalise(input);
        boolean ok = expected == null ? actual == null : expected.equals(actual);
        if (!ok) {
            failures++;
            System.out.println("FAIL  " + why
                    + "\n      normalise(" + quote(input) + ")"
                    + "\n      expected " + quote(expected)
                    + "\n      got      " + quote(actual));
        }
    }

    private static String quote(String s) {
        return s == null ? "null" : "\"" + s + "\"";
    }
}
