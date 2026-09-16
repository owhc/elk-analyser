// Updated: 2026-09-02 16:20:51 +0800
package com.demo.banking.model;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * Transaction domain model — represents a single transfer record.
 */
public class Transaction {

    private String txId;
    private String fromAccount;
    private String toAccount;
    private BigDecimal amount;
    private LocalDateTime txTime;
    private String status;
    private String description;

    public Transaction() {}

    public Transaction(String txId, String fromAccount, String toAccount,
                       BigDecimal amount, LocalDateTime txTime, String status) {
        this.txId = txId;
        this.fromAccount = fromAccount;
        this.toAccount = toAccount;
        this.amount = amount;
        this.txTime = txTime;
        this.status = status;
    }

    public String getTxId() { return txId; }
    public void setTxId(String txId) { this.txId = txId; }

    public String getFromAccount() { return fromAccount; }
    public void setFromAccount(String fromAccount) { this.fromAccount = fromAccount; }

    public String getToAccount() { return toAccount; }
    public void setToAccount(String toAccount) { this.toAccount = toAccount; }

    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }

    public LocalDateTime getTxTime() { return txTime; }
    public void setTxTime(LocalDateTime txTime) { this.txTime = txTime; }

    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
}
