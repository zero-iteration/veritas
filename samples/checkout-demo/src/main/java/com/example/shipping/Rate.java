package com.example.shipping;
public class Rate {
    public final String carrier;
    public final int price;
    public final int margin;
    public final Breakdown breakdown;       // nested decision field
    public final String customerEmail;      // PII — must be redacted
    public Rate(String carrier, int price, int margin, Breakdown breakdown, String customerEmail) {
        this.carrier = carrier; this.price = price; this.margin = margin;
        this.breakdown = breakdown; this.customerEmail = customerEmail;
    }
    public String toString() { return carrier + "(price=" + price + ", margin=" + margin + ")"; }
}
