package com.peva.copybridge;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.PixelFormat;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import android.util.DisplayMetrics;

import com.google.android.gms.tasks.OnFailureListener;
import com.google.android.gms.tasks.OnSuccessListener;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.ByteBuffer;
import java.io.File;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * One explicit, local screen OCR session. It never records video or loops:
 * after a small handoff delay it consumes one image, OCRs the chat area, and
 * releases MediaProjection, ImageReader and the foreground service.
 */
public final class ScreenOcrService extends Service {
    public static final String ACTION_CAPTURE_ONCE = "com.peva.copybridge.OCR_CAPTURE_ONCE";
    public static final String ACTION_PROCESS_ADB_SCREENSHOT = "com.peva.copybridge.OCR_ADB_SCREENSHOT";
    public static final String EXTRA_RESULT_CODE = "result_code";
    public static final String EXTRA_RESULT_DATA = "result_data";
    public static final String EXTRA_IMAGE_PATH = "image_path";
    public static final String ADB_SCREENSHOT_NAME = "peva_ocr_on_demand.png";

    private static final String CHANNEL_ID = "peva_screen_ocr";
    private static final int NOTIFICATION_ID = 42;
    // Returning from Android's capture confirmation briefly exposes a stale
    // game frame.  Wait for Last Z to restore its chat panel, then consume the
    // next stable frame rather than the first one emitted by the display.
    private static final long HANDOFF_DELAY_MS = 1200L;
    private static final long FRAME_SETTLE_MS = 800L;
    private static final long CAPTURE_TIMEOUT_MS = 6000L;

    private final AtomicBoolean completed = new AtomicBoolean(false);
    private final AtomicBoolean frameClaimed = new AtomicBoolean(false);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private MediaProjection projection;
    private ImageReader imageReader;
    private VirtualDisplay virtualDisplay;
    private TextRecognizer recognizer;
    private long acceptFrameAfterElapsed;

