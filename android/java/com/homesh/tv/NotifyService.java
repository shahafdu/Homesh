package com.homesh.tv;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * Instant: a request held open at the server, so a message arrives in seconds
 * instead of within fifteen minutes. Costs a permanent notification, which
 * Android requires of anything that keeps running, so it is a switch the person
 * turns on and not the default.
 *
 * <p>Foreground service type "remoteMessaging", declared in the manifest. It
 * is the type Android gives to delivering messages between a person's own
 * devices, which is what this is. "dataSync" would have been the easy choice
 * and is the wrong one: from Android 15 it is cut off after a few hours a day,
 * which would stop this silently in the middle of the night.
 *
 * <p>The background job stays scheduled beside it as the safety net.
 */
public final class NotifyService extends Service {

    static final String ACTION_STOP = "com.homesh.tv.NOTIFY_STOP";
    private static final String CHANNEL = "listening";
    private static final int ID = 2;
    private static final long[] BACKOFF_MS = {5_000, 15_000, 60_000, 300_000};

    private volatile boolean running;
    private Thread worker;

    static void start(Context context) {
        Intent go = new Intent(context, NotifyService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(go);
        else context.startService(go);
    }

    static void stop(Context context) {
        context.stopService(new Intent(context, NotifyService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            Vault.put(this, Mailbox.KEY_INSTANT, null);
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!Mailbox.on(this)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        listening();
        if (!running) {
            running = true;
            final Context context = getApplicationContext();
            worker = new Thread(() -> loop(context));
            worker.start();
        }
        return START_STICKY;
    }

    private void loop(Context context) {
        int failures = 0;
        while (running && Mailbox.on(context)) {
            int n = Mailbox.check(context, 50);
            if (n >= 0) {
                failures = 0;
                continue;
            }
            long pause = BACKOFF_MS[Math.min(failures, BACKOFF_MS.length - 1)];
            failures++;
            try {
                Thread.sleep(pause);
            } catch (InterruptedException e) {
                return;
            }
        }
        if (running) stopSelf();
    }

    private void listening() {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Listening",
                    NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL);
        } else {
            builder = new Notification.Builder(this);
        }
        PendingIntent stop = PendingIntent.getService(this, 0,
                new Intent(this, NotifyService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, PhoneActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        builder.setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle("Homesh is listening for messages")
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Turn off instant", stop).build());
        Notification notification = builder.build();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING);
        } else {
            startForeground(ID, notification);
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
