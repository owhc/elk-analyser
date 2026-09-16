// Updated: 2026-09-02 16:20:51 +0800
package com.demo.banking.model;

import java.math.BigDecimal;

/**
 * Transfer request payload — initiates a fund transfer via JMS.
 */
public class TransferRequest {

    private String fromAccount;
    private String toAccount;
    private BigDecimal amount;
    private String description;

    public TransferRequest() {}

    public String getFromAccount() { return fromAccount; }
    public void setFromAccount(String fromAccount) { this.fromAccount = fromAccount; }

    public String getToAccount() { return toAccount; }
    public void setToAccount(String toAccount) { this.toAccount = toAccount; }

    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
}
