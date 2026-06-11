package io.veritas.agent;

import net.bytebuddy.asm.Advice;
import net.bytebuddy.implementation.bytecode.assign.Assigner;

/** Inlined entry/exit advice. Records method execution, call edges, and (for
 *  capture-listed methods) the unfolded argument + return values at the decision site. */
public class MethodAdvice {

    @Advice.OnMethodEnter
    static void enter(@Advice.Origin("#t.#m") String method) {
        Recorder.onEnter(method);
    }

    @Advice.OnMethodExit(onThrowable = Throwable.class)
    static void exit(@Advice.Origin("#t.#m") String method,
                     @Advice.AllArguments Object[] args,
                     @Advice.Return(typing = Assigner.Typing.DYNAMIC) Object ret) {
        Recorder.onExit(method, args, ret);
    }
}
