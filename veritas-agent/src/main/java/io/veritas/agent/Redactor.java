package io.veritas.agent;

import java.util.*;
import java.util.regex.Pattern;

/** PII redaction (§3.1-2): a field/key-name denylist + value-shape redactors, applied
 *  inside ValueExtractor BEFORE anything is written. Capture must never leak secrets. */
public final class Redactor {
    private Redactor() {}

    static final Set<String> NAME_DENY = new HashSet<>(Arrays.asList(
            "email", "mail", "pan", "pnr", "phone", "mobile", "contact", "token", "password",
            "passwd", "secret", "otp", "card", "cardno", "cvv", "cvc", "aadhaar", "aadhar",
            "ssn", "dob", "authorization", "apikey", "api_key", "passport"));

    static final Pattern EMAIL = Pattern.compile("[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}");
    static final Pattern LONG_DIGITS = Pattern.compile("\\b\\d{9,}\\b");      // phones/cards/aadhaar
    static final Pattern PNR = Pattern.compile("\\b[A-Z0-9]{6,8}\\b");        // conservative; only on deny-named fields

    static volatile boolean enabled = true;

    static boolean denyName(String name) {
        if (name == null) return false;
        String n = name.toLowerCase();
        for (String d : NAME_DENY) if (n.contains(d)) return true;
        return false;
    }

    /** Redact a scalar value by its field/key NAME and its shape. */
    static Object field(String name, Object value) {
        if (!enabled) return value;
        if (denyName(name)) return "<redacted:" + name + ">";
        return value(value);
    }

    /** Shape-based redaction independent of name. Replaces only the MATCHED SPAN (an embedded
     *  email/long-number in a larger string is redacted in place; surrounding domain text is
     *  preserved). Number-typed values never reach here (they skip Redactor.value). */
    static Object value(Object v) {
        if (!enabled || !(v instanceof String)) return v;
        String s = (String) v;
        String r = EMAIL.matcher(s).replaceAll("<redacted:email>");
        r = LONG_DIGITS.matcher(r).replaceAll("<redacted:number>");
        return r;
    }
}
