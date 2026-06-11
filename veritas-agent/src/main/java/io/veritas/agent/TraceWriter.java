package io.veritas.agent;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Serializes the Recorder state to the trace JSON contract consumed by the join engine.
 *  Self-contained JSON writer (no external deps in the agent). */
public final class TraceWriter {
    private TraceWriter() {}

    public static void write(String path) {
        try {
            Map<String, Object> fp = new LinkedHashMap<>();
            fp.put("env", System.getProperty("veritas.env", "local"));
            fp.put("profile", System.getProperty("veritas.profile"));
            fp.put("git_sha", System.getProperty("veritas.sha"));
            fp.put("timestamp", System.currentTimeMillis() / 1000.0);
            Map<String, Object> strat = new LinkedHashMap<>();
            for (Map.Entry<String, Object> e : Recorder.configLive.entrySet())
                if (e.getKey().contains("strategy") || e.getKey().contains("switch")) strat.put(e.getKey(), e.getValue());
            fp.put("strategy_keys", strat);

            List<Map<String, Object>> edges = new ArrayList<>();
            for (Map.Entry<String, Long> e : Recorder.edges.entrySet()) {
                String[] ce = e.getKey().split("\\|", 2);
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("caller", ce[0]); m.put("callee", ce.length > 1 ? ce[1] : ""); m.put("count", e.getValue());
                edges.add(m);
            }

            Map<String, Object> root = new LinkedHashMap<>();
            root.put("trace_ref", System.getProperty("veritas.trace", "run"));
            root.put("fingerprint", fp);
            root.put("methods", new ArrayList<>(Recorder.methods));
            root.put("edges", edges);
            root.put("invocations", Recorder.invocations);
            root.put("config_live", Recorder.configLive);

            StringBuilder sb = new StringBuilder();
            json(root, sb);
            Files.write(Paths.get(path), sb.toString().getBytes(StandardCharsets.UTF_8));
            System.err.println("[veritas] wrote " + path + "  (" + Recorder.methods.size() + " methods, "
                    + Recorder.invocations.size() + " invocations, " + Recorder.configLive.size() + " config keys)");
        } catch (IOException e) {
            System.err.println("[veritas] failed to write trace: " + e);
        }
    }

    @SuppressWarnings("unchecked")
    private static void json(Object o, StringBuilder sb) {
        if (o == null) { sb.append("null"); return; }
        if (o instanceof String) { str((String) o, sb); return; }
        if (o instanceof Boolean || o instanceof Number) { sb.append(o.toString()); return; }
        if (o instanceof Map) {
            sb.append('{'); boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                if (!first) sb.append(','); first = false;
                str(String.valueOf(e.getKey()), sb); sb.append(':'); json(e.getValue(), sb);
            }
            sb.append('}'); return;
        }
        if (o instanceof Iterable) {
            sb.append('['); boolean first = true;
            for (Object e : (Iterable<?>) o) { if (!first) sb.append(','); first = false; json(e, sb); }
            sb.append(']'); return;
        }
        str(o.toString(), sb);
    }

    private static void str(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c)); else sb.append(c);
            }
        }
        sb.append('"');
    }
}
