package com.example.config;
import java.util.*;
/** Resolves runtime config. The LIVE value here diverges from the file-declared one. */
public class PropertyResolver {
    private final Map<String,String> live = new HashMap<>();
    public PropertyResolver() {
        // file declares rate.selection.strategy=cheapest_selection, but an ops override
        // / profile makes the RUNTIME value margin_max_v3 — the divergence Veritas catches.
        live.put("rate.selection.strategy", "margin_max_v3");
        live.put("rate.fallback.enabled", "true");
    }
    public String resolve(String key) { return live.get(key); }
}
