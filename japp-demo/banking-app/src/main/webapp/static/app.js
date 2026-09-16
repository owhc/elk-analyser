// Updated: 2026-09-03 16:23:26 +0800
/**
 * app.js — Shared utility functions for the Banking Demo front-end.
 *
 * All three pages (login, dashboard, transfer) include this script.
 * It provides:
 *   - API base path resolution (handles Liberty context root "/banking-app")
 *   - Typed fetch wrappers (apiGet, apiPost)
 *   - Authentication guard (requireAuth)
 *   - Formatting helpers (formatAmount, formatDateTime)
 */

'use strict';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/**
 * The API base path.
 * Liberty deploys the WAR as banking-app.war with context root "/banking-app".
 * Supports calling with "/api/..." or just "/..." paths seamlessly.
 */
const API_BASE = '/banking-app';

function resolveApiPath(path) {
    let cleanPath = path.startsWith('/') ? path : '/' + path;
    if (!cleanPath.startsWith('/api')) {
        cleanPath = '/api' + cleanPath;
    }
    return API_BASE + cleanPath;
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

/**
 * Performs a GET request to the given API path.
 *
 * @param {string} path  — e.g. "/api/accounts/ACC001" or "/accounts/ACC001"
 * @returns {Promise<any>} parsed JSON response body
 * @throws {Error} on non-2xx HTTP status or network failure
 */
async function apiGet(path) {
    const fullUrl = resolveApiPath(path);
    const resp = await fetch(fullUrl, {
        method: 'GET',
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin'
    });
    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.message || `HTTP ${resp.status}`);
    }
    return resp.json();
}

/**
 * Performs a POST request with a JSON body.
 *
 * @param {string} path    — e.g. "/api/login" or "/login"
 * @param {object} payload — object that will be JSON-serialised as the body
 * @returns {Promise<any>} parsed JSON response body
 * @throws {Error} on network failure; non-2xx responses are returned as-is
 *                 (controllers may return 4xx with meaningful JSON bodies)
 */
async function apiPost(path, payload) {
    const fullUrl = resolveApiPath(path);
    const resp = await fetch(fullUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        credentials: 'same-origin',
        body: JSON.stringify(payload)
    });
    // Return parsed body even for 4xx responses (e.g. 401 login failure)
    return resp.json();
}

// ---------------------------------------------------------------------------
// Authentication guard
// ---------------------------------------------------------------------------

/**
 * Redirects to the login page if the user is not authenticated.
 * Call at the top of any protected page's script block.
 */
function requireAuth() {
    const accountId = sessionStorage.getItem('accountId');
    if (!accountId) {
        window.location.replace('login.html');
    }
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

/**
 * Formats a numeric amount as a locale-aware string with 2 decimal places.
 *
 * @param {number|string} amount
 * @returns {string} e.g. "1,234.56"
 */
function formatAmount(amount) {
    const num = parseFloat(amount);
    if (isNaN(num)) return '0.00';
    return num.toLocaleString('zh-TW', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

/**
 * Formats a date-time string or Date object for display.
 * Handles ISO-8601 strings returned by the MDB (LocalDateTime.toString()).
 *
 * @param {string|Date} value
 * @returns {string} e.g. "2026-09-02 14:30:00"
 */
function formatDateTime(value) {
    if (!value) return '—';
    try {
        // LocalDateTime.toString() returns "2026-09-02T14:30:00"
        // Replace 'T' separator and trim sub-seconds
        const normalised = String(value).replace('T', ' ').substring(0, 19);
        return normalised;
    } catch (_) {
        return String(value);
    }
}
