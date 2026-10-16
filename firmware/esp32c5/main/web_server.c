#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_http_server.h"
#include "cJSON.h"
#include "wifi_manager.h"
#include "web_server.h"

static const char *TAG = "web_server";
static httpd_handle_t s_server = NULL;

/* Embedded Web Page HTML */
static const char s_index_html[] =
"<!DOCTYPE html>"
"<html lang=\"zh-CN\">"
"<head>"
"<meta charset=\"UTF-8\">"
"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
"<title>Wireless Probe 配网中心</title>"
"<style>"
"* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }"
"body { background: #0f172a; color: #f8fafc; padding: 20px; display: flex; justify-content: center; }"
".card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; width: 100%; max-width: 480px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }"
"h1 { font-size: 20px; font-weight: 700; color: #38bdf8; margin-bottom: 8px; }"
"p.subtitle { font-size: 13px; color: #94a3b8; margin-bottom: 20px; }"
".section { background: #0f172a; border-radius: 8px; padding: 14px; margin-bottom: 16px; border: 1px solid #1e293b; }"
".section-title { font-size: 13px; font-weight: 600; color: #cbd5e1; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; }"
".status-row { display: flex; justify-content: space-between; font-size: 14px; margin-bottom: 8px; }"
".status-row:last-child { margin-bottom: 0; }"
".label { color: #94a3b8; }"
".val { font-weight: 500; }"
".badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }"
".badge-online { background: #065f46; color: #34d399; }"
".badge-connecting { background: #854d0e; color: #facc15; }"
".badge-connected { background: #1e3a8a; color: #60a5fa; }"
".badge-offline { background: #7f1d1d; color: #f87171; }"
"label { display: block; font-size: 13px; color: #cbd5e1; margin-bottom: 6px; font-weight: 500; }"
"input, select { width: 100%; padding: 10px 12px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; color: #f8fafc; font-size: 14px; margin-bottom: 14px; outline: none; }"
"input:focus, select:focus { border-color: #38bdf8; }"
"button { width: 100%; padding: 10px 16px; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; border: none; transition: all 0.2s; }"
".btn-primary { background: #0284c7; color: white; margin-top: 4px; }"
".btn-primary:hover { background: #0369a1; }"
".btn-secondary { background: #334155; color: #cbd5e1; margin-bottom: 14px; }"
".btn-secondary:hover { background: #475569; }"
".btn-danger { background: #991b1b; color: #fecaca; margin-top: 10px; }"
".btn-danger:hover { background: #7f1d1d; }"
".tip { font-size: 12px; color: #64748b; line-height: 1.5; margin-top: 16px; }"
".alert { padding: 10px 12px; border-radius: 6px; font-size: 13px; margin-bottom: 14px; display: none; }"
".alert-success { background: #064e3b; color: #6ee7b7; border: 1px solid #059669; }"
".alert-error { background: #7f1d1d; color: #fca5a5; border: 1px solid #dc2626; }"
"</style>"
"</head>"
"<body>"
"<div class=\"card\">"
"  <h1>Wireless-Probe 配网中心</h1>"
"  <p class=\"subtitle\">Wi-Fi 6 调试器网络配置与状态监控</p>"
"  <div id=\"alertBox\" class=\"alert\"></div>"
"  <div class=\"section\">"
"    <div class=\"section-title\">网络状态 (Network Status)</div>"
"    <div class=\"status-row\">"
"      <span class=\"label\">备用热点 (AP):</span>"
"      <span class=\"val\"><span class=\"badge badge-online\">Mek-Wireless-Probe</span> (192.168.4.1)</span>"
"    </div>"
"    <div class=\"status-row\">"
"      <span class=\"label\">局域网状态 (STA):</span>"
"      <span class=\"val\" id=\"staBadge\"><span class=\"badge badge-offline\">检测中...</span></span>"
"    </div>"
"    <div class=\"status-row\" id=\"staIpRow\" style=\"display:none;\">"
"      <span class=\"label\">局域网 IP:</span>"
"      <span class=\"val\" id=\"staIpVal\" style=\"color:#38bdf8; font-family:monospace;\">-</span>"
"    </div>"
"    <div class=\"status-row\" id=\"staRssiRow\" style=\"display:none;\">"
"      <span class=\"label\">信号强度:</span>"
"      <span class=\"val\" id=\"staRssiVal\">-</span>"
"    </div>"
"  </div>"
"  <div class=\"section\">"
"    <div class=\"section-title\">连接局域网 Wi-Fi (Join Wi-Fi)</div>"
"    <label>周边 Wi-Fi 扫描</label>"
"    <select id=\"wifiSelect\" onchange=\"onSelectWifi(this.value)\">"
"      <option value=\"\">-- 点击下方按钮扫描周边 Wi-Fi --</option>"
"    </select>"
"    <button type=\"button\" class=\"btn-secondary\" onclick=\"scanWifi()\" id=\"btnScan\">扫描周边 Wi-Fi</button>"
"    <label>Wi-Fi 名称 (SSID)</label>"
"    <input type=\"text\" id=\"ssidInput\" placeholder=\"输入或在列表选择 Wi-Fi\" />"
"    <label>Wi-Fi 密码 (Password)</label>"
"    <input type=\"password\" id=\"passInput\" placeholder=\"若无需密码可留空\" />"
"    <button type=\"button\" class=\"btn-primary\" onclick=\"saveAndConnect()\" id=\"btnConnect\">保存配置并连接</button>"
"  </div>"
"  <div class=\"section\">"
"    <div class=\"section-title\">恢复出厂配置</div>"
"    <p style=\"font-size: 12px; color: #94a3b8;\">清除探针保存的 Wi-Fi 账号密码，回退到纯热点模式。</p>"
"    <button type=\"button\" class=\"btn-danger\" onclick=\"resetConfig()\">清除已存 Wi-Fi 并重置</button>"
"  </div>"
"  <p class=\"tip\">注意：配置保存后，探针热点 <b>Mek-Wireless-Probe (192.168.4.1)</b> 将始终保持在线，您随时可以再次连入修改网络。</p>"
"</div>"
"<script>"
"function showAlert(msg, isSuccess) {"
"  var el = document.getElementById('alertBox');"
"  el.className = 'alert ' + (isSuccess ? 'alert-success' : 'alert-error');"
"  el.innerText = msg;"
"  el.style.display = 'block';"
"  setTimeout(function() { el.style.display = 'none'; }, 6000);"
"}"
"function updateStatus() {"
"  fetch('/api/status').then(r => r.json()).then(d => {"
"    var b = document.getElementById('staBadge');"
"    var ipRow = document.getElementById('staIpRow');"
"    var rssiRow = document.getElementById('staRssiRow');"
"    if (d.sta_status === 'connected') {"
"      b.innerHTML = '<span class=\"badge badge-connected\">已连接: ' + d.sta_ssid + '</span>';"
"      ipRow.style.display = 'flex';"
"      document.getElementById('staIpVal').innerText = d.sta_ip;"
"      rssiRow.style.display = 'flex';"
"      document.getElementById('staRssiVal').innerText = d.sta_rssi + ' dBm';"
"    } else if (d.sta_status === 'connecting') {"
"      b.innerHTML = '<span class=\"badge badge-connecting\">正在连接: ' + d.sta_ssid + '...</span>';"
"      ipRow.style.display = 'none';"
"      rssiRow.style.display = 'none';"
"    } else if (d.sta_status === 'failed') {"
"      b.innerHTML = '<span class=\"badge badge-offline\">连接失败 (' + d.sta_ssid + ')</span>';"
"      ipRow.style.display = 'none';"
"      rssiRow.style.display = 'none';"
"    } else {"
"      b.innerHTML = '<span class=\"badge badge-offline\">未配置</span>';"
"      ipRow.style.display = 'none';"
"      rssiRow.style.display = 'none';"
"    }"
"  }).catch(function(e) {});"
"}"
"function scanWifi() {"
"  var btn = document.getElementById('btnScan');"
"  btn.disabled = true; btn.innerText = '正在扫描周围网络...';"
"  fetch('/api/scan').then(r => r.json()).then(list => {"
"    var sel = document.getElementById('wifiSelect');"
"    sel.innerHTML = '<option value=\"\">-- 找到 ' + list.length + ' 个网络，请选择 --</option>';"
"    list.forEach(function(item) {"
"      var opt = document.createElement('option');"
"      opt.value = item.ssid;"
"      opt.text = item.ssid + ' (' + item.rssi + ' dBm)'; "
"      sel.appendChild(opt);"
"    });"
"    btn.disabled = false; btn.innerText = '刷新周边 Wi-Fi 列表';"
"  }).catch(function() {"
"    btn.disabled = false; btn.innerText = '扫描失败，点击重试';"
"  });"
"}"
"function onSelectWifi(val) {"
"  if (val) document.getElementById('ssidInput').value = val;"
"}"
"function saveAndConnect() {"
"  var ssid = document.getElementById('ssidInput').value.trim();"
"  var pass = document.getElementById('passInput').value;"
"  if (!ssid) { showAlert('请输入或选择 Wi-Fi 名称', false); return; }"
"  var btn = document.getElementById('btnConnect');"
"  btn.disabled = true; btn.innerText = '正在发送并尝试连接...';"
"  fetch('/api/connect', {"
"    method: 'POST',"
"    headers: { 'Content-Type': 'application/json' },"
"    body: JSON.stringify({ ssid: ssid, password: pass })"
"  }).then(r => r.json()).then(res => {"
"    btn.disabled = false; btn.innerText = '保存配置并连接';"
"    showAlert('已提交连接请求！探针正在尝试接入 ' + ssid + '，请关注上方状态。', true);"
"    updateStatus();"
"  }).catch(function() {"
"    btn.disabled = false; btn.innerText = '保存配置并连接';"
"    showAlert('请求失败，请检查网络连接', false);"
"  });"
"}"
"function resetConfig() {"
"  if (!confirm('确定要清除已保存的 Wi-Fi 密码并恢复出厂热点模式吗？')) return;"
"  fetch('/api/reset', { method: 'POST' }).then(r => r.json()).then(res => {"
"    showAlert('Wi-Fi 配置已成功重置，探针已回退为纯 AP 模式。', true);"
"    updateStatus();"
"  });"
"}"
"setInterval(updateStatus, 3000);"
"updateStatus();"
"</script>"
"</body>"
"</html>";