    private final Runnable timeout = () -> finish("Odczyt ekranu wygasł", "");

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            finish("Brak zgody na jednorazowy odczyt", "");
            return START_NOT_STICKY;
        }
        if (ACTION_PROCESS_ADB_SCREENSHOT.equals(intent.getAction())) {
            startForeground(NOTIFICATION_ID, notification("Jednorazowy odczyt czatu"));
            handler.postDelayed(timeout, CAPTURE_TIMEOUT_MS);
            processAdbScreenshot(intent.getStringExtra(EXTRA_IMAGE_PATH));
            return START_NOT_STICKY;
        }
        if (!ACTION_CAPTURE_ONCE.equals(intent.getAction())) {
            finish("Nieznany tryb jednorazowego odczytu", "");
            return START_NOT_STICKY;
        }
        int resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0);
        Intent resultData = intent.getParcelableExtra(EXTRA_RESULT_DATA);
        if (resultCode == 0 || resultData == null) {
            finish("Zgoda na odczyt ekranu nie została przekazana", "");
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, notification("Jednorazowy odczyt ekranu"));
        try {
            MediaProjectionManager manager = (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
            if (manager == null) {
                finish("Android nie udostępnia przechwycenia ekranu", "");
                return START_NOT_STICKY;
            }
            projection = manager.getMediaProjection(resultCode, resultData);
            if (projection == null) {
                finish("Nie udało się uzyskać obrazu ekranu", "");
                return START_NOT_STICKY;
            }
            handler.postDelayed(this::startCapture, HANDOFF_DELAY_MS);
            handler.postDelayed(timeout, CAPTURE_TIMEOUT_MS);
        } catch (RuntimeException error) {
            finish("Nie udało się rozpocząć odczytu ekranu", "");
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        cleanup();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private void startCapture() {
        if (completed.get() || projection == null) {
            return;
        }
        DisplayMetrics metrics = getResources().getDisplayMetrics();
        int width = metrics.widthPixels;
        int height = metrics.heightPixels;
        if (width <= 0 || height <= 0) {
            finish("Nieprawidłowy rozmiar ekranu", "");
            return;
        }
        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2);
        acceptFrameAfterElapsed = SystemClock.elapsedRealtime() + FRAME_SETTLE_MS;
        imageReader.setOnImageAvailableListener(reader -> {
            if (completed.get()) {
                return;
            }
            Image image = reader.acquireLatestImage();
            if (image == null) {
                return;
            }
            if (SystemClock.elapsedRealtime() < acceptFrameAfterElapsed) {
                // Drain transition frames so a later, settled frame can enter
                // the two-slot ImageReader buffer.
                image.close();
                return;
            }
            if (!frameClaimed.compareAndSet(false, true)) {
                image.close();
                return;
            }
            try {
                Bitmap frame = bitmapFrom(image);
                recognizeChatArea(frame);
            } catch (RuntimeException error) {
                finish("Nie udało się przygotować kadru OCR", "");
            } finally {
                image.close();
            }
        }, handler);
        virtualDisplay = projection.createVirtualDisplay(
                "PEva-one-shot-ocr",
                width,
                height,
                metrics.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.getSurface(),
                null,
                handler
        );
    }

    /**
     * Reads one ADB-created framebuffer image from this app's external files
     * folder. This avoids emulator MediaProjection's stale Unity layer. The
     * file is deleted immediately after it is decoded, before OCR begins.
     */
    private void processAdbScreenshot(String rawPath) {
        File screenshot = approvedAdbScreenshot(rawPath);
        if (screenshot == null) {
            finish("Nieprawidłowy jednorazowy obraz czatu", "");
            return;
        }
        Bitmap frame = null;
        try {
            frame = BitmapFactory.decodeFile(screenshot.getAbsolutePath());
        } finally {
            // Even on a malformed image it must not remain on shared storage.
            if (screenshot.exists() && !screenshot.delete()) {
                // There is no content fallback: the file becomes inaccessible
                // to PEva once this service exits and the next capture replaces it.
            }
        }
        if (frame == null) {
            finish("Nie udało się odczytać obrazu czatu", "");
            return;
        }
        recognizeChatArea(frame);
    }

    private File approvedAdbScreenshot(String rawPath) {
        if (rawPath == null || rawPath.isEmpty()) {
            return null;
        }
        File externalFiles = getExternalFilesDir(null);
        if (externalFiles == null) {
            return null;
        }
        try {
            File root = externalFiles.getCanonicalFile();
            File candidate = new File(rawPath).getCanonicalFile();
            if (!candidate.getName().equals(ADB_SCREENSHOT_NAME)
                    || !candidate.getParentFile().equals(root)
                    || !candidate.isFile()) {
                return null;
            }
            return candidate;
        } catch (IOException error) {
            return null;
        }
    }

    private void recognizeChatArea(Bitmap frame) {
        if (completed.get()) {
            return;
        }
        // Keep the centred private-conversation title.  The old 12% crop
        // sometimes removed it completely, letting OCR mistake the first
        // bubble's "V8 LV.27 ♂ NAME" sender line for the chat recipient.
        int top = Math.max(0, Math.round(frame.getHeight() * 0.04f));
        int bottom = Math.min(frame.getHeight(), Math.round(frame.getHeight() * 0.99f));
        if (bottom <= top) {
            finish("Nie udało się wyciąć obszaru czatu", "");
            return;
        }
        // The crop excludes the heavy game header and keeps the composer at
        // the bottom. Message layout excludes that last band separately;
        // it is only used to prove that a human draft is not present.
        Bitmap chat = Bitmap.createBitmap(frame, 0, top, frame.getWidth(), bottom - top);
        // Last Z uses small outlined letters inside bubbles.  Upscaling this
        // one still image makes the letters legible to the local recognizer;
        // it does not add a continuous graphics or CPU workload.
        Bitmap enlarged = Bitmap.createScaledBitmap(
                chat,
                chat.getWidth() * 2,
                chat.getHeight() * 2,
                true
        );
        recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS);
        recognizer.process(InputImage.fromBitmap(enlarged, 0))
                .addOnSuccessListener(new OnSuccessListener<Text>() {
                    @Override
                    public void onSuccess(Text text) {
                        finish(
                                "Odczyt lokalny zakończony",
                                chatTextFrom(text, enlarged.getHeight()),
                                privateHeaderFrom(text, enlarged.getWidth(), enlarged.getHeight()),
                                lineLayoutFrom(text, enlarged.getWidth(), enlarged.getHeight()),
                                composerTextFrom(text, enlarged.getHeight())
                        );
                    }
                })
                .addOnFailureListener(new OnFailureListener() {
                    @Override
                    public void onFailure(Exception error) {
                        finish("OCR nie rozpoznał tekstu", "");
                    }
                });
    }

    private Bitmap bitmapFrom(Image image) {
        Image.Plane plane = image.getPlanes()[0];
        ByteBuffer buffer = plane.getBuffer();
        int width = image.getWidth();
        int height = image.getHeight();
        int pixelStride = plane.getPixelStride();
        int rowPadding = plane.getRowStride() - pixelStride * width;
        Bitmap padded = Bitmap.createBitmap(
                width + Math.max(0, rowPadding / pixelStride),
                height,
                Bitmap.Config.ARGB_8888
        );
        padded.copyPixelsFromBuffer(buffer);
        return Bitmap.createBitmap(padded, 0, 0, width, height);
    }

    private void finish(String status, String text) {
        finish(status, text, "");
    }

    private void finish(String status, String text, String header) {
        finish(status, text, header, "");
    }

    private void finish(String status, String text, String header, String layout) {
        finish(status, text, header, layout, "");
    }

    private void finish(String status, String text, String header, String layout, String composer) {
        if (!completed.compareAndSet(false, true)) {
            return;
        }
        OcrResultStore.save(this, status, text, header, layout, composer);
        cleanup();
        stopForeground(true);
        stopSelf();
    }

    /**
     * Extract only the centred title of an open private conversation.  Last Z
     * also prints e.g. "V8 LV.27 [ROXY] Name" above every message bubble;
     * that sender line must never become the conversation identity.  This is
     * metadata from the same one-shot OCR pass, not a second screen read and
     * not a persistent transcript.
     */
    private String privateHeaderFrom(Text text, int imageWidth, int imageHeight) {
        // A private-chat title is a plain, centred label immediately below the
        // tabs. If it cannot be read, return no identity. The desktop then
        // waits rather than guessing from a level/VIP/sender line in a bubble.
        for (Text.TextBlock block : text.getTextBlocks()) {
            Rect box = block.getBoundingBox();
            String value = block.getText();
            if (box == null || value == null) {
                continue;
            }
            String compact = value.trim().replaceAll("\\s+", " ");
            float center = (box.left + box.right) / 2.0f;
            // The title sits immediately below the global tab strip. Bubble
            // sender lines are usually lower and off-centre. The remaining
            // text check below rejects even a corrupted bubble line such as
            // "8 LV.27O DOKTORB" if it reaches this band.
            boolean titleBand = box.top >= imageHeight * 0.015f
                    && box.top <= imageHeight * 0.16f;
            boolean centered = Math.abs(center - imageWidth / 2.0f) <= imageWidth * 0.12f;
            boolean plainAlias = compact.matches("[A-Za-z0-9][A-Za-z0-9 ._'\\-]{1,31}");
            boolean bubbleSenderHeader = compact.matches(
                    "(?i)^\\s*(?:(?:v\\.?\\s*)?\\d{1,2}\\s+)?lv\\.?\\s*\\d{1,3}"
                            + "(?:\\s*[♀♂o0@])?\\s+.+$"
            );
            if (titleBand && centered && plainAlias && !bubbleSenderHeader
                    && !compact.equalsIgnoreCase("Private Chat")
                    && !compact.equalsIgnoreCase("Group Chat")
                    && !compact.equalsIgnoreCase("Chat")
                    && !compact.equalsIgnoreCase("Notice")) {
                return compact;
            }
        }
        return "";
    }

    /**
     * Return geometry from the same one-shot OCR result.  It is not another
     * scan, never leaves the local ADB bridge, and is erased after one read.
     */
    private String chatTextFrom(Text text, int imageHeight) {
        StringBuilder visible = new StringBuilder();
        for (Text.TextBlock block : text.getTextBlocks()) {
            Rect box = block.getBoundingBox();
            String value = block.getText();
            if (box == null || value == null || box.top < imageHeight * 0.12f
                    || box.bottom > imageHeight * 0.90f) {
                continue;
            }
            if (visible.length() > 0) {
                visible.append('\n');
            }
            visible.append(value.trim());
        }
        return visible.toString();
    }

    private String composerTextFrom(Text text, int imageHeight) {
        StringBuilder composer = new StringBuilder();
        for (Text.TextBlock block : text.getTextBlocks()) {
            Rect box = block.getBoundingBox();
            String value = block.getText();
            if (box == null || value == null || box.top < imageHeight * 0.90f) {
                continue;
            }
            if (composer.length() > 0) {
                composer.append(' ');
            }
            composer.append(value.trim());
        }
        return composer.toString();
    }

    private String lineLayoutFrom(Text text, int width, int imageHeight) {
        try {
            JSONObject payload = new JSONObject();
            payload.put("version", 1);
            payload.put("width", width);
            JSONArray lines = new JSONArray();
            for (Text.TextBlock block : text.getTextBlocks()) {
                for (Text.Line line : block.getLines()) {
                    Rect box = line.getBoundingBox();
                    String value = line.getText();
                    if (box == null || value == null || value.trim().isEmpty()
                            || box.top < imageHeight * 0.12f
                            || box.bottom > imageHeight * 0.90f) {
                        continue;
                    }
                    JSONObject item = new JSONObject();
                    item.put("text", value.trim());
                    item.put("left", box.left);
                    item.put("top", box.top);
                    item.put("right", box.right);
                    item.put("bottom", box.bottom);
                    lines.put(item);
                }
            }
            payload.put("lines", lines);
            return payload.toString();
        } catch (JSONException error) {
            // Ordinary text-only OCR remains valid if layout metadata fails.
            return "";
        }
    }

    private void cleanup() {
        handler.removeCallbacks(timeout);
        if (recognizer != null) {
            recognizer.close();
            recognizer = null;
        }
        if (virtualDisplay != null) {
            virtualDisplay.release();
            virtualDisplay = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
        if (projection != null) {
            projection.stop();
            projection = null;
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "PEva jednorazowy OCR",
                NotificationManager.IMPORTANCE_LOW
        );
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pending = PendingIntent.getActivity(
                this,
                0,
                open,
                PendingIntent.FLAG_UPDATE_CURRENT
        );
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_menu_view)
                .setContentTitle("PEva Eyes")
                .setContentText(text)
                .setContentIntent(pending)
                .setOngoing(true)
                .build();
    }
}
