package io.veritas.agent;

import java.lang.reflect.*;
import java.util.*;

/** Deep value extraction. Unfolds args/returns to decision-FIELD values, bounded, never throwing
 *  into the app.
 *
 *  Capture completeness is the product (§6.5 / §11.1): the verifier can only reason about what this
 *  layer addresses, and a field silently dropped here is a behavior it can never verify. Worse, a
 *  dropped ARGUMENT field collapses distinct inputs into one observed condition, so a deterministic
 *  behavior is later mislabeled non-deterministic and silently un-watched — the single worst failure
 *  for a verifier whose promise is "silence means verified."
 *
 *  Two defenses, both here:
 *   1. Args define the condition, so they unfold FULLY by default (auto-nest, no allowlist needed)
 *      under far looser bounds than returns. The operator never has to know the discriminating field.
 *   2. Any cap that is still hit is recorded LOUDLY (a truncation marker) and the field is shallowed
 *      to a string rather than dropped — present-but-shallow is recoverable; absent is not. */
public final class ValueExtractor {

    /** Limits for one unfold pass. Args and returns get deliberately different policies. */
    static final class Limits {
        final int maxDepth, maxElems, maxFields, maxPath, nodeBudget;
        final boolean autoNest;   // recurse nested POJOs without an explicit allowlist (args do this)
        Limits(int d, int e, int f, int p, int n, boolean a) {
            maxDepth = d; maxElems = e; maxFields = f; maxPath = p; nodeBudget = n; autoNest = a;
        }
    }
    /** Returns: bounded — a deep return graph must not bloat the trace. */
    static final Limits RET  = new Limits(2, 50, 40, 8, 2000, false);
    /** Arguments: the condition. Loose bounds + auto-nest so the discriminating field is never the
     *  one we happened to drop. Caps exist only as a blow-up backstop and fire loudly when hit. */
    static final Limits ARGS = new Limits(6, 200, 512, 12, 50000, true);

    static final Set<String> NONE = Collections.emptySet();

    // Per-invocation truncation log + node-budget counter. Recorder brackets each capture with begin().
    private static final ThreadLocal<List<String>> TRUNC = new ThreadLocal<List<String>>() {
        protected List<String> initialValue() { return new ArrayList<String>(); }
    };
    private static final ThreadLocal<int[]> NODES = new ThreadLocal<int[]>() {
        protected int[] initialValue() { return new int[]{0}; }
    };

    private ValueExtractor() {}

    static void begin() { TRUNC.get().clear(); NODES.get()[0] = 0; }
    static List<String> truncations() { return new ArrayList<String>(TRUNC.get()); }

    private static void truncate(String path, String why) {
        List<String> l = TRUNC.get();
        if (l.size() < 256) l.add((path == null || path.isEmpty() ? "?" : path) + ": " + why);
    }
    private static boolean overBudget(Limits lim, String path) {
        int[] c = NODES.get();
        if (++c[0] > lim.nodeBudget) { truncate(path, "node budget " + lim.nodeBudget); return true; }
        return false;
    }

    static boolean isScalar(Object v) {
        return v == null || v instanceof String || v instanceof Number
                || v instanceof Boolean || v instanceof Character || v instanceof Enum;
    }

    static Object scalar(Object o) {
        Object v = (o == null || o instanceof String || o instanceof Number || o instanceof Boolean)
                ? o : String.valueOf(o);
        return Redactor.value(v);
    }

    // ---- public entry points (back-compatible signatures) -----------------
    static Object unfold(Object o, int depth) { return unfold(o, depth, NONE, "ret", RET); }
    static Object unfold(Object o, int depth, Set<String> paths) { return unfold(o, depth, paths, "ret", RET); }

