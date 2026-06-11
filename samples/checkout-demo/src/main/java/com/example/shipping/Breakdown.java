package com.example.shipping;
/** Nested POJO — netPrice lives TWO levels deep (Rate.breakdown.netPrice). */
public class Breakdown {
    public final int netPrice;
    public final int tax;
    public Breakdown(int netPrice, int tax) { this.netPrice = netPrice; this.tax = tax; }
}
