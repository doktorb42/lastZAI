package peva.patch;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.util.Enumeration;
import java.util.zip.CRC32;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipOutputStream;

/** Rebuild one APK on-device without touching the signed original. */
public final class PatchZip {
    private static final int BUFFER = 1024 * 1024;

    private static boolean isSignature(String name) {
        String upper = name.toUpperCase();
        return upper.startsWith("META-INF/") &&
            (upper.endsWith("MANIFEST.MF") || upper.endsWith(".SF") ||
             upper.endsWith(".RSA") || upper.endsWith(".DSA") || upper.endsWith(".EC"));
    }

    private static long crc(File file) throws Exception {
        CRC32 crc = new CRC32();
        byte[] buffer = new byte[BUFFER];
        try (InputStream input = new BufferedInputStream(new FileInputStream(file), BUFFER)) {
            for (int read; (read = input.read(buffer)) >= 0;) {
                crc.update(buffer, 0, read);
            }
        }
        return crc.getValue();
    }

    private static ZipEntry replacementEntry(ZipEntry old, File replacement) throws Exception {
        ZipEntry result = new ZipEntry(old.getName());
        result.setMethod(old.getMethod());
        result.setTime(old.getTime());
        if (old.getExtra() != null) result.setExtra(old.getExtra());
        if (old.getComment() != null) result.setComment(old.getComment());
        if (old.getMethod() == ZipEntry.STORED) {
            result.setSize(replacement.length());
            result.setCompressedSize(replacement.length());
            result.setCrc(crc(replacement));
        }
        return result;
    }

    private static ZipEntry copiedEntry(ZipEntry old) {
        ZipEntry result = new ZipEntry(old.getName());
        result.setMethod(old.getMethod());
        result.setTime(old.getTime());
        if (old.getExtra() != null) result.setExtra(old.getExtra());
        if (old.getComment() != null) result.setComment(old.getComment());
        if (old.getMethod() == ZipEntry.STORED) {
            result.setSize(old.getSize());
            result.setCompressedSize(old.getSize());
            result.setCrc(old.getCrc());
        }
        return result;
    }

    private static void copy(InputStream source, ZipOutputStream target) throws Exception {
        byte[] buffer = new byte[BUFFER];
        for (int read; (read = source.read(buffer)) >= 0;) target.write(buffer, 0, read);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 4) throw new IllegalArgumentException("source.apk output.apk replacement.bundle target/entry");
        File source = new File(args[0]);
        File output = new File(args[1]);
        File replacement = new File(args[2]);
        String target = args[3];
        if (!source.isFile() || !replacement.isFile() || output.exists()) throw new IllegalStateException("Invalid file arguments");
        int copied = 0, skipped = 0, matches = 0;
        try (ZipFile input = new ZipFile(source); ZipOutputStream zip = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(output), BUFFER))) {
            Enumeration<? extends ZipEntry> entries = input.entries();
            while (entries.hasMoreElements()) {
                ZipEntry entry = entries.nextElement();
                if (isSignature(entry.getName())) { skipped++; continue; }
                boolean replace = entry.getName().equals(target);
                if (replace) matches++;
                zip.putNextEntry(replace ? replacementEntry(entry, replacement) : copiedEntry(entry));
                try (InputStream data = new BufferedInputStream(replace ? new FileInputStream(replacement) : input.getInputStream(entry), BUFFER)) {
                    copy(data, zip);
                }
                zip.closeEntry();
                copied++;
            }
        }
        if (matches != 1) {
            output.delete();
            throw new IllegalStateException("Expected one target entry, found " + matches);
        }
        System.out.println("OK entries=" + copied + " signatures_removed=" + skipped + " output_bytes=" + output.length());
    }
}
