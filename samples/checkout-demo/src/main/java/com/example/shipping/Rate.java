package com.example.shipping;
/** A shipping quote from a carrier. Decision fields are unfolded by the capture agent. */
public class Rate {
    public final String carrier;
    public final int price;    // what the customer pays
    public final int margin;   // what we keep
    public Rate(String carrier, int price, int margin) { this.carrier = carrier; this.price = price; this.margin = margin; }
    public String toString() { return carrier + "(price=" + price + ", margin=" + margin + ")"; }
}
