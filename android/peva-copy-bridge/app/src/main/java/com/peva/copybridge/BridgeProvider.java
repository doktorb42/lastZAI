package com.peva.copybridge;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Process;
import android.util.Base64;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

/**
 * Tiny local ADB bridge. Only Android's shell UID and this app's own UID can
 * query it. Messages are base64 encoded in the cursor so punctuation/newlines
 * in a Last Z chat cannot corrupt the ADB text transport.
 */
public final class BridgeProvider extends ContentProvider {
    public static final String AUTHORITY = "com.peva.copybridge.local";
    private static final String PATH_PAIR = "pair";
    private static final String PATH_INBOX = "inbox";
    private static final String PATH_STATUS = "status";
    private static final String PATH_OCR = "ocr";

    @Override
    public boolean onCreate() {
        return getContext() != null;
    }

    @Override
    public Cursor query(
            Uri uri,
            String[] projection,
            String selection,
            String[] selectionArgs,
            String sortOrder
    ) {
        enforceLocalAdbCaller();
        if (uri == null || uri.getPathSegments().isEmpty()) {
            throw new IllegalArgumentException("Missing BridgeProvider path");
        }
        String path = uri.getPathSegments().get(0);
        if (PATH_PAIR.equals(path)) {
            MatrixCursor cursor = new MatrixCursor(new String[]{"token"});
            cursor.addRow(new Object[]{BridgeAuth.token(getContext())});
            return cursor;
        }
        if (PATH_INBOX.equals(path)) {
            MatrixCursor cursor = new MatrixCursor(
                    new String[]{"_id", "text_b64", "sha256", "captured_at"}
            );
            // This is a one-time local handoff: the queue is cleared after it
            // has been copied into this cursor for PEva's runtime to read.
            JSONArray records = ClipboardInbox.take(getContext());
            for (int index = 0; index < records.length(); index++) {
                JSONObject record = records.optJSONObject(index);
                if (record == null) {
                    continue;
                }
                String id = record.optString("id", "");
                String text = record.optString("text", "");
                String fingerprint = record.optString("sha256", "");
                long capturedAt = record.optLong("captured_at", 0L);
                if (id.isEmpty() || text.isEmpty() || fingerprint.isEmpty() || capturedAt <= 0) {
                    continue;
                }
                String encodedText = Base64.encodeToString(
                        text.getBytes(StandardCharsets.UTF_8),
                        Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
                );
                cursor.addRow(new Object[]{id, encodedText, fingerprint, capturedAt});
            }
            return cursor;
        }
        if (PATH_STATUS.equals(path)) {
            SharedPreferences preferences = getContext().getSharedPreferences(
                    ClipboardBridgeService.PREFS,
                    Context.MODE_PRIVATE
            );
            String status = preferences.getString("status", "Gotowy — schowek nie jest odczytywany");
            String statusB64 = Base64.encodeToString(
                    status.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            MatrixCursor cursor = new MatrixCursor(new String[]{
                    "status_b64", "last_length", "captured_at", "captured_in_window", "armed_until", "pending"
            });
            cursor.addRow(new Object[]{
                    statusB64,
                    preferences.getInt("last_length", 0),
                    preferences.getLong("captured_at", 0L),
                    preferences.getInt("captured_in_window", 0),
                    preferences.getLong("armed_until", 0L),
                    ClipboardInbox.pendingCount(getContext())
            });
            return cursor;
        }
        if (PATH_OCR.equals(path)) {
            OcrResultStore.Result result = OcrResultStore.read(getContext());
            String status = Base64.encodeToString(
                    result.status.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            String text = Base64.encodeToString(
                    result.text.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            String header = Base64.encodeToString(
                    result.header.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            String layout = Base64.encodeToString(
                    result.layout.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            String composer = Base64.encodeToString(
                    result.composer.getBytes(StandardCharsets.UTF_8),
                    Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
            );
            MatrixCursor cursor = new MatrixCursor(new String[]{
                    "status_b64", "text_b64", "header_b64", "layout_b64", "composer_b64", "captured_at"
            });
            cursor.addRow(new Object[]{status, text, header, layout, composer, result.capturedAt});
            // Read once from the computer, then erase the transient transcript.
            OcrResultStore.clear(getContext());
            return cursor;
        }
        throw new IllegalArgumentException("Unknown BridgeProvider path");
    }

    @Override
    public String getType(Uri uri) {
        return "vnd.android.cursor.dir/vnd.peva.copy";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("BridgeProvider is read-only");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("BridgeProvider is read-only");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("BridgeProvider is read-only");
    }

    private void enforceLocalAdbCaller() {
        int caller = Binder.getCallingUid();
        int ownUid = getContext().getApplicationInfo().uid;
        if (caller != ownUid && caller != Process.SHELL_UID && caller != Process.ROOT_UID) {
            throw new SecurityException("PEva Copy Bridge is available only to local ADB");
        }
    }
}
