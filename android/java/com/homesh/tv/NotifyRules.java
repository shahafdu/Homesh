package com.homesh.tv;

/**
 * The parts of notifications that are decisions rather than Android calls, in
 * plain Java so the build machine tests them (test/NotifyRulesTest.java).
 *
 * <p>The services, channels and notifications themselves cannot be tested
 * without a phone; what must never be wrong is kept here instead.
 */
final class NotifyRules {

    static final int MAX_PATH = 512;
    static final String HOMESH = "Homesh";

    private NotifyRules() {}

    /**
     * Whether {@code path}, appended to an app's address, stays inside that app.
     *
     * <p>A copy of the server's {@code notify.valid_path}, with the same cases
     * in the tests. A message carries a path and never an address; this is the
     * second place that is enforced, because a notification is tapped on a
     * phone that trusts nothing it was handed.
     */
    static boolean validPath(String path) {
        if (path == null || path.isEmpty() || path.length() > MAX_PATH) return false;
        if (path.charAt(0) != '/' || path.contains("//") || path.indexOf('\\') >= 0) return false;
        for (int i = 0; i < path.length(); ) {
            int cp = path.codePointAt(i);
            int type = Character.getType(cp);
            if (type == Character.CONTROL || type == Character.FORMAT
                    || type == Character.LINE_SEPARATOR || type == Character.PARAGRAPH_SEPARATOR) {
                return false;
            }
            i += Character.charCount(cp);
        }
        int slash = path.indexOf('/', 1);
        String first = slash < 0 ? path.substring(1) : path.substring(1, slash);
        return first.indexOf('@') < 0 && first.indexOf(':') < 0;
    }

    /**
     * The notification's id: the same for the same sender and tag, so a newer
     * message replaces the older ("3 waiting" becoming "4 waiting"); the
     * message's own id when it has no tag.
     */
    static int notificationId(String sender, String tag, long messageId) {
        if (tag == null || tag.isEmpty()) return (int) (messageId ^ (messageId >>> 32));
        return (sender + "/" + tag).hashCode();
    }

    /** Where the cursor for one machine's mailbox is kept. Ids are per machine. */
    static String cursorKey(String role) {
        return "standby".equals(role) ? "notify.cursor.standby" : "notify.cursor.primary";
    }

    /**
     * Which held address a link's origin names, or null when none. A link
     * asking to turn notifications on is honoured only for an address the app
     * already holds for the PC: any web page can produce such a link, and this
     * is what stops one pointing the phone at somebody else's mailbox. The held
     * address is returned so the claim is sent there, never to the link's own
     * spelling of it.
     */
    static String match(String origin, String... held) {
        if (origin == null || origin.isEmpty()) return null;
        for (String address : held) {
            if (address != null && !address.isEmpty() && Origins.same(origin, address)) {
                return address;
            }
        }
        return null;
    }

    /** A claim code is url-safe base64 of 32 bytes; anything else is not one. */
    static boolean validClaim(String claim) {
        if (claim == null || claim.length() < 16 || claim.length() > 128) return false;
        for (int i = 0; i < claim.length(); i++) {
            char c = claim.charAt(i);
            boolean ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                    || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '=';
            if (!ok) return false;
        }
        return true;
    }

    /**
     * What a 401 from the inbox means. Only the PC's refusal says the phone
     * was removed. The standby cannot know of a phone registered since the
     * last backup, so its 401 -- or one whose body cannot be read, or names no
     * role -- never deletes anything.
     */
    static boolean removedByPc(String role) {
        return "primary".equals(role);
    }

    /**
     * Whether the machine that answered is worth asking first next time. Only
     * the PC's: once the standby has answered, the PC must still be tried at
     * every check, or the phone would stay with the standby after the PC came
     * back and miss what was posted there.
     */
    static boolean remember(String role) {
        return !"standby".equals(role);
    }
}
