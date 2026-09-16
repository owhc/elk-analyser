// Updated: 2026-09-03 13:39:28 +0800
package com.demo.banking.mdb;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.apache.activemq.artemis.api.core.TransportConfiguration;
import org.apache.activemq.artemis.api.jms.ActiveMQJMSClient;
import org.apache.activemq.artemis.api.jms.JMSFactoryType;
import org.apache.activemq.artemis.core.remoting.impl.netty.NettyConnectorFactory;
import org.apache.activemq.artemis.core.remoting.impl.netty.TransportConstants;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import jakarta.jms.*;
import javax.sql.DataSource;
import java.math.BigDecimal;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Banking Message-Driven Bean — 直接使用 Artemis JMS client（繞過 Spring DMLC）。
 *
 * <p>在 Liberty 環境下，Spring {@code DefaultMessageListenerContainer} 受 Liberty JTA
 * 干擾，無法在 sessionTransacted 模式下穩定建立 JMS consumer。
 * 本實作用 {@code @PostConstruct} 啟動一個 daemon consumer thread，直接建立
 * Artemis native JMS session（LOCAL_TRANSACTION），不依賴 Spring DMLC。</p>
 *
 * <h3>支援操作</h3>
 * <ul>
 *   <li>{@code QUERY}    — 讀取帳號資訊，寫回 JMSReplyTo（bankingReplyQueue）</li>
 *   <li>{@code TRANSFER} — 原子化轉帳（INSERT + 2x UPDATE）</li>
 * </ul>
 */
@Component
public class BankingMDB {

    private static final Logger log = Logger.getLogger(BankingMDB.class.getName());

    private static final String REQUEST_QUEUE = "bankingQueue";

    @Autowired
    private DataSource dataSource;

    @Value("${mq.broker-url:tcp://localhost:61616}")
    private String brokerUrl;

    @Value("${mq.user:admin}")
    private String mqUser;

