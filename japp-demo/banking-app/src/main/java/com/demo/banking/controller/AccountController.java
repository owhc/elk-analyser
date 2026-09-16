// Updated: 2026-09-03 16:19:07 +0800
package com.demo.banking.controller;

import com.demo.banking.model.Account;
import com.demo.banking.model.ApiResponse;
import com.demo.banking.model.Transaction;
import com.demo.banking.service.BankingJmsProducer;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Account information controller.
 *
 * <h3>Endpoints</h3>
 * <ul>
 *   <li>{@code GET /banking-app/api/accounts/{accountId}} — returns account + recent transactions</li>
 *   <li>{@code GET /banking-app/api/health}               — simple liveness probe for Liberty health checks</li>
 * </ul>
 *
 * <h3>Data flow</h3>
 * <pre>
 * Browser → GET /api/accounts/{id}
 *         → BankingJmsProducer.queryAccount(id)  [JMS QUERY message]
 *         → Liberty MDB (BankingMDB)             [PostgreSQL SELECT]
 *         → reply TextMessage (JSON)             [back to JmsTemplate.sendAndReceive]
 *         → parse → Account domain object → ResponseEntity
 * </pre>
 *
 * If the JMS call times out (MDB unavailable or DB down), mock fallback
 * data is returned so the UI remains functional during a demo.
 */
@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*")
public class AccountController {

    private static final Logger log = LoggerFactory.getLogger(AccountController.class);

    private final BankingJmsProducer jmsProducer;
    private final ObjectMapper objectMapper;

    @Autowired
    public AccountController(BankingJmsProducer jmsProducer, ObjectMapper objectMapper) {
        this.jmsProducer  = jmsProducer;
        this.objectMapper = objectMapper;
    }

    /**
     * Retrieves account details and recent transactions.
     *
     * @param accountId the account identifier (e.g. "ACC001")
     * @return account data or fallback mock data on JMS timeout
     */
    @GetMapping("/accounts/{accountId}")
    public ResponseEntity<ApiResponse<Account>> getAccount(
            @PathVariable String accountId) {

        log.info("Account query for accountId={}", accountId);

        String jsonReply = jmsProducer.queryAccount(accountId);

        if (jsonReply != null) {
            try {
                Account account = parseAccountFromMdbReply(jsonReply);
                return ResponseEntity.ok(ApiResponse.ok(account));
            } catch (Exception ex) {
                log.error("Failed to parse MDB reply for accountId={}: {}", accountId, ex.getMessage(), ex);
            }
        }

        // JMS timeout or parse failure — return mock data to keep the demo running
        log.warn("Using mock fallback data for accountId={}", accountId);
        Account fallback = buildFallbackAccount(accountId);
        return ResponseEntity.ok(ApiResponse.ok(fallback));
    }

    /**
     * Simple health check endpoint used by Podman Compose health check config.
     */
    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> health() {
        Map<String, String> status = new HashMap<>();
        status.put("status", "UP");
        status.put("service", "banking-app");
        return ResponseEntity.ok(status);
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /**
     * Parses the JSON reply sent by {@link com.demo.banking.mdb.BankingMDB}.
     *
     * Expected MDB reply format:
     * <pre>
     * {
     *   "accountId": "ACC001",
     *   "ownerName": "王大明",
     *   "balance": 100000.00,
     *   "transactions": [
     *     { "txId": "...", "fromAccount": "...", "toAccount": "...",
     *       "amount": ..., "txTime": "...", "status": "...", "description": "..." }
     *   ]
     * }
     * </pre>
     */
    private Account parseAccountFromMdbReply(String json) throws Exception {
        JsonNode root = objectMapper.readTree(json);

        Account account = new Account();
        account.setAccountId(root.path("accountId").asText());
        account.setOwnerName(root.path("ownerName").asText());
        account.setBalance(new BigDecimal(root.path("balance").asText("0")));

        List<Transaction> txList = new ArrayList<>();
        JsonNode txArray = root.path("transactions");
        if (txArray.isArray()) {
            for (JsonNode txNode : txArray) {
                Transaction tx = new Transaction();
                tx.setTxId(txNode.path("txId").asText());
                tx.setFromAccount(txNode.path("fromAccount").asText());
                tx.setToAccount(txNode.path("toAccount").asText());
                tx.setAmount(new BigDecimal(txNode.path("amount").asText("0")));
                tx.setStatus(txNode.path("status").asText());
                tx.setDescription(txNode.path("description").asText());
                String txTimeStr = txNode.path("txTime").asText("");
                if (!txTimeStr.isBlank()) {
                    tx.setTxTime(LocalDateTime.parse(txTimeStr));
                }
                txList.add(tx);
            }
        }
        account.setRecentTransactions(txList);
        return account;
    }

    /**
     * Builds static mock account data when the MDB/DB path is unavailable.
     * This keeps the front-end demo functional even if MQ or DB is not running.
     */
    private Account buildFallbackAccount(String accountId) {
        Map<String, String[]> mockAccounts = new HashMap<>();
        mockAccounts.put("ACC001", new String[]{"王大明", "100000.00"});
        mockAccounts.put("ACC002", new String[]{"李小美",  "50000.00"});
        mockAccounts.put("ACC003", new String[]{"張志偉",  "75000.00"});

        String[] info = mockAccounts.getOrDefault(accountId, new String[]{"未知用戶", "0.00"});
        Account account = new Account(accountId, info[0], new BigDecimal(info[1]));

        List<Transaction> mockTx = new ArrayList<>();
        mockTx.add(new Transaction("TX-MOCK-001", "ACC002", accountId,
                new BigDecimal("5000.00"), LocalDateTime.now().minusDays(1), "已完成"));
        mockTx.add(new Transaction("TX-MOCK-002", accountId, "ACC003",
                new BigDecimal("2000.00"), LocalDateTime.now().minusDays(3), "已完成"));
        mockTx.add(new Transaction("TX-MOCK-003", "ACC001", accountId,
                new BigDecimal("10000.00"), LocalDateTime.now().minusDays(7), "已完成"));
        account.setRecentTransactions(mockTx);

        return account;
    }
}
