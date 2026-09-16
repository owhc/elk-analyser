// Updated: 2026-09-02 16:20:51 +0800
package com.demo.banking.model;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

/**
 * Account domain model.
 * Simple POJO (not a Spring bean) — carried across layers as plain data.
 */
public class Account {

    private String accountId;
    private String ownerName;
    private BigDecimal balance;
    private LocalDateTime createdAt;
    private List<Transaction> recentTransactions;

    public Account() {}

    public Account(String accountId, String ownerName, BigDecimal balance) {
        this.accountId = accountId;
        this.ownerName = ownerName;
        this.balance = balance;
    }

    public String getAccountId() { return accountId; }
    public void setAccountId(String accountId) { this.accountId = accountId; }

    public String getOwnerName() { return ownerName; }
    public void setOwnerName(String ownerName) { this.ownerName = ownerName; }

    public BigDecimal getBalance() { return balance; }
    public void setBalance(BigDecimal balance) { this.balance = balance; }

    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }

    public List<Transaction> getRecentTransactions() { return recentTransactions; }
    public void setRecentTransactions(List<Transaction> recentTransactions) {
        this.recentTransactions = recentTransactions;
    }
}