    static Object unfoldArgs(String method, Object[] args, Set<String> paths) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        if (args == null) return m;
        String[] names = paramNames(method, args.length);
        for (int i = 0; i < args.length; i++) m.put(names[i], unfold(args[i], 0, paths, names[i], ARGS));
        return m;
    }

    // ---- the unfold core --------------------------------------------------
    @SuppressWarnings("unchecked")
    static Object unfold(Object o, int depth, Set<String> paths, String path, Limits lim) {
        if (o == null) return null;
        if (o instanceof String) return Redactor.value(o);
        if (o instanceof Number || o instanceof Boolean || o instanceof Character) return o;
        if (o instanceof Enum) return o.toString();
        if (o instanceof Optional) {
            Optional<?> op = (Optional<?>) o;
            return op.isPresent() ? unfold(op.get(), depth, paths, path, lim) : null;
        }
        if (o.getClass().isArray()) {
            List<Object> out = new ArrayList<Object>(); int n = Array.getLength(o);
            for (int i = 0; i < n; i++) {
                if (i >= lim.maxElems) { truncate(path, "element cap " + lim.maxElems + " (" + n + ")"); break; }
                if (overBudget(lim, path)) break;
                out.add(unfold(Array.get(o, i), depth, paths, path + "[" + i + "]", lim));
            }
            return out;
        }
        if (o instanceof Collection) {
            List<Object> out = new ArrayList<Object>(); int i = 0; int total = ((Collection<?>) o).size();
            for (Object e : (Collection<?>) o) {
                if (i >= lim.maxElems) { truncate(path, "element cap " + lim.maxElems + " (" + total + ")"); break; }
                if (overBudget(lim, path)) break;
                out.add(unfold(e, depth, paths, path + "[" + i + "]", lim)); i++;
            }
            return out;
        }
        if (o instanceof Map) {
            Map<String, Object> out = new LinkedHashMap<String, Object>(); int i = 0; int total = ((Map<?, ?>) o).size();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) o).entrySet()) {
                if (i >= lim.maxElems) { truncate(path, "entry cap " + lim.maxElems + " (" + total + ")"); break; }
                if (overBudget(lim, path)) break;
                i++;
                String k = String.valueOf(e.getKey());
                out.put(k, Redactor.denyName(k) ? "<redacted:" + k + ">"
                                                : unfold(e.getValue(), depth, paths, path + "." + k, lim));
            }
            return out;
        }
        // POJO: scalar fields (redacted) + nested fields (auto for args, allowlisted/collections for ret)
        if (depth >= lim.maxPath) { truncate(path, "max recursion " + lim.maxPath); return o.toString(); }
        List<Field> fields = allFields(o.getClass(), path);
        Map<String, Object> m = new LinkedHashMap<String, Object>(); int n = 0;
        for (Field f : fields) {
            if (Modifier.isStatic(f.getModifiers()) || f.isSynthetic()) continue;
            if (n >= lim.maxFields) {
                truncate(path, "field cap " + lim.maxFields + " (" + fields.size() + " fields)"); break;
            }
            if (overBudget(lim, path)) break;
            String fn = f.getName();
            Object v;
            try { f.setAccessible(true); v = f.get(o); } catch (Throwable t) { continue; }
            String cp = path + "." + fn;
            if (isScalar(v)) { m.put(fn, Redactor.field(fn, v instanceof Enum ? v.toString() : v)); n++; continue; }
            Set<String> sub = remainders(paths, fn);
            if (!sub.isEmpty()) {                                  // allowlisted nested path, depth-exempt
                m.put(fn, unfold(v, depth, sub, cp, lim)); n++;
            } else if (paths.contains(fn) && v != null) {
                m.put(fn, unfold(v, depth + 1, NONE, cp, lim)); n++;
            } else if (v != null && depth + 1 < lim.maxDepth
                    && (lim.autoNest || v instanceof Collection || v instanceof Optional || v.getClass().isArray())) {
                m.put(fn, unfold(v, depth + 1, paths, cp, lim)); n++;   // AUTO recurse (args) / collections (ret)
            } else if (v != null) {
                // would exceed depth: SHALLOW the field instead of dropping it. A missing field is what
                // collapses the condition (§11.1); present-but-shallow is recoverable.
                m.put(fn, v.toString()); n++;
                if (depth + 1 >= lim.maxDepth) truncate(cp, "max depth " + lim.maxDepth + " (shallowed)");
            }
        }
        return m.isEmpty() ? o.toString() : m;
    }

    private static Set<String> remainders(Set<String> paths, String field) {
        if (paths.isEmpty()) return NONE;
        Set<String> out = new HashSet<String>();
        String pre = field + ".";
        for (String p : paths) if (p.startsWith(pre)) out.add(p.substring(pre.length()));
        return out;
    }

    static Object unfoldArgs(String method, Object[] args) { return unfoldArgs(method, args, NONE); }

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

    /** All non-Object superclass fields, subclass-first. Walks the hierarchy deeply (a
     *  decision-governing field is often declared in a base class) and marks LOUDLY if it bottoms
     *  out the walk, so a deep hierarchy is never silently half-read. */
    private static List<Field> allFields(Class<?> c, String path) {
        List<Field> out = new ArrayList<Field>(); int d = 0;
        while (c != null && c != Object.class) {
            if (d++ >= 12) { truncate(path, "class hierarchy depth 12 (" + c.getName() + " not read)"); break; }
            out.addAll(Arrays.asList(c.getDeclaredFields())); c = c.getSuperclass();
        }
        return out;
    }
}
