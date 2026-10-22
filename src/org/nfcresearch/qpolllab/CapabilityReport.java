package org.nfcresearch.qpolllab;

import android.os.Build;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Read-only capability report: which model this is, which reader / vendor entry points the NFC
 * framework exposes, and whether the vendor configuration mentions Q-Poll at all. Nothing here
 * talks to the controller; it only answers "does this phone even ship the feature".
 */
public final class CapabilityReport {
    private static final String[] KEYWORDS = {
            "vendor", "qtag", "qpoll", "q_poll", "eink", "e-ink", "epd", "case", "cover",
            "discover", "reader", "transceive", "raw"
    };
    private static final String[] CONFIG_KEYS = {
            "qpoll", "q_poll", "qtag", "eink", "e-ink", "epd", "stag"
    };
    private static final String[] CONFIG_FILES = {
            "/odm/etc/libnfc-nxp.conf", "/vendor/etc/libnfc-nxp.conf",
            "/odm/etc/libnfc-nci.conf", "/vendor/etc/libnfc-nci.conf",
            "/odm/etc/libnfc-nxp-pnscr.conf", "/vendor/etc/libnfc-nxp-pnscr.conf"
    };

    public static List<String> lines() {
        List<String> out = new ArrayList<>();
        out.add("机型=" + Build.MANUFACTURER + " " + Build.MODEL + "（" + Build.DEVICE + "）；Android "
                + Build.VERSION.RELEASE + " / SDK " + Build.VERSION.SDK_INT);
        out.add("NFC 入口=" + entryPoints());
        out.add("配置关键字=" + configHits());
        return out;
    }

    /** Reader / vendor methods the framework actually offers, so we know what can be reflected on. */
    private static String entryPoints() {
        StringBuilder sb = new StringBuilder();
        int hits = 0;
        for (String type : new String[]{"android.nfc.INfcAdapter", "android.nfc.NfcAdapter"}) {
            Class<?> clazz;
            try { clazz = Class.forName(type); } catch (Throwable e) { continue; }
            Method[] methods;
            try { methods = clazz.getMethods(); } catch (Throwable e) { continue; }
            for (Method m : methods) {
                String name = m.getName().toLowerCase(Locale.ROOT);
                boolean match = false;
                for (String key : KEYWORDS) if (name.contains(key)) { match = true; break; }
                if (!match) continue;
                if (sb.length() > 0) sb.append("；");
                sb.append(clazz.getSimpleName()).append('.').append(m.getName())
                        .append('(').append(m.getParameterTypes().length).append("参)");
                if (++hits >= 24) return sb.toString();
            }
        }
        return hits == 0 ? "未枚举到寻卡 / 厂商相关入口（可能全部被隐藏）" : sb.toString();
    }

    /** Any vendor configuration line mentioning the private e-ink polling feature. */
    private static String configHits() {
        StringBuilder sb = new StringBuilder();
        List<String> files = new ArrayList<>();
        for (String path : CONFIG_FILES) files.add(path);
        File[] dirs = {new File("/odm/etc"), new File("/vendor/etc")};
        for (File dir : dirs) {
            File[] found = dir.listFiles((d, name) -> name.startsWith("libnfc") && name.endsWith(".conf"));
            if (found != null) for (File f : found) if (!files.contains(f.getPath())) files.add(f.getPath());
        }
        int scanned = 0;
        for (String path : files) {
            if (!new File(path).canRead()) continue;
            scanned++;
            try (BufferedReader in = new BufferedReader(new FileReader(path))) {
                String line;
                while ((line = in.readLine()) != null) {
                    String lower = line.toLowerCase(Locale.ROOT);
                    boolean hit = false;
                    for (String key : CONFIG_KEYS) if (lower.contains(key)) { hit = true; break; }
                    if (!hit) continue;
                    if (sb.length() > 0) sb.append("；");
                    sb.append(new File(path).getName()).append(": ").append(line.trim());
                    if (sb.length() > 300) return sb.toString();
                }
            } catch (Exception ignored) { }
        }
        return sb.length() == 0
                ? "已扫描 " + scanned + " 个 libnfc 配置，未出现 QPOLL / EPD / EINK 等关键字"
                : sb.toString();
    }
}