    @Value("${mq.password:admin123}")
    private String mqPassword;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final AtomicBoolean running = new AtomicBoolean(false);
    private ExecutorService consumerExecutor;
    private Connection jmsConnection;

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    @PostConstruct
    public void startConsumer() {
        running.set(true);
        consumerExecutor = Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "BankingMDB-Consumer");
            t.setDaemon(true);
            return t;
        });
        consumerExecutor.submit(this::consumerLoop);
        log.info("BankingMDB: consumer thread started, listening on " + REQUEST_QUEUE);
    }

    @PreDestroy
    public void stopConsumer() {
        running.set(false);
        if (consumerExecutor != null) consumerExecutor.shutdownNow();
        closeConnection();
        log.info("BankingMDB: consumer thread stopped");
    }

    // -----------------------------------------------------------------------
    // Consumer loop — reconnects on failure
    // -----------------------------------------------------------------------

    private void consumerLoop() {
        while (running.get()) {
            try {
                connectAndConsume();
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                break;
            } catch (Exception ex) {
                if (!running.get()) break;
                log.log(Level.WARNING, "BankingMDB: consumer error, reconnecting in 5s: " + ex.getMessage());
                closeConnection();
                try { Thread.sleep(5_000); } catch (InterruptedException ie) { break; }
            }
        }
    }

    private void connectAndConsume() throws Exception {
        // 解析 broker URL（tcp://host:port?params → host:port）
        String url = brokerUrl.replaceFirst("tcp://", "").replaceFirst("\\?.*", "");
        String host = url.contains(":") ? url.split(":")[0] : url;
        int port = url.contains(":") ? Integer.parseInt(url.split(":")[1]) : 61616;

        Map<String, Object> params = new HashMap<>();
        params.put(TransportConstants.HOST_PROP_NAME, host);
        params.put(TransportConstants.PORT_PROP_NAME, port);

        TransportConfiguration tc = new TransportConfiguration(
            NettyConnectorFactory.class.getName(), params);

        ConnectionFactory cf = ActiveMQJMSClient.createConnectionFactoryWithoutHA(
            JMSFactoryType.CF, tc);

        jmsConnection = cf.createConnection(mqUser, mqPassword);
        jmsConnection.start();

        Session session = jmsConnection.createSession(false, Session.AUTO_ACKNOWLEDGE);
        Queue queue = session.createQueue(REQUEST_QUEUE);
        MessageConsumer consumer = session.createConsumer(queue);

        log.info("BankingMDB: connected to Artemis " + host + ":" + port
                 + ", consuming from " + REQUEST_QUEUE);

        while (running.get()) {
            Message msg = consumer.receive(2_000); // 2 秒 timeout，讓迴圈能定期檢查 running
            if (msg == null) continue;
            if (msg instanceof TextMessage textMsg) {
                onMessage(textMsg, session);
            } else {
                log.warning("BankingMDB: unexpected message type " + msg.getClass().getName());
            }
        }

        consumer.close();
        session.close();
    }

    private void closeConnection() {
        if (jmsConnection != null) {
            try { jmsConnection.close(); } catch (Exception e) { /* ignore */ }
            jmsConnection = null;
        }
    }

    // -----------------------------------------------------------------------
    // Message dispatch
    // -----------------------------------------------------------------------

    private void onMessage(TextMessage textMessage, Session session) {
        try {
            String body = textMessage.getText();
            log.info("BankingMDB received: " + body);

            JsonNode envelope = objectMapper.readTree(body);
            String op        = envelope.path("op").asText("");
            String requestId = envelope.path("requestId").asText(UUID.randomUUID().toString());
            JsonNode payload = envelope.path("payload");

            switch (op.toUpperCase()) {
                case "QUERY"    -> handleQuery(textMessage, session, payload, requestId);
                case "TRANSFER" -> handleTransfer(payload, requestId);
                default         -> log.warning("BankingMDB: unknown op=" + op);
            }
        } catch (Exception ex) {
            log.log(Level.SEVERE, "BankingMDB: error processing message", ex);
        }
    }

    // -----------------------------------------------------------------------
    // QUERY handler
    // -----------------------------------------------------------------------

    private void handleQuery(TextMessage request, Session session,
                              JsonNode payload, String requestId) throws Exception {

        String accountId = payload.path("accountId").asText();
        log.info("BankingMDB QUERY accountId=" + accountId);

        ObjectNode result = objectMapper.createObjectNode();

        // 切換 TCCL 確保 Liberty executor thread 能找到 WAR 內的 PostgreSQL driver
        ClassLoader origCl = Thread.currentThread().getContextClassLoader();
        Thread.currentThread().setContextClassLoader(dataSource.getClass().getClassLoader());
        try (java.sql.Connection conn = dataSource.getConnection()) {

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
                        tx.put("txTime",      rs.getTimestamp("tx_time")
                                               .toLocalDateTime().toString());
                        tx.put("status",      rs.getString("status"));
                        tx.put("description", rs.getString("description") != null
                                              ? rs.getString("description") : "");
                    }
                }
            }
        } finally {
            Thread.currentThread().setContextClassLoader(origCl);
        }

        // 3. 送回 reply
        Destination replyTo = request.getJMSReplyTo();
        log.info("BankingMDB QUERY replyTo=" + replyTo);
        if (replyTo == null) {
            log.warning("BankingMDB QUERY: no JMSReplyTo, dropping reply");
            return;
        }

        final String replyBody = objectMapper.writeValueAsString(result);
        final String corrId    = request.getJMSCorrelationID();

        MessageProducer producer = session.createProducer(replyTo);
        TextMessage reply = session.createTextMessage(replyBody);
        reply.setJMSCorrelationID(corrId);
        producer.send(reply);
        producer.close();

        log.info("BankingMDB QUERY reply sent for requestId=" + requestId);
    }

    // -----------------------------------------------------------------------
    // TRANSFER handler
    // -----------------------------------------------------------------------

    private void handleTransfer(JsonNode payload, String requestId) {
        String fromAccount = payload.path("fromAccount").asText();
        String toAccount   = payload.path("toAccount").asText();
        BigDecimal amount  = new BigDecimal(payload.path("amount").asText("0"));
        String description = payload.path("description").asText("");

        log.info("BankingMDB TRANSFER from=" + fromAccount + " to=" + toAccount +
                 " amount=" + amount);

        try (java.sql.Connection conn = dataSource.getConnection()) {
            conn.setAutoCommit(false);

            try {
                // 1. 插入交易記錄
                String txId = UUID.randomUUID().toString();
                try (PreparedStatement ps = conn.prepareStatement(
                        "INSERT INTO banking.transactions " +
                        "(tx_id, from_account, to_account, amount, tx_time, status, description) " +
                        "VALUES (?, ?, ?, ?, ?, ?, ?)")) {
                    ps.setString(1, txId);
                    ps.setString(2, fromAccount);
                    ps.setString(3, toAccount);
                    ps.setBigDecimal(4, amount);
                    ps.setTimestamp(5, Timestamp.valueOf(LocalDateTime.now()));
                    ps.setString(6, "已完成");
                    ps.setString(7, description);
                    ps.executeUpdate();
                }

                // 2. 扣款
                try (PreparedStatement ps = conn.prepareStatement(
                        "UPDATE banking.accounts SET balance = balance - ? WHERE account_id = ?")) {
                    ps.setBigDecimal(1, amount);
                    ps.setString(2, fromAccount);
                    if (ps.executeUpdate() == 0)
                        throw new SQLException("來源帳號不存在: " + fromAccount);
                }

                // 3. 入帳
                try (PreparedStatement ps = conn.prepareStatement(
                        "UPDATE banking.accounts SET balance = balance + ? WHERE account_id = ?")) {
                    ps.setBigDecimal(1, amount);
                    ps.setString(2, toAccount);
                    if (ps.executeUpdate() == 0)
                        throw new SQLException("目標帳號不存在: " + toAccount);
                }

                conn.commit();
                log.info("BankingMDB TRANSFER committed requestId=" + requestId);

            } catch (Exception ex) {
                conn.rollback();
                log.log(Level.SEVERE, "BankingMDB TRANSFER rolled back requestId=" + requestId, ex);
            }

        } catch (SQLException ex) {
            log.log(Level.SEVERE, "BankingMDB TRANSFER DB error requestId=" + requestId, ex);
        }
    }
}
