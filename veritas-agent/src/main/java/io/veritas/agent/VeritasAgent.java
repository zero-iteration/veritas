package io.veritas.agent;

import net.bytebuddy.agent.builder.AgentBuilder;
import net.bytebuddy.asm.Advice;
import net.bytebuddy.description.method.MethodDescription;
import net.bytebuddy.matcher.ElementMatcher;
import net.bytebuddy.matcher.ElementMatchers;

import java.lang.instrument.Instrumentation;
import java.util.*;

import static net.bytebuddy.matcher.ElementMatchers.*;

/**
 * Veritas capture agent. Deterministic ByteBuddy instrumentation of a scoped package:
 * records executed methods, call edges, decision-site values (deep-unfolded), and live
 * config getter values. Writes the trace JSON on shutdown.
 *
 * Usage:
 *   -javaagent:veritas-agent.jar=scope=com.example;out=trace.json;captureValues=pick;configGetter=resolve
 *   -Dveritas.env=staging -Dveritas.sha=0fed1b1 -Dveritas.trace=run_0617
 */
public class VeritasAgent {

    public static void premain(String arg, Instrumentation inst) { install(arg, inst); }
    public static void agentmain(String arg, Instrumentation inst) { install(arg, inst); }

    static void install(String arg, Instrumentation inst) {
        Map<String, String> a = parse(arg);
        String scope = a.getOrDefault("scope", "com.example");
        String out = a.getOrDefault("out", "veritas-trace.json");
        String cv = a.getOrDefault("captureValues", "*");
        if ("*".equals(cv)) Recorder.captureAll = true;
        else for (String s : cv.split(",")) if (!s.trim().isEmpty()) Recorder.captureMethods.add(s.trim());

        for (String p : a.getOrDefault("unfold", "").split(","))      // §3.1-1 nested field paths
            if (!p.trim().isEmpty()) Recorder.unfoldPaths.add(p.trim());
        if ("false".equalsIgnoreCase(a.getOrDefault("redact", "true"))) Redactor.enabled = false;
        long flushMs = 0;                                               // §3.1-5 periodic flush
        try { flushMs = Long.parseLong(a.getOrDefault("flushMs", "0").trim()); }
        catch (NumberFormatException e) { System.err.println("[veritas] bad flushMs, disabling periodic flush"); }

        Set<String> getters = new HashSet<>();
        for (String s : a.getOrDefault("configGetter", "").split(","))
            if (!s.trim().isEmpty()) getters.add(s.trim());

        ElementMatcher.Junction<net.bytebuddy.description.type.TypeDescription> typeScope = nameStartsWith(scope);

        AgentBuilder b = new AgentBuilder.Default()
                .with(AgentBuilder.RedefinitionStrategy.RETRANSFORMATION)
                .ignore(nameStartsWith("io.veritas").or(nameStartsWith("io.veritas.shaded")).or(isSynthetic()))
                .type(typeScope)
                .transform((builder, type, cl, module, pd) ->
                        builder.visit(Advice.to(MethodAdvice.class)
                                .on(isMethod().and(not(isConstructor())).and(not(isAbstract())).and(not(isNative())))));

        if (!getters.isEmpty()) {
            ElementMatcher.Junction<MethodDescription> gm = ElementMatchers.<MethodDescription>none();
            for (String g : getters) gm = gm.or(named(g).and(takesArgument(0, String.class)));
            final ElementMatcher.Junction<MethodDescription> gmf = gm;
            b = b.type(typeScope).transform((builder, type, cl, module, pd) ->
                    builder.visit(Advice.to(ConfigAdvice.class).on(gmf)));
        }

        b.installOn(inst);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> TraceWriter.write(out)));
        if (flushMs > 0) {                                  // §3.1-5 periodic flush (crash-safe)
            final long every = flushMs;
            Thread flusher = new Thread(() -> {
                while (true) {
                    try { Thread.sleep(every); } catch (InterruptedException e) { return; }
                    TraceWriter.write(out);
                }
            });
            flusher.setDaemon(true); flusher.setName("veritas-flush"); flusher.start();
        }
        System.err.println("[veritas] scope=" + scope + " out=" + out + " captureValues=" + cv
                + " unfold=" + Recorder.unfoldPaths + " redact=" + Redactor.enabled + " configGetter=" + getters);
    }

    private static Map<String, String> parse(String arg) {
        Map<String, String> m = new HashMap<>();
        if (arg == null) return m;
        for (String part : arg.split(";")) {
            int eq = part.indexOf('=');
            if (eq > 0) m.put(part.substring(0, eq).trim(), part.substring(eq + 1).trim());
        }
        return m;
    }
}
