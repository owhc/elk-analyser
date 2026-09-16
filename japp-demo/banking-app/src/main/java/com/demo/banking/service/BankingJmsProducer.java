// Updated: 2026-09-03 15:00:17 +0800
package com.demo.banking.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.jms.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jms.core.JmsTemplate;
import org.springframework.stereotype.Service;

import javax.sql.DataSource;
import java.math.BigDecimal;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * JMS message producer — 銀行 Web 層訊息服務。
 *
 * <h3>架構決策</h3>
 * <ul>
 *   <li><b>QUERY（帳號查詢）</b>: 直接 JDBC（不走 JMS），
 *       避免阻塞 Liberty HTTP thread pool。JMS request-reply 模式
 *       在 Liberty 環境下因 thread 耗盡而不穩定。</li>
 *   <li><b>TRANSFER（轉帳）</b>: 透過 JMS fire-and-forget，
 *       BankingMDB 非同步消費並執行原子化轉帳。</li>
 * </ul>
 */
@Service
public class BankingJmsProducer {

    private static final Logger log = LoggerFactory.getLogger(BankingJmsProducer.class);

    static final String REQUEST_QUEUE = "bankingQueue";

    @Autowired
    private JmsTemplate jmsTemplate;

    @Autowired
    private DataSource dataSource;

    @Autowired
    private ObjectMapper objectMapper;

    /**
     * Spring executor（WAR classloader context）用於 JDBC 查詢。
     * Liberty executor thread 的 classloader 不包含 WAR 內的 PostgreSQL driver，
     * 導致 getConnection() hang。改用此 executor 確保在正確的 classloader 下執行。
     */
    private final java.util.concurrent.ExecutorService jdbcExecutor =
        java.util.concurrent.Executors.newFixedThreadPool(4, r -> {
            Thread t = new Thread(r, "jdbc-pool-" + System.nanoTime());
            t.setDaemon(true);
            return t;
        });

    /**
     * 在背景預熱 DataSource（確保第一次查詢前連線已建立）
     */
    @jakarta.annotation.PostConstruct
    public void warmUpDataSource() {
        jdbcExecutor.submit(() -> {
            try {
                Thread.sleep(3_000);
                try (java.sql.Connection conn = dataSource.getConnection()) {
                    log.info("DataSource warm-up OK");
                }
            } catch (Exception e) {
                log.warn("DataSource warm-up failed: {}", e.getMessage());
            }
        });
    }

    // -----------------------------------------------------------------------
    // QUERY — 直接 JDBC（不走 JMS，避免 blocking receive 耗盡 thread pool）
    // -----------------------------------------------------------------------

    /**
     * 查詢帳號資訊，直接使用 DataSource 存取 PostgreSQL。
     * 切換 TCCL（Thread Context ClassLoader）確保 Liberty executor thread
     * 能正確找到 WAR 內的 PostgreSQL JDBC driver。
     *
     * @return JSON 字串（accountId, ownerName, balance, transactions[]）；失敗時回 null
     */
    public String queryAccount(String accountId) {
        log.info("QUERY accountId={}", accountId);
        // JDBC 必須在 jdbcExecutor 的 thread 上執行（WAR classloader context）
        // Liberty executor thread 的 classloader 不包含 WAR 的 PostgreSQL driver
        try {
            java.util.concurrent.Future<String> f = jdbcExecutor.submit(() -> executeQuery(accountId));
            return f.get(10, java.util.concurrent.TimeUnit.SECONDS);
        } catch (java.util.concurrent.TimeoutException e) {
            log.warn("QUERY timeout for accountId={}", accountId);
            return null;
        } catch (Exception e) {
            log.error("QUERY error for accountId={}: {}", accountId, e.getMessage(), e);
            return null;
        }
    }

    private String executeQuery(String accountId) throws Exception {
        try (java.sql.Connection conn = dataSource.getConnection()) {
            ObjectNode result = objectMapper.createObjectNode();

            // 1. 帳號基本資料
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT account_id, owner_name, balance FROM banking.accounts WHERE account_id = ?")) {
                ps.setString(1, accountId);
                try (ResultSet rs = ps.executeQuery()) {
                    if (rs.next()) {
                        result.put("accountId", rs.getString("account_id"));
                        result.put("ownerName", rs.getString("owner_name"));
                        result.put("balance",   rs.getBigDecimal("balance").toPlainString());
                    } else {
                        result.put("accountId", accountId);
                        result.put("ownerName", "未知帳號");
                        result.put("balance",   "0.00");
                    }
                }
            }

            // 2. 最近 10 筆交易
            ArrayNode txArray = result.putArray("transactions");
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT tx_id, from_account, to_account, amount, tx_time, status, description " +
                    "FROM banking.transactions " +
                    "WHERE from_account = ? OR to_account = ? " +
                    "ORDER BY tx_time DESC LIMIT 10")) {
                ps.setString(1, accountId);
                ps.setString(2, accountId);
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        ObjectNode tx = txArray.addObject();
                        tx.put("txId",        rs.getString("tx_id"));
                        tx.put("fromAccount", rs.getString("from_account"));
                        tx.put("toAccount",   rs.getString("to_account"));
                        tx.put("amount",      rs.getBigDecimal("amount").toPlainString());
                        tx.put("txTime",      rs.getTimestamp("tx_time").toLocalDateTime().toString());
                        tx.put("status",      rs.getString("status"));
                        tx.put("description", rs.getString("description") != null
                                              ? rs.getString("description") : "");
                    }
                }
            }

            log.info("QUERY done accountId={}", accountId);
            return objectMapper.writeValueAsString(result);
        }
    }

    // -----------------------------------------------------------------------
    // TRANSFER — JMS fire-and-forget
    // -----------------------------------------------------------------------

    /**
     * 發送 TRANSFER 訊息至 bankingQueue（fire-and-forget）。
     * BankingMDB 非同步消費後執行原子化轉帳。
     */
    public void sendTransfer(String fromAccount, String toAccount,
                             BigDecimal amount, String description) {
        String requestId = UUID.randomUUID().toString();
        log.info("Sending TRANSFER from={} to={} amount={} requestId={}",
                 fromAccount, toAccount, amount, requestId);

        try {
            Map<String, Object> envelope = new HashMap<>();
            envelope.put("op", "TRANSFER");
            envelope.put("requestId", requestId);
            Map<String, Object> payload = new HashMap<>();
            payload.put("fromAccount", fromAccount);
            payload.put("toAccount",   toAccount);
            payload.put("amount",      amount);
            payload.put("description", description != null ? description : "");
            envelope.put("payload", payload);
            final String jsonBody = objectMapper.writeValueAsString(envelope);

            jmsTemplate.send(REQUEST_QUEUE, session -> {
                TextMessage msg = session.createTextMessage(jsonBody);
                msg.setJMSCorrelationID(requestId);
                return msg;
            });

            log.info("TRANSFER message queued requestId={}", requestId);

        } catch (Exception ex) {
            log.error("Error sending TRANSFER: {}", ex.getMessage(), ex);
            throw new RuntimeException("轉帳訊息發送失敗，請稍後再試", ex);
        }
    }
}
