package io.veritas.agent;

import java.lang.reflect.*;
import java.util.*;

/** Deep value extraction. Unfolds args/returns to decision-FIELD values, bounded, never
 *  throwing into the app. §3.1-1: an EXPECTATION-DRIVEN field-path allowlist (e.g.
 *  "breakdown.netPrice") is followed depth-EXEMPT so nested POJO fields two levels deep are
 *  captured even though generic unfolding stops at depth 2. §3.1-2: redaction at write time. */
public final class ValueExtractor {
    static final int MAX_DEPTH = 2, MAX_ELEMS = 50, MAX_FIELDS = 40, MAX_PATH = 8;
    static final Set<String> NONE = Collections.emptySet();

    private ValueExtractor() {}

    static boolean isScalar(Object v) {
        return v == null || v instanceof String || v instanceof Number
                || v instanceof Boolean || v instanceof Character || v instanceof Enum;
    }

    static Object scalar(Object o) {
        Object v = (o == null || o instanceof String || o instanceof Number || o instanceof Boolean)
                ? o : String.valueOf(o);
        return Redactor.value(v);
    }

    static Object unfold(Object o, int depth) { return unfold(o, depth, NONE); }

    @SuppressWarnings("unchecked")
    static Object unfold(Object o, int depth, Set<String> paths) {
        if (o == null) return null;
        if (o instanceof String) return Redactor.value(o);
        if (o instanceof Number || o instanceof Boolean || o instanceof Character) return o;
        if (o instanceof Enum) return o.toString();
        if (o instanceof Optional) {
            Optional<?> op = (Optional<?>) o; return op.isPresent() ? unfold(op.get(), depth, paths) : null;
        }
        if (o.getClass().isArray()) {
            List<Object> out = new ArrayList<>(); int n = Array.getLength(o);
            for (int i = 0; i < n && i < MAX_ELEMS; i++) out.add(unfold(Array.get(o, i), depth, paths));
            return out;
        }
        if (o instanceof Collection) {
            List<Object> out = new ArrayList<>(); int i = 0;
            for (Object e : (Collection<?>) o) { if (i++ >= MAX_ELEMS) break; out.add(unfold(e, depth, paths)); }
            return out;
        }
        if (o instanceof Map) {
            Map<String, Object> out = new LinkedHashMap<>(); int i = 0;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                if (i++ >= MAX_ELEMS) break;
                String k = String.valueOf(e.getKey());
                out.put(k, Redactor.denyName(k) ? "<redacted:" + k + ">" : unfold(e.getValue(), depth, paths));
            }
            return out;
        }
        // POJO: scalar fields (redacted) + allowlisted nested paths (depth-exempt) + shallow collections
        if (depth >= MAX_DEPTH && paths.isEmpty()) return o.toString();
        if (depth >= MAX_PATH) return o.toString();   // hard recursion backstop
        Map<String, Object> m = new LinkedHashMap<>(); int n = 0;
        for (Field f : allFields(o.getClass())) {
            if (n >= MAX_FIELDS) break;
            if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
            String fn = f.getName();
            Object v;
            try { f.setAccessible(true); v = f.get(o); } catch (Throwable t) { continue; }
            if (isScalar(v)) {
                m.put(fn, Redactor.field(fn, v instanceof Enum ? v.toString() : v)); n++;
                continue;
            }
            Set<String> sub = remainders(paths, fn);
            if (!sub.isEmpty()) {                       // follow allowlisted nested path, depth-exempt
                m.put(fn, unfold(v, depth, sub)); n++;
            } else if (paths.contains(fn) && v != null) {
                m.put(fn, unfold(v, depth + 1, NONE)); n++;
            } else if (v != null && depth + 1 < MAX_DEPTH
                    && (v instanceof Collection || v instanceof Optional || v.getClass().isArray())) {
                m.put(fn, unfold(v, depth + 1, paths)); n++;
            }
        }
        return m.isEmpty() ? o.toString() : m;
    }

    private static Set<String> remainders(Set<String> paths, String field) {
        if (paths.isEmpty()) return NONE;
        Set<String> out = new HashSet<>();
        String pre = field + ".";
        for (String p : paths) if (p.startsWith(pre)) out.add(p.substring(pre.length()));
        return out;
    }

    static Object unfoldArgs(String method, Object[] args, Set<String> paths) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (args == null) return m;
        String[] names = paramNames(method, args.length);
        for (int i = 0; i < args.length; i++) m.put(names[i], unfold(args[i], 0, paths));
        return m;
    }

    private static String[] paramNames(String method, int count) {
        String[] fb = new String[count];
        for (int i = 0; i < count; i++) fb[i] = "arg" + i;
        int dot = method.lastIndexOf('.');
        if (dot < 0) return fb;
        String cls = method.substring(0, dot), mn = method.substring(dot + 1);
        for (ClassLoader cl : new ClassLoader[]{Thread.currentThread().getContextClassLoader(),
                ClassLoader.getSystemClassLoader()}) {
            try {
                Class<?> c = Class.forName(cls, false, cl);
                for (Method me : c.getDeclaredMethods()) {
                    if (me.getName().equals(mn) && me.getParameterCount() == count) {
                        Parameter[] ps = me.getParameters(); String[] out = new String[count];
                        for (int i = 0; i < count; i++) out[i] = ps[i].isNamePresent() ? ps[i].getName() : ("arg" + i);
                        return out;
                    }
                }
            } catch (Throwable ignored) { }
        }
        return fb;
    }

    private static List<Field> allFields(Class<?> c) {
        List<Field> out = new ArrayList<>(); int d = 0;
        while (c != null && c != Object.class && d++ < 4) {
            out.addAll(Arrays.asList(c.getDeclaredFields())); c = c.getSuperclass();
        }
        return out;
    }
}
