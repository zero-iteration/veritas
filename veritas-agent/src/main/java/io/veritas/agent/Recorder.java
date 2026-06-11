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
    static final Set<String> captureMethods = ConcurrentHashMap.newKeySet();      // simple method names
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
            inv.put("args", ValueExtractor.unfoldArgs(method, args));
            inv.put("ret", ValueExtractor.unfold(ret, 0));
            invocations.add(inv);
        } catch (Throwable ignored) { /* capture must never break the app */ }
    }

    /** Config getter instrumentation: (key) => value. */
    public static void config(String key, Object value) {
        if (key != null) configLive.put(key, ValueExtractor.scalar(value));
    }

    private static boolean shouldCapture(String method) {
        if (captureAll) return true;
        int dot = method.lastIndexOf('.');
        return captureMethods.contains(dot >= 0 ? method.substring(dot + 1) : method);
    }
}
