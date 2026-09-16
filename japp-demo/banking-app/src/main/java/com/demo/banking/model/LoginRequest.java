// Updated: 2026-09-02 16:20:51 +0800
package com.demo.banking.model;

/**
 * Login request payload from the browser.
 */
public class LoginRequest {

    private String username;
    private String password;

    public LoginRequest() {}

    public LoginRequest(String username, String password) {
        this.username = username;
        this.password = password;
    }

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }

    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
}
