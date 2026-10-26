package com.homesh.tv;

/**
 * When an app beside Homesh should change machines, as opposed to how.
 *
 * <p>Plain Java, so it is tested on the build machine (FollowTest) rather than
 * on a phone with poor reception, which is where it matters and where it once
 * failed. On a weak signal every answer is late, so the PC looked down to one
 * check and up to the next, and the app moved on each: a reload every few
 * seconds, back to the top of the page every time.
 *
 * <p>Two rules came out of that:
 * <ul>
 * <li>One check is never enough. The PC has to be heard the same way
 *     {@link #AGREE} times running -- a couple of minutes at the keepalive's
 *     pace -- before the other machine is so much as offered.
 * <li>Moving on its own is rationed: at most once per {@link #GAP_MS}, so if
 *     both machines are struggling it settles on one and says so, instead of
 *     trading between them.
 * </ul>
 */
final class Follow {

    /** Checks in a row that must agree before a move is offered. */
    static final int AGREE = 2;
    /** The least time between two moves nobody asked for. */
    static final long GAP_MS = 30_000;

    private int streak;
    private long lastMove = Long.MIN_VALUE / 2;

    /**
     * A keepalive's answer. True once the other machine has earned an offer:
     * on the PC, a PC that keeps not answering; on the standby, one that keeps
     * answering.
     */
    boolean heard(boolean onPc, boolean pcUp) {
        boolean away = onPc != pcUp;
        streak = away ? streak + 1 : 0;
        return streak >= AGREE;
    }

    /** Whether a move nobody asked for may happen now. */
    boolean mayMove(long now) {
        return now - lastMove >= GAP_MS;
    }

    /** A move happened, asked for or not; the counting starts again. */
    void moved(long now) {
        lastMove = now;
        streak = 0;
    }
}
