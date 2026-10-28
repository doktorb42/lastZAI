package com.peva.copybridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Receives Last Z's explicit Copy action only during a short capture window.
 * It does not inspect the clipboard outside that window and stops itself when
 * the window ends. Text is retained briefly in app-private storage so the
 * local PEva runtime can collect a whole burst of copied bubbles.
 */
public final class ClipboardBridgeService extends Service {
    public static final String PREFS = "peva_copy_bridge";
    public static final String ACTION_ARM = "com.peva.copybridge.service.ARM";

    private static final String CHANNEL_ID = "peva_copy_bridge";
    private static final int NOTIFICATION_ID = 41;
    private static final int MIN_WINDOW_MS = 1000;
    private static final int MAX_WINDOW_MS = 20000;
    private static final int MAX_COPIES = 12;
    private static final int CLIPBOARD_POLL_INTERVAL_MS = 200;

    private ClipboardManager clipboard;
    private ClipboardManager.OnPrimaryClipChangedListener listener;
    private SharedPreferences preferences;
    private Handler handler;
    private final AtomicBoolean handling = new AtomicBoolean(false);

    private long armedUntilElapsed;
    private int maxCopies;
    private int capturedCopies;
    private boolean listenerRegistered;
    private boolean finalStatusWritten;

    private final Runnable windowExpired = () -> finishCaptureWindow("Okno odbioru wygasło");
    private final Runnable clipboardPoll = new Runnable() {
        @Override
        public void run() {
            captureCopiedText();
            if (isArmed()) {
                handler.postDelayed(this, CLIPBOARD_POLL_INTERVAL_MS);
            }
        }
    };