/* Handler: GET / (Serve Web Page) */
static esp_err_t index_get_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    return httpd_resp_send(req, s_index_html, HTTPD_RESP_USE_STRLEN);
}

/* Handler: GET /api/status (JSON status) */
static esp_err_t api_status_get_handler(httpd_req_t *req) {
    wifi_manager_status_t st;
    wifi_manager_get_status(&st);

    const char *sta_status_str = "disabled";
    if (st.sta_status == STA_STATUS_CONNECTING) sta_status_str = "connecting";
    else if (st.sta_status == STA_STATUS_CONNECTED) sta_status_str = "connected";
    else if (st.sta_status == STA_STATUS_FAILED) sta_status_str = "failed";

    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "ap_ssid", st.ap_ssid);
    cJSON_AddStringToObject(root, "ap_ip", st.ap_ip);
    cJSON_AddBoolToObject(root, "ap_active", st.ap_active);
    cJSON_AddStringToObject(root, "sta_status", sta_status_str);
    cJSON_AddStringToObject(root, "sta_ssid", st.sta_ssid);
    cJSON_AddStringToObject(root, "sta_ip", st.sta_ip);
    cJSON_AddNumberToObject(root, "sta_rssi", st.sta_rssi);

    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t ret = httpd_resp_send(req, json_str, HTTPD_RESP_USE_STRLEN);
    free(json_str);
    return ret;
}

