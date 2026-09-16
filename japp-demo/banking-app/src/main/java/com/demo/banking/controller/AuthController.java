// Updated: 2026-09-03 16:19:07 +0800
package com.demo.banking.controller;

import com.demo.banking.model.ApiResponse;
import com.demo.banking.model.LoginRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

/**
 * Authentication controller.
 * <p>
 * Provides mock login/logout endpoints for the banking demo.
 * Authentication is intentionally simplified (no security backend) —
 * the three hardcoded accounts demonstrate the login flow.
 * </p>
 *
 * <h3>Endpoints</h3>
 * <ul>
 *   <li>{@code POST /banking-app/api/login}  — accepts JSON {@code {username, password}}</li>
 *   <li>{@code POST /banking-app/api/logout} — stateless, always returns 200</li>
 * </ul>
 */
@RestController
@RequestMapping("/api")
@CrossOrigin(origins = "*")
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    /**
     * Mock user store: username → [password, accountId, ownerName].
     * Replace with a real UserService + DB query for production use.
     */
    private static final Map<String, String[]> USERS = new HashMap<>();

    static {
        USERS.put("user001", new String[]{"pass001", "ACC001", "王大明"});
        USERS.put("user002", new String[]{"pass002", "ACC002", "李小美"});
        USERS.put("user003", new String[]{"pass003", "ACC003", "張志偉"});
    }

    /**
     * Mock login endpoint.
     *
     * @param request JSON body with {@code username} and {@code password}
     * @return 200 with account info on success, 401 on failure
     */
    @PostMapping("/login")
    public ResponseEntity<ApiResponse<Map<String, String>>> login(
            @RequestBody LoginRequest request) {

        log.info("Login attempt for username={}", request.getUsername());

        String[] userInfo = USERS.get(request.getUsername());
        if (userInfo != null && userInfo[0].equals(request.getPassword())) {
            Map<String, String> data = new HashMap<>();
            data.put("accountId", userInfo[1]);
            data.put("ownerName", userInfo[2]);
            log.info("Login successful for username={} accountId={}", request.getUsername(), userInfo[1]);
            return ResponseEntity.ok(ApiResponse.ok(data));
        }

        log.warn("Login failed for username={}", request.getUsername());
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(ApiResponse.error("帳號或密碼錯誤，請重新輸入"));
    }

    /**
     * Stateless logout — the client simply discards the stored accountId.
     */
    @PostMapping("/logout")
    public ResponseEntity<ApiResponse<Void>> logout() {
        return ResponseEntity.ok(ApiResponse.ok("已成功登出"));
    }
}
