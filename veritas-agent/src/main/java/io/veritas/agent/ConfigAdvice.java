package io.veritas.agent;

import net.bytebuddy.asm.Advice;
import net.bytebuddy.implementation.bytecode.assign.Assigner;

/** Instruments a property getter `(String key) -> value` so observed-LIVE config
 *  populates per key. Joined against file-declared values -> the config-divergence line. */
public class ConfigAdvice {

    @Advice.OnMethodExit(onThrowable = Throwable.class)
    static void exit(@Advice.AllArguments Object[] args,
                     @Advice.Return(typing = Assigner.Typing.DYNAMIC) Object ret) {
        if (args != null && args.length >= 1 && args[0] instanceof String) {
            Recorder.config((String) args[0], ret);
        }
    }
}
