package com.peva.copybridge;

import android.content.Context;
import android.util.AtomicFile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * A deliberately tiny, app-private queue. It is not shared Android storage.
 * The desktop reads it through ADB's debug-only `run-as` command, then applies
 * its own message deduplication. Records are capped and pruned on every use.
 */
final class ClipboardInbox {
    static final String FILE_NAME = "copy_inbox.json";
    private static final int VERSION = 1;
    private static final int MAX_RECORDS = 16;
    private static final long MAX_AGE_MS = 15L * 60L * 1000L;

    private ClipboardInbox() {
    }

    static synchronized int enqueue(
            Context context,
            String text,
            String sha256,
            long capturedAt
    ) {
        JSONObject root = load(context, capturedAt);
        JSONArray records = root.optJSONArray("messages");
        if (records == null) {
            records = new JSONArray();
            putQuietly(root, "messages", records);
        }

        JSONObject record = new JSONObject();
        putQuietly(record, "id", UUID.randomUUID().toString());
        putQuietly(record, "text", text);
        putQuietly(record, "sha256", sha256);
        putQuietly(record, "captured_at", capturedAt);
        records.put(record);

        JSONArray trimmed = trim(records, capturedAt);
        putQuietly(root, "version", VERSION);
        putQuietly(root, "messages", trimmed);
        save(context, root);
        return trimmed.length();
    }

    static synchronized int pendingCount(Context context) {
        JSONObject root = load(context, System.currentTimeMillis());
        JSONArray records = root.optJSONArray("messages");
        return records == null ? 0 : records.length();
    }

    /**
     * Return the short burst collected for PEva, then immediately forget it.
     * The desktop runtime asks for this once after a deliberately armed copy
     * window, so retaining a second copy inside the helper serves no purpose.
     */
    static synchronized JSONArray take(Context context) {
        JSONObject root = load(context, System.currentTimeMillis());
        JSONArray records = root.optJSONArray("messages");
        JSONArray result = records == null ? new JSONArray() : records;
        putQuietly(root, "version", VERSION);
        putQuietly(root, "messages", new JSONArray());
        save(context, root);
        return result;
    }

    /** Clear PEva Helper's own temporary queue without touching the user's clipboard. */
    static synchronized void clear(Context context) {
        JSONObject root = new JSONObject();
        putQuietly(root, "version", VERSION);
        putQuietly(root, "messages", new JSONArray());
        save(context, root);
    }

    private static JSONObject load(Context context, long now) {
        File source = new File(context.getFilesDir(), FILE_NAME);
        JSONObject root = new JSONObject();
        try (FileInputStream stream = new FileInputStream(source)) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[1024];
            int read;
            while ((read = stream.read(buffer)) != -1) {
                output.write(buffer, 0, read);
            }
            root = new JSONObject(output.toString("UTF-8"));
        } catch (Exception ignored) {
            // Start with an empty, well-formed queue.
        }

        JSONArray records = root.optJSONArray("messages");
        JSONArray trimmed = trim(records == null ? new JSONArray() : records, now);
        putQuietly(root, "version", VERSION);
        putQuietly(root, "messages", trimmed);
        return root;
    }

    private static JSONArray trim(JSONArray source, long now) {
        JSONArray result = new JSONArray();
        long minimumTime = now - MAX_AGE_MS;
        int start = Math.max(0, source.length() - MAX_RECORDS);
        for (int index = start; index < source.length(); index++) {
            JSONObject item = source.optJSONObject(index);
            if (item == null || item.optString("text", "").isEmpty()) {
                continue;
            }
            if (item.optLong("captured_at", 0L) < minimumTime) {
                continue;
            }
            result.put(item);
        }
        return result;
    }

    private static void save(Context context, JSONObject root) {
        AtomicFile file = new AtomicFile(new File(context.getFilesDir(), FILE_NAME));
        FileOutputStream stream = null;
        try {
            stream = file.startWrite();
            stream.write(root.toString().getBytes(StandardCharsets.UTF_8));
            file.finishWrite(stream);
        } catch (Exception ignored) {
            if (stream != null) {
                file.failWrite(stream);
            }
        }
    }

    private static void putQuietly(JSONObject object, String key, Object value) {
        try {
            object.put(key, value);
        } catch (Exception ignored) {
            // Inputs are simple primitives/JSON values, so this is defensive only.
        }
    }
}
