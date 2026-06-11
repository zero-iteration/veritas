package io.veritas.agent;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/** Static, thread-safe collectors. Deterministic (no sampling floor): every entered
 *  method, every call edge, and decision-site values for capture-listed methods. */
public final class Recorder {
    public static final Set<String> methods = ConcurrentHashMap.newKeySet();
    public static final Map<String, Long> edges = new ConcurrentHashMap<>();      // "caller|callee" -> count
    public static final List<Map<String, Object>> invocations = Collections.synchronizedList(new ArrayList<>());
    public static final Map<String, Object> configLive = new ConcurrentHashMap<>();

    static volatile boolean captureAll = false;
    static final Set<String> captureMethods = ConcurrentHashMap.newKeySet();      // simple, Class.method, or FQN
    static final Set<String> unfoldPaths = ConcurrentHashMap.newKeySet();         // expectation-driven nested field paths
    static int maxInvocations = 2000;

    private static final ThreadLocal<Deque<String>> STACK = ThreadLocal.withInitial(ArrayDeque::new);

    private Recorder() {}

    public static void onEnter(String method) {
        methods.add(method);
        Deque<String> st = STACK.get();
        if (!st.isEmpty()) edges.merge(st.peek() + "|" + method, 1L, Long::sum);
        st.push(method);
    }

    public static void onExit(String method, Object[] args, Object ret) {
        Deque<String> st = STACK.get();
        if (!st.isEmpty()) st.pop();
        if (!shouldCapture(method) || invocations.size() >= maxInvocations) return;
        try {
            Map<String, Object> inv = new LinkedHashMap<>();
            inv.put("method", method);
            inv.put("args", ValueExtractor.unfoldArgs(method, args, unfoldPaths));
            inv.put("ret", ValueExtractor.unfold(ret, 0, unfoldPaths));
            invocations.add(inv);
        } catch (Throwable ignored) { /* capture must never break the app */ }
    }

    /** Config getter instrumentation: (key) => value, redacted by key name/shape. */
    public static void config(String key, Object value) {
        if (key != null) configLive.put(key, Redactor.field(key, ValueExtractor.scalar(value)));
    }

    /** Match full FQN, Class.method (last two segments), or bare method name (§3.1-3). */
    private static boolean shouldCapture(String method) {
        if (captureAll) return true;
        if (captureMethods.contains(method)) return true;
        int dot = method.lastIndexOf('.');
        if (dot >= 0) {
            if (captureMethods.contains(method.substring(dot + 1))) return true;   // bare name
            int dot2 = method.lastIndexOf('.', dot - 1);
            if (dot2 >= 0 && captureMethods.contains(method.substring(dot2 + 1))) return true;  // Class.method
        } else if (captureMethods.contains(method)) return true;
        return false;
    }
}
