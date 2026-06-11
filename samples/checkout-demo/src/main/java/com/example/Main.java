package com.example;
import com.example.shipping.*;
import com.example.config.PropertyResolver;
import java.util.*;
public class Main {
    public static void main(String[] a) {
        RateSelector sel = new RateSelector(new PropertyResolver());
        for (int req = 0; req < 3; req++) {
            List<Rate> candidates = Arrays.asList(
                new Rate("STANDARD", 549, 540, new Breakdown(520, 29), "alice@example.com"),
                new Rate("PARTNER",  519, 510, new Breakdown(491, 28), "bob@example.com"));
            Rate chosen = sel.pick(candidates);
            System.out.println("request " + req + " -> chose " + chosen);
        }
    }
}