/* Handler: GET /api/scan (Scan nearby Wi-Fi networks) */
static esp_err_t api_scan_get_handler(httpd_req_t *req) {
    wifi_scan_item_t scan_list[WIFI_SCAN_MAX_APS];
    int count = wifi_manager_scan_networks(scan_list, WIFI_SCAN_MAX_APS);

    cJSON *root = cJSON_CreateArray();
    for (int i = 0; i < count; i++) {
        cJSON *item = cJSON_CreateObject();
        cJSON_AddStringToObject(item, "ssid", scan_list[i].ssid);
        cJSON_AddNumberToObject(item, "rssi", scan_list[i].rssi);
        cJSON_AddNumberToObject(item, "auth", scan_list[i].authmode);
        cJSON_AddItemToArray(root, item);
    }

    char *json_str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    esp_err_t ret = httpd_resp_send(req, json_str, HTTPD_RESP_USE_STRLEN);
    free(json_str);
    return ret;
}

/* Handler: POST /api/connect (Configure & connect to target Wi-Fi) */
static esp_err_t api_connect_post_handler(httpd_req_t *req) {
    char buf[256];
    int ret_len = httpd_req_recv(req, buf, sizeof(buf) - 1);
    if (ret_len <= 0) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    buf[ret_len] = '\0';

    cJSON *root = cJSON_Parse(buf);
    if (!root) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid JSON");
        return ESP_FAIL;
    }

    cJSON *j_ssid = cJSON_GetObjectItem(root, "ssid");
    cJSON *j_pass = cJSON_GetObjectItem(root, "password");

    if (!cJSON_IsString(j_ssid) || (j_ssid->valuestring == NULL)) {
        cJSON_Delete(root);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "SSID required");
        return ESP_FAIL;
    }

    const char *ssid = j_ssid->valuestring;
    const char *pass = (cJSON_IsString(j_pass) && j_pass->valuestring) ? j_pass->valuestring : "";

    ESP_LOGI(TAG, "Web Provisioning received: Target SSID='%s'", ssid);
    wifi_manager_save_and_connect_sta(ssid, pass);

    cJSON_Delete(root);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, "{\"status\":\"ok\"}", HTTPD_RESP_USE_STRLEN);
}

