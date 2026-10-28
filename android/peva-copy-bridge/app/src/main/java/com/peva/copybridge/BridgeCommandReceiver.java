package com.peva.copybridge;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/** Receives only authenticated, local ADB commands to arm or stop the bridge. */
public final class BridgeCommandReceiver extends BroadcastReceiver {
    public static final String ACTION_ARM = "com.peva.copybridge.ARM";
    public static final String ACTION_STOP = "com.peva.copybridge.STOP";
    public static final String ACTION_OCR_ADB_SCREENSHOT = "com.peva.copybridge.OCR_ADB_SCREENSHOT";
    public static final String EXTRA_TOKEN = "token";
    public static final String EXTRA_WINDOW_MS = "window_ms";
    public static final String EXTRA_MAX_COPIES = "max_copies";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !BridgeAuth.matches(context, intent.getStringExtra(EXTRA_TOKEN))) {
            return;
        }

        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            context.stopService(new Intent(context, ClipboardBridgeService.class));
            context.stopService(new Intent(context, ScreenOcrService.class));
            return;
        }
        if (ACTION_OCR_ADB_SCREENSHOT.equals(action)) {
            Intent service = new Intent(context, ScreenOcrService.class)
                    .setAction(ScreenOcrService.ACTION_PROCESS_ADB_SCREENSHOT)
                    .putExtra(
                            ScreenOcrService.EXTRA_IMAGE_PATH,
                            intent.getStringExtra(ScreenOcrService.EXTRA_IMAGE_PATH)
                    );
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(service);
            } else {
                context.startService(service);
            }
            return;
        }
        if (!ACTION_ARM.equals(action)) {
            return;
        }

        Intent service = new Intent(context, ClipboardBridgeService.class)
                .setAction(ClipboardBridgeService.ACTION_ARM)
                .putExtra(EXTRA_WINDOW_MS, intent.getIntExtra(EXTRA_WINDOW_MS, 8000))
                .putExtra(EXTRA_MAX_COPIES, intent.getIntExtra(EXTRA_MAX_COPIES, 8));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(service);
        } else {
            context.startService(service);
        }
    }
}