    @Override
    public void onCreate() {
        super.onCreate();
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        clipboard = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        handler = new Handler(Looper.getMainLooper());
        BridgeAuth.token(this); // Creates the ADB pairing secret once, privately.
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_ARM.equals(intent.getAction())) {
            beginCaptureWindow(
                    intent.getIntExtra(BridgeCommandReceiver.EXTRA_WINDOW_MS, 8000),
                    intent.getIntExtra(BridgeCommandReceiver.EXTRA_MAX_COPIES, 8)
            );
        } else {
            finishCaptureWindow("Brak prawidłowego zlecenia odbioru");
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        handler.removeCallbacks(windowExpired);
        handler.removeCallbacks(clipboardPoll);
        removeListener();
        if (!finalStatusWritten) {
            armedUntilElapsed = 0L;
            preferences.edit()
                    .putString("status", "Zatrzymany — schowek nie jest odczytywany")
                    .putLong("armed_until", 0L)
                    .apply();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void beginCaptureWindow(int requestedWindowMs, int requestedMaxCopies) {
        if (clipboard == null) {
            finishCaptureWindow("Brak dostępu do schowka Androida");
            return;
        }
        int windowMs = Math.max(MIN_WINDOW_MS, Math.min(MAX_WINDOW_MS, requestedWindowMs));
        maxCopies = Math.max(1, Math.min(MAX_COPIES, requestedMaxCopies));
        capturedCopies = 0;
        finalStatusWritten = false;
        armedUntilElapsed = SystemClock.elapsedRealtime() + windowMs;

        // A repeated Last Z message can be identical to the previous primary
        // clip, in which case Android is allowed not to publish a new change
        // callback. Clear the user-visible clipboard only inside this explicit,
        // short capture window and before installing the listener. The next
        // Copy is therefore always a fresh change; it is cleared again after
        // capture by clearOnlyIfUnchanged().
        clearClipboardBeforeCapture();
        if (!listenerRegistered) {
            listener = this::captureCopiedText;
            clipboard.addPrimaryClipChangedListener(listener);
            listenerRegistered = true;
        }
        startForeground(NOTIFICATION_ID, createNotification("Czeka na kopie z Last Z"));
        handler.removeCallbacks(windowExpired);
        handler.removeCallbacks(clipboardPoll);
        handler.postDelayed(windowExpired, windowMs);
        // Some emulator builds do not deliver clipboard-change callbacks from
        // a game process to a background service consistently. This bounded
        // 0.2-second fallback exists only while the user explicitly armed the
        // bridge; it has no work outside that short window.
        handler.postDelayed(clipboardPoll, CLIPBOARD_POLL_INTERVAL_MS);
        preferences.edit()
                .putString("status", "Gotowy przez " + (windowMs / 1000.0) + " s; maks. " + maxCopies + " kopii")
                .putLong("armed_until", System.currentTimeMillis() + windowMs)
                .putInt("captured_in_window", 0)
                .apply();
    }

    private void captureCopiedText() {
        if (!isArmed() || !handling.compareAndSet(false, true)) {
            return;
        }
        try {
            ClipData data = clipboard.getPrimaryClip();
            if (data == null || data.getItemCount() == 0) {
                return;
            }
            CharSequence copied = data.getItemAt(0).coerceToText(this);
            if (copied == null || copied.length() == 0) {
                return;
            }

            String text = copied.toString();
            String fingerprint = sha256(text);
            long capturedAt = System.currentTimeMillis();
            int pending = ClipboardInbox.enqueue(this, text, fingerprint, capturedAt);
            capturedCopies++;

            // Do not erase a later user copy that replaced the captured item.
            clearOnlyIfUnchanged(fingerprint);
            preferences.edit()
                    .putString("status", "Odebrano " + capturedCopies + " kopię; w prywatnej kolejce: " + pending)
                    .putInt("last_length", text.length())
                    .putString("last_sha256", fingerprint)
                    .putLong("captured_at", capturedAt)
                    .putInt("captured_in_window", capturedCopies)
                    .apply();

            if (capturedCopies >= maxCopies) {
                finishCaptureWindow("Odebrano pełną serię: " + capturedCopies + " kopii");
            }
        } finally {
            handling.set(false);
        }
    }

    private boolean isArmed() {
        return armedUntilElapsed > SystemClock.elapsedRealtime();
    }

    private void clearOnlyIfUnchanged(String expectedFingerprint) {
        ClipData latest = clipboard.getPrimaryClip();
        if (latest == null || latest.getItemCount() == 0) {
            return;
        }
        CharSequence latestText = latest.getItemAt(0).coerceToText(this);
        if (latestText == null || !expectedFingerprint.equals(sha256(latestText.toString()))) {
            return;
        }
        try {
            // BlueStacks' Android 9 ClipboardService crashes the caller on
            // clearPrimaryClip() when it creates a null ClipData. A real,
            // empty clip has the same user-visible result and keeps the
            // bounded capture session alive for the next message in a burst.
            clipboard.setPrimaryClip(ClipData.newPlainText("", ""));
        } catch (RuntimeException ignored) {
            // The exact message was already queued. Never let clipboard
            // cleanup terminate the reader or leave a foreground service up.
        }
    }

    private void clearClipboardBeforeCapture() {
        try {
            clipboard.setPrimaryClip(ClipData.newPlainText("", ""));
        } catch (RuntimeException ignored) {
            // Never fail a bounded capture session merely because the emulator
            // rejects an empty clipboard update. Capture still remains safe.
        }
    }

    private void finishCaptureWindow(String status) {
        armedUntilElapsed = 0L;
        handler.removeCallbacks(windowExpired);
        handler.removeCallbacks(clipboardPoll);
        removeListener();
        finalStatusWritten = true;
        preferences.edit()
                .putString("status", status + " — schowek nie jest już odczytywany")
                .putLong("armed_until", 0L)
                .apply();
        stopForeground(true);
        stopSelf();
    }

    private void removeListener() {
        if (clipboard != null && listenerRegistered && listener != null) {
            clipboard.removePrimaryClipChangedListener(listener);
        }
        listenerRegistered = false;
        listener = null;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "PEva Copy Bridge",
                NotificationManager.IMPORTANCE_LOW
        );
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private Notification createNotification(String text) {
        Intent intent = new Intent(this, MainActivity.class);
        PendingIntent pendingIntent = PendingIntent.getActivity(
                this,
                0,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_menu_info_details)
                .setContentTitle("PEva Helper")
                .setContentText(text)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .build();
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder output = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                output.append(String.format("%02x", item));
            }
            return output.toString();
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 unavailable", error);
        }
    }
}
