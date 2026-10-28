package com.peva.copybridge;

import android.content.Context;
import android.util.Base64;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

/** Per-install secret used only to arm a capture window from the local desktop. */
final class BridgeAuth {
    static final String TOKEN_FILE = "bridge_token";
    private static final int TOKEN_BYTES = 24;

    private BridgeAuth() {
    }

    static synchronized String token(Context context) {
        File file = new File(context.getFilesDir(), TOKEN_FILE);
        String existing = read(file);
        if (existing != null && existing.length() >= 24) {
            return existing;
        }

        byte[] bytes = new byte[TOKEN_BYTES];
        new SecureRandom().nextBytes(bytes);
        String created = Base64.encodeToString(
                bytes,
                Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP
        );
        write(file, created);
        return created;
    }

    static boolean matches(Context context, String candidate) {
        if (candidate == null || candidate.isEmpty()) {
            return false;
        }
        return MessageDigest.isEqual(
                token(context).getBytes(StandardCharsets.UTF_8),
                candidate.getBytes(StandardCharsets.UTF_8)
        );
    }

    private static String read(File file) {
        if (!file.isFile()) {
            return null;
        }
        try (FileInputStream stream = new FileInputStream(file)) {
            byte[] bytes = new byte[(int) Math.min(file.length(), 256)];
            int count = stream.read(bytes);
            if (count <= 0) {
                return null;
            }
            return new String(bytes, 0, count, StandardCharsets.UTF_8).trim();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static void write(File file, String value) {
        try (FileOutputStream stream = new FileOutputStream(file, false)) {
            stream.write(value.getBytes(StandardCharsets.UTF_8));
            stream.getFD().sync();
        } catch (Exception ignored) {
            // The next arm attempt regenerates a token if storage is unavailable.
        }
    }
}
