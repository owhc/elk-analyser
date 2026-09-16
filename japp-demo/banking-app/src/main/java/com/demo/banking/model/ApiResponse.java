// Updated: 2026-09-02 16:20:51 +0800
package com.demo.banking.model;

import com.fasterxml.jackson.annotation.JsonInclude;

/**
 * Generic JSON response envelope used by all REST controllers.
 *
 * @param <T> the type of the payload data field
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class ApiResponse<T> {

    private boolean success;
    private String message;
    private T data;

    /** Convenience factory — successful response with data payload */
    public static <T> ApiResponse<T> ok(T data) {
        ApiResponse<T> r = new ApiResponse<>();
        r.success = true;
        r.data = data;
        return r;
    }

    /** Convenience factory — successful response with message only */
    public static <T> ApiResponse<T> ok(String message) {
        ApiResponse<T> r = new ApiResponse<>();
        r.success = true;
        r.message = message;
        return r;
    }

    /** Convenience factory — error response with message */
    public static <T> ApiResponse<T> error(String message) {
        ApiResponse<T> r = new ApiResponse<>();
        r.success = false;
        r.message = message;
        return r;
    }

    public boolean isSuccess() { return success; }
    public void setSuccess(boolean success) { this.success = success; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public T getData() { return data; }
    public void setData(T data) { this.data = data; }
}
