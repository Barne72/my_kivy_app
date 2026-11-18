package org.mangafon;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import org.kivy.android.PythonActivity;

public class NotifyHelper {
    private static final String CH_PROGRESS = "mangafon_progress";
    private static final String CH_DONE = "mangafon_done";
    private static final int ID_PROGRESS = 1001;
    private static final int ID_DONE = 1002;

    private static Context getCtx() {
        return PythonActivity.mActivity;
    }

    private static boolean canNotify(Context ctx) {
        if (ctx == null) return false;
        if (Build.VERSION.SDK_INT >= 33) {
            return ctx.checkSelfPermission("android.permission.POST_NOTIFICATIONS") == PackageManager.PERMISSION_GRANTED;
        }
        return true;
    }

    private static void ensureChannels(NotificationManager nm) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel prog = new NotificationChannel(
                    CH_PROGRESS,
                    "Прогресс задач",
                    NotificationManager.IMPORTANCE_LOW
            );
            prog.setSound(null, null);
            prog.enableVibration(false);
            nm.createNotificationChannel(prog);

            NotificationChannel done = new NotificationChannel(
                    CH_DONE,
                    "Готовые задачи",
                    NotificationManager.IMPORTANCE_DEFAULT
            );
            nm.createNotificationChannel(done);
        }
    }

    private static int getSmallIcon(Context ctx) {
        int icon = ctx.getApplicationInfo().icon;
        if (icon != 0) return icon;
        return android.R.drawable.ic_dialog_info;
    }

    private static PendingIntent getLaunchIntent(Context ctx) {
        try {
            Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(ctx.getPackageName());
            if (launch == null) return null;
            launch.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= 23) {
                flags |= PendingIntent.FLAG_IMMUTABLE;
            }
            return PendingIntent.getActivity(ctx, 0, launch, flags);
        } catch (Throwable ignored) {
            return null;
        }
    }

    public static void showProgress(String title, String text, int percent) {
        try {
            Context ctx = getCtx();
            if (!canNotify(ctx)) return;
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            ensureChannels(nm);

            Notification.Builder b = (Build.VERSION.SDK_INT >= 26)
                    ? new Notification.Builder(ctx, CH_PROGRESS)
                    : new Notification.Builder(ctx);

            int p = Math.max(0, Math.min(100, percent));
            b.setSmallIcon(getSmallIcon(ctx))
                    .setContentTitle(title)
                    .setContentText(text)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setProgress(100, p, p <= 0);

            PendingIntent pi = getLaunchIntent(ctx);
            if (pi != null) b.setContentIntent(pi);

            nm.notify(ID_PROGRESS, b.build());
        } catch (Throwable ignored) {}
    }

    public static void cancelProgress() {
        try {
            Context ctx = getCtx();
            if (ctx == null) return;
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm != null) nm.cancel(ID_PROGRESS);
        } catch (Throwable ignored) {}
    }

    public static void showDone(String title, String text) {
        try {
            Context ctx = getCtx();
            if (!canNotify(ctx)) return;
            NotificationManager nm = (NotificationManager) ctx.getSystemService(Context.NOTIFICATION_SERVICE);
            if (nm == null) return;
            ensureChannels(nm);
            nm.cancel(ID_PROGRESS);

            Notification.Builder b = (Build.VERSION.SDK_INT >= 26)
                    ? new Notification.Builder(ctx, CH_DONE)
                    : new Notification.Builder(ctx);

            b.setSmallIcon(getSmallIcon(ctx))
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setAutoCancel(true);

            PendingIntent pi = getLaunchIntent(ctx);
            if (pi != null) b.setContentIntent(pi);

            nm.notify(ID_DONE, b.build());
        } catch (Throwable ignored) {}
    }
}
