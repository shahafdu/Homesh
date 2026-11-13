package com.homesh.tv;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/** After a restart: bring back what the person had switched on. */
public final class NotifyBoot extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        if (!Mailbox.on(context)) return;
        NotifyJob.schedule(context);
        if (Mailbox.instant(context)) {
            try {
                NotifyService.start(context);
            } catch (RuntimeException e) {
                // Refused by the system: the fifteen-minute job still runs.
                Log.i("HomeshNotify", "instant not restarted: " + e.getClass().getSimpleName());
            }
        }
    }
}
