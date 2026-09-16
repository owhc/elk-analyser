// Updated: 2026-09-03 16:19:07 +0800
package com.demo.banking.controller;

import com.demo.banking.model.ApiResponse;
import com.demo.banking.model.TransferRequest;
import com.demo.banking.service.BankingJmsProducer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * Fund transfer controller.
 *
 * <h3>Endpoint</h3>
 * {@code POST /banking-app/api/transfer}
 *
 * <h3>Design</h3>
 * The transfer is processed asynchronously:
 * <ol>
 *   <li>Controller validates the request (basic null / amount check).</li>
 *   <li>{@link BankingJmsProducer#sendTransfer} puts a {@code TRANSFER} message
 *       on {@code jms/bankingQueue} — fire-and-forget.</li>
 *   <li>The controller immediately returns HTTP 202 Accepted.</li>
 *   <li>{@link com.demo.banking.mdb.BankingMDB} consumes the message and
 *       performs the database INSERT + UPDATE operations.</li>
 * </ol>
 *
 * This pattern ensures the web tier never blocks on DB I/O and remains
 * consistent with the message-driven architecture requirement.
 */
@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*")
public class TransferController {

    private static final Logger log = LoggerFactory.getLogger(TransferController.class);

    private final BankingJmsProducer jmsProducer;

    @Autowired
    public TransferController(BankingJmsProducer jmsProducer) {
        this.jmsProducer = jmsProducer;
    }

    /**
     * Initiates a fund transfer.
     *
     * @param request JSON body:
     *   {@code {fromAccount, toAccount, amount, description}}
     * @return 202 Accepted with confirmation message, or 400/500 on error
     */
    @PostMapping("/transfer")
    public ResponseEntity<ApiResponse<Void>> transfer(
            @RequestBody TransferRequest request) {

        // Basic validation
        if (request.getFromAccount() == null || request.getFromAccount().isBlank()) {
            return ResponseEntity.badRequest()
                    .body(ApiResponse.error("請輸入來源帳號"));
        }
        if (request.getToAccount() == null || request.getToAccount().isBlank()) {
            return ResponseEntity.badRequest()
                    .body(ApiResponse.error("請輸入目標帳號"));
        }
        if (request.getFromAccount().equals(request.getToAccount())) {
            return ResponseEntity.badRequest()
                    .body(ApiResponse.error("來源帳號與目標帳號不可相同"));
        }
        if (request.getAmount() == null || request.getAmount().signum() <= 0) {
            return ResponseEntity.badRequest()
                    .body(ApiResponse.error("轉帳金額必須大於零"));
        }

        log.info("Transfer request: from={} to={} amount={}",
                 request.getFromAccount(), request.getToAccount(), request.getAmount());

        try {
            jmsProducer.sendTransfer(
                    request.getFromAccount(),
                    request.getToAccount(),
                    request.getAmount(),
                    request.getDescription()
            );

            return ResponseEntity.status(HttpStatus.ACCEPTED)
                    .body(ApiResponse.ok("轉帳申請已送出，系統正在處理中，請稍後查看帳戶餘額"));

        } catch (Exception ex) {
            log.error("Transfer failed: {}", ex.getMessage(), ex);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                    .body(ApiResponse.error("轉帳處理發生錯誤，請稍後再試"));
        }
    }
}
