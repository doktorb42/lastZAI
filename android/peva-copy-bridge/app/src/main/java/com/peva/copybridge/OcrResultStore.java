package com.peva.copybridge;

import android.content.Context;
import android.content.SharedPreferences;

/** App-private, short-lived output of one local screen OCR read. */
final class OcrResultStore {
    private static final String PREFS = "peva_screen_ocr";
    private static final String KEY_STATUS = "status";
    private static final String KEY_TEXT = "text";
    private static final String KEY_HEADER = "header";
    private static final String KEY_LAYOUT = "layout";
    private static final String KEY_COMPOSER = "composer";
    private static final String KEY_CAPTURED_AT = "captured_at";
    private static final long MAX_AGE_MS = 15L * 60L * 1000L;

    private OcrResultStore() {
    }

    static void save(Context context, String status, String text) {
        save(context, status, text, "");
    }

    static void save(Context context, String status, String text, String header) {
        save(context, status, text, header, "");
    }

    static void save(Context context, String status, String text, String header, String layout) {
        save(context, status, text, header, layout, "");
    }

    static void save(Context context, String status, String text, String header, String layout, String composer) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(KEY_STATUS, status == null ? "Odczyt bez wyniku" : status)
                .putString(KEY_TEXT, text == null ? "" : text)
                .putString(KEY_HEADER, header == null ? "" : header)
                .putString(KEY_LAYOUT, layout == null ? "" : layout)
                .putString(KEY_COMPOSER, composer == null ? "" : composer)
                .putLong(KEY_CAPTURED_AT, System.currentTimeMillis())
                .apply();
    }

    static Result read(Context context) {
        SharedPreferences preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        long capturedAt = preferences.getLong(KEY_CAPTURED_AT, 0L);
        if (capturedAt <= 0L || System.currentTimeMillis() - capturedAt > MAX_AGE_MS) {
            return new Result("Brak świeżego odczytu", "", "", "", "", 0L);
        }
        return new Result(
                preferences.getString(KEY_STATUS, "Odczyt bez wyniku"),
                preferences.getString(KEY_TEXT, ""),
                preferences.getString(KEY_HEADER, ""),
                preferences.getString(KEY_LAYOUT, ""),
                preferences.getString(KEY_COMPOSER, ""),
                capturedAt
        );
    }

    static void clear(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply();
    }

    static final class Result {
        final String status;
        final String text;
        final String header;
        final String layout;
        final String composer;
        final long capturedAt;

        Result(String status, String text, String header, String layout, String composer, long capturedAt) {
            this.status = status == null ? "" : status;
            this.text = text == null ? "" : text;
            this.header = header == null ? "" : header;
            this.layout = layout == null ? "" : layout;
            this.composer = composer == null ? "" : composer;
            this.capturedAt = capturedAt;
        }
    }
}
