-- PostgreSQL 16 schema — Banking Demo
-- Updated: 2026-09-15 19:56:07 +0800
-- PostgreSQL image 啟動時自動執行此檔案

-- ── Schema ──────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS banking;

-- ── Accounts ────────────────────────────────────────────────────────
CREATE TABLE banking.accounts (
    account_id   VARCHAR(20)     NOT NULL PRIMARY KEY,
    owner_name   VARCHAR(100)    NOT NULL,
    balance      DECIMAL(15,2)   NOT NULL DEFAULT 0.00,
    account_type VARCHAR(20)     NOT NULL DEFAULT 'SAVINGS',
    status       VARCHAR(10)     NOT NULL DEFAULT 'ACTIVE',
    created_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ── Transactions ─────────────────────────────────────────────────────
CREATE TABLE banking.transactions (
    tx_id        VARCHAR(36)     NOT NULL PRIMARY KEY,
    from_account VARCHAR(20),
    to_account   VARCHAR(20),
    amount       DECIMAL(15,2)   NOT NULL,
    tx_type      VARCHAR(20)     NOT NULL DEFAULT 'TRANSFER',
    status       VARCHAR(20)     NOT NULL DEFAULT 'PENDING',
    description  VARCHAR(200),
    tx_time      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_account) REFERENCES banking.accounts(account_id),
    FOREIGN KEY (to_account)   REFERENCES banking.accounts(account_id)
);

CREATE INDEX idx_tx_from ON banking.transactions(from_account);
CREATE INDEX idx_tx_to   ON banking.transactions(to_account);
CREATE INDEX idx_tx_time ON banking.transactions(tx_time DESC);

-- ── Seed data ────────────────────────────────────────────────────────
INSERT INTO banking.accounts VALUES
    ('ACC001', '王大明', 100000.00, 'CHECKING', 'ACTIVE', NOW()),
    ('ACC002', '李小美',  50000.00, 'SAVINGS',  'ACTIVE', NOW()),
    ('ACC003', '張志偉',  75000.00, 'CHECKING', 'ACTIVE', NOW());

INSERT INTO banking.transactions VALUES
    ('TXN-001', 'ACC001', 'ACC002',  5000.00, 'TRANSFER', 'COMPLETED', '薪資轉帳', NOW() - INTERVAL '7 days'),
    ('TXN-002', 'ACC002', 'ACC003',  2000.00, 'TRANSFER', 'COMPLETED', '日常轉帳', NOW() - INTERVAL '5 days'),
    ('TXN-003', NULL,     'ACC001', 10000.00, 'DEPOSIT',  'COMPLETED', '存款',     NOW() - INTERVAL '3 days'),
    ('TXN-004', 'ACC003', 'ACC001',  1500.00, 'TRANSFER', 'COMPLETED', '還款',     NOW() - INTERVAL '1 day'),
    ('TXN-005', 'ACC001', 'ACC003',  3000.00, 'TRANSFER', 'COMPLETED', '借款',     NOW() - INTERVAL '2 hours');