/* Handler: POST /api/reset (Forget Wi-Fi & reset to AP) */
static esp_err_t api_reset_post_handler(httpd_req_t *req) {
    ESP_LOGI(TAG, "Web Provisioning reset requested. Clearing STA credentials...");
    wifi_manager_clear_sta();

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    return httpd_resp_send(req, "{\"status\":\"ok\"}", HTTPD_RESP_USE_STRLEN);
}

esp_err_t web_server_init(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    config.ctrl_port = 32768;
    config.max_uri_handlers = 8;
    config.stack_size = 8192;

    ESP_LOGI(TAG, "Starting HTTP Web Provisioning Server on port %d...", config.server_port);
    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start HTTP server: %s", esp_err_to_name(ret));
        return ret;
    }

    httpd_uri_t uri_get_index = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = index_get_handler,
        .user_ctx = NULL
    };
    httpd_register_uri_handler(s_server, &uri_get_index);

    httpd_uri_t uri_get_status = {
        .uri = "/api/status",
        .method = HTTP_GET,
        .handler = api_status_get_handler,
        .user_ctx = NULL
    };
    httpd_register_uri_handler(s_server, &uri_get_status);

    httpd_uri_t uri_get_scan = {
        .uri = "/api/scan",
        .method = HTTP_GET,
        .handler = api_scan_get_handler,
        .user_ctx = NULL
    };
    httpd_register_uri_handler(s_server, &uri_get_scan);

    httpd_uri_t uri_post_connect = {
        .uri = "/api/connect",
        .method = HTTP_POST,
        .handler = api_connect_post_handler,
        .user_ctx = NULL
    };
    httpd_register_uri_handler(s_server, &uri_post_connect);

    httpd_uri_t uri_post_reset = {
        .uri = "/api/reset",
        .method = HTTP_POST,
        .handler = api_reset_post_handler,
        .user_ctx = NULL
    };
    httpd_register_uri_handler(s_server, &uri_post_reset);

    ESP_LOGI(TAG, "Web Provisioning Server started! Access http://192.168.4.1/ in browser.");
    return ESP_OK;
}

void web_server_stop(void) {
    if (s_server) {
        httpd_stop(s_server);
        s_server = NULL;
    }
}
