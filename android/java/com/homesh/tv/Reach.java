package com.homesh.tv;

import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Which of several addresses to use, asked of all of them at once.
 *
 * <p>Asking in turn is what made opening slow: the PC by one address, then by
 * the other, then the standby, each allowed four seconds to fail, so a PC that
 * was off cost twelve seconds of a spinner before anything opened. Asked
 * together, the answer is known as soon as the first useful reply arrives.
 *
 * <p>Plain Java, no Android, so it is tested on the build machine
 * (android/test/ReachTest.java) rather than only on a phone.
 */
final class Reach {

    /** Whether one address answers; how is up to the caller. */
    interface Probe {
        boolean ok(String address);
    }

    private Reach() {}

    /**
     * The address to use, or null when nothing answered.
     *
     * <p>{@code first} is preferred -- the PC -- and {@code second} is where to
     * go instead -- the standby. Every address is asked at once.
     * {@code first} is in order of preference: one of them is taken the moment
     * it answers if every one before it has failed, and otherwise once those
     * have answered or the time is up. The PC's secure address comes before
     * its house address, and must: at home the house address always answers
     * first, and a passkey works only on the secure one -- taking the quicker
     * of the two (1.6.0 to 1.7.2) opened Homesh where nobody could sign in. One of
     * {@code second} is taken once every {@code first} has failed, or once
     * {@code quickMs} have passed with none of them answering: a PC that takes
     * longer than that is too busy to be worth waiting for while the standby
     * stands ready. Nothing is waited for beyond {@code giveUpMs}.
     *
     * <p>A {@code quickMs} as long as {@code giveUpMs} means "the second only
     * when the first is down, not merely slow".
     */
    static String pick(String[] first, String[] second, Probe probe, long quickMs, long giveUpMs) {
        final String[] all = new String[first.length + second.length];
        System.arraycopy(first, 0, all, 0, first.length);
        System.arraycopy(second, 0, all, first.length, second.length);
        final Boolean[] answered = new Boolean[all.length];
        final Object lock = new Object();

        for (int i = 0; i < all.length; i++) {
            final int at = i;
            final String address = all[i];
            if (address == null || address.isEmpty()) {
                answered[at] = Boolean.FALSE;
                continue;
            }
            Thread t = new Thread(() -> {
                boolean ok;
                try {
                    ok = probe.ok(address);
                } catch (RuntimeException e) {
                    ok = false;
                }
                synchronized (lock) {
                    answered[at] = ok;
                    lock.notifyAll();
                }
            });
            // Never what keeps a process alive: an answer nobody waits for any
            // more is simply dropped.
            t.setDaemon(true);
            t.start();
        }

        final long start = System.nanoTime();
        synchronized (lock) {
            while (true) {
                long waited = (System.nanoTime() - start) / 1_000_000;

                boolean firstPending = false;
                String best = null;
                for (int i = 0; i < first.length; i++) {
                    if (Boolean.TRUE.equals(answered[i])) {
                        if (!firstPending) return all[i];
                        if (best == null) best = all[i];
                    }
                    if (answered[i] == null) firstPending = true;
                }
                if (best != null) {
                    // A later address of the PC answered; an earlier one may yet.
                    if (waited >= giveUpMs) return best;
                    try {
                        lock.wait(Math.max(1, giveUpMs - waited));
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return best;
                    }
                    continue;
                }

                boolean secondPending = false;
                for (int i = first.length; i < all.length; i++) {
                    if (Boolean.TRUE.equals(answered[i])) {
                        if (!firstPending || waited >= quickMs) return all[i];
                    }
                    if (answered[i] == null) secondPending = true;
                }

                if (!firstPending && !secondPending) return null;
                if (waited >= giveUpMs) return null;

                long next = waited < quickMs ? quickMs - waited : giveUpMs - waited;
                try {
                    lock.wait(Math.max(1, Math.min(next, giveUpMs - waited)));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return null;
                }
            }
        }
    }

    /**
     * Whether a web address answers at all, within the time given.
     *
     * <p>For the other apps beside Homesh, which have no /api/health to ask:
     * any reply short of a server error counts. A sign-in redirect is still a
     * machine that is up, and a dead one throws before it replies.
     */
    static boolean answers(String url, int timeoutMs) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(url).openConnection();
            conn.setConnectTimeout(timeoutMs);
            conn.setReadTimeout(timeoutMs);
            conn.setInstanceFollowRedirects(false);
            conn.setUseCaches(false);
            return conn.getResponseCode() < 500;
        } catch (Exception e) {
            return false;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
