package com.example.shipping;
import com.example.config.PropertyResolver;
import java.util.List;
/** Picks a shipping rate. SHOULD pick the cheapest, but the live strategy maximizes margin. */
public class RateSelector {
    private final PropertyResolver resolver;
    private String strategy;
    public RateSelector(PropertyResolver resolver) { this.resolver = resolver; }

    public Rate pick(List<Rate> candidates) {
        strategy = resolver.resolve("rate.selection.strategy");
        Rate best = null; int bestScore = Integer.MIN_VALUE;
        for (Rate r : candidates) {
            int s = score(r);
            if (s > bestScore) { bestScore = s; best = r; }
        }
        return best;
    }
    int score(Rate r) {
        return "cheapest_selection".equals(strategy) ? -r.price : r.margin; // bug: margin path live
    }
}
