package io.veritas.agent;

import java.lang.reflect.*;
import java.util.*;

/** Deep value extraction (Issue 1): unfold args/returns to decision-FIELD values
 *  (primitives/boxed/String/enum), bounded in depth/breadth, never throwing into the app. */
public final class ValueExtractor {
    static final int MAX_DEPTH = 2, MAX_ELEMS = 50, MAX_FIELDS = 40;

    private ValueExtractor() {}

    static boolean isScalar(Object v) {
        return v == null || v instanceof String || v instanceof Number
                || v instanceof Boolean || v instanceof Character || v instanceof Enum;
    }

    static Object scalar(Object o) {
        if (o == null || o instanceof String || o instanceof Number || o instanceof Boolean) return o;
        return String.valueOf(o);
    }

    @SuppressWarnings("unchecked")
    static Object unfold(Object o, int depth) {
        if (o == null) return null;
        if (o instanceof String || o instanceof Number || o instanceof Boolean || o instanceof Character) return o;
        if (o instanceof Enum) return o.toString();
        if (o instanceof Optional) {
            Optional<?> op = (Optional<?>) o; return op.isPresent() ? unfold(op.get(), depth) : null;
        }
        if (o.getClass().isArray()) {
            List<Object> out = new ArrayList<>(); int n = Array.getLength(o);
            for (int i = 0; i < n && i < MAX_ELEMS; i++) out.add(unfold(Array.get(o, i), depth));
            return out;
        }
        if (o instanceof Collection) {
            List<Object> out = new ArrayList<>(); int i = 0;
            for (Object e : (Collection<?>) o) { if (i++ >= MAX_ELEMS) break; out.add(unfold(e, depth)); }
            return out;
        }
        if (o instanceof Map) {
            Map<String, Object> out = new LinkedHashMap<>(); int i = 0;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                if (i++ >= MAX_ELEMS) break; out.put(String.valueOf(e.getKey()), unfold(e.getValue(), depth));
            }
            return out;
        }
        if (depth >= MAX_DEPTH) return o.toString();
        // POJO -> field-dict of scalar fields (+ shallow collections)
        Map<String, Object> m = new LinkedHashMap<>(); int n = 0;
        for (Field f : allFields(o.getClass())) {
            if (n >= MAX_FIELDS) break;
            if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
            try {
                f.setAccessible(true); Object v = f.get(o);
                if (isScalar(v)) { m.put(f.getName(), v instanceof Enum ? v.toString() : v); n++; }
                else if (v != null && depth + 1 < MAX_DEPTH
                        && (v instanceof Collection || v instanceof Optional || v.getClass().isArray())) {
                    m.put(f.getName(), unfold(v, depth + 1)); n++;
                }
            } catch (Throwable ignored) { /* skip unreadable field */ }
        }
        return m.isEmpty() ? o.toString() : m;
    }

    static Object unfoldArgs(String method, Object[] args) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (args == null) return m;
        String[] names = paramNames(method, args.length);
        for (int i = 0; i < args.length; i++) m.put(names[i], unfold(args[i], 0));
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
            } catch (Throwable ignored) { /* try next loader */ }
        }
        return fb;
    }

    private static List<Field> allFields(Class<?> c) {
        List<Field> out = new ArrayList<>(); int depth = 0;
        while (c != null && c != Object.class && depth++ < 4) {
            out.addAll(Arrays.asList(c.getDeclaredFields())); c = c.getSuperclass();
        }
        return out;
    }
}
