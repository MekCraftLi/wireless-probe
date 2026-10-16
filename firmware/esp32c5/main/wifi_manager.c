#include <string.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "wifi_manager.h"

static const char *TAG = "wifi_manager";

#define NVS_NAMESPACE       "probe_wifi"
#define NVS_KEY_STA_SSID    "sta_ssid"
#define NVS_KEY_STA_PASS    "sta_pass"
#define STA_MAX_RETRIES     5

static esp_netif_t *s_ap_netif = NULL;
static esp_netif_t *s_sta_netif = NULL;

static bool s_ap_active = false;
static char s_ap_ip_str[16] = "192.168.4.1";

static sta_status_t s_sta_status = STA_STATUS_DISABLED;
static char s_sta_ssid[33] = {0};
static char s_sta_pass[65] = {0};
static char s_sta_ip_str[16] = {0};
static int8_t s_sta_rssi = 0;
static int s_sta_retry_count = 0;

/* Load saved credentials from NVS Flash */
static bool load_sta_from_nvs(char *ssid_out, char *pass_out) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) return false;

    size_t ssid_len = 33;
    size_t pass_len = 65;
    err = nvs_get_str(handle, NVS_KEY_STA_SSID, ssid_out, &ssid_len);
    if (err == ESP_OK) {
        nvs_get_str(handle, NVS_KEY_STA_PASS, pass_out, &pass_len);
    }
    nvs_close(handle);
    return (err == ESP_OK && strlen(ssid_out) > 0);
}

/* Save credentials to NVS Flash */
static esp_err_t save_sta_to_nvs(const char *ssid, const char *pass) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;

    err = nvs_set_str(handle, NVS_KEY_STA_SSID, ssid);
    if (err == ESP_OK) {
        err = nvs_set_str(handle, NVS_KEY_STA_PASS, pass ? pass : "");
    }
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}

/* Erase credentials from NVS Flash */
static esp_err_t erase_sta_from_nvs(void) {
    nvs_handle_t handle;
    esp_err_t err = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) return err;

    nvs_erase_key(handle, NVS_KEY_STA_SSID);
    nvs_erase_key(handle, NVS_KEY_STA_PASS);
    err = nvs_commit(handle);
    nvs_close(handle);
    return err;
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_AP_STACONNECTED: {
                wifi_event_ap_staconnected_t *event = (wifi_event_ap_staconnected_t *)event_data;
                ESP_LOGI(TAG, "AP Client connected: AID=%d", event->aid);
                break;
            }
            case WIFI_EVENT_AP_STADISCONNECTED: {
                wifi_event_ap_stadisconnected_t *event = (wifi_event_ap_stadisconnected_t *)event_data;
                ESP_LOGI(TAG, "AP Client disconnected: AID=%d", event->aid);
                break;
            }
            case WIFI_EVENT_STA_START: {
                ESP_LOGI(TAG, "Wi-Fi STA interface started");
                if (strlen(s_sta_ssid) > 0) {
                    s_sta_status = STA_STATUS_CONNECTING;
                    s_sta_retry_count = 0;
                    esp_wifi_connect();
                }
                break;
            }
            case WIFI_EVENT_STA_CONNECTED: {
                ESP_LOGI(TAG, "Connected to Target AP '%s', waiting for DHCP IP...", s_sta_ssid);
                wifi_ap_record_t ap_info;
                if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
                    s_sta_rssi = ap_info.rssi;
                }
                break;
            }
            case WIFI_EVENT_STA_DISCONNECTED: {
                wifi_event_sta_disconnected_t *event = (wifi_event_sta_disconnected_t *)event_data;
                ESP_LOGW(TAG, "Disconnected from Target AP (reason: %d)", event->reason);
                s_sta_ip_str[0] = '\0';
                if (s_sta_retry_count < STA_MAX_RETRIES && strlen(s_sta_ssid) > 0) {
                    s_sta_retry_count++;
                    s_sta_status = STA_STATUS_CONNECTING;
                    ESP_LOGI(TAG, "Reconnecting to '%s' (attempt %d/%d)...",
                             s_sta_ssid, s_sta_retry_count, STA_MAX_RETRIES);
                    esp_wifi_connect();
                } else {
                    s_sta_status = STA_STATUS_FAILED;
                    ESP_LOGE(TAG, "Failed to connect to '%s' after %d attempts. Hotspot AP '%s' remains active at 192.168.4.1",
                             s_sta_ssid, STA_MAX_RETRIES, PROBE_WIFI_AP_SSID);
                }
                break;
            }
            default:
                break;
        }
    } else if (event_base == IP_EVENT) {
        if (event_id == IP_EVENT_STA_GOT_IP) {
            ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
            esp_ip4addr_ntoa(&event->ip_info.ip, s_sta_ip_str, sizeof(s_sta_ip_str));
            s_sta_status = STA_STATUS_CONNECTED;
            s_sta_retry_count = 0;
            ESP_LOGI(TAG, "================================================================");
            ESP_LOGI(TAG, ">>> STA CONNECTED! Target Network: '%s' <<<", s_sta_ssid);
            ESP_LOGI(TAG, ">>> PROBE LAN IP: %s (Access probe at http://%s) <<<", s_sta_ip_str, s_sta_ip_str);
            ESP_LOGI(TAG, ">>> HOTSPOT AP REMAINS ACTIVE: http://192.168.4.1 <<<");
            ESP_LOGI(TAG, "================================================================");
        }
    }
}

esp_err_t wifi_manager_init(void) {
    esp_err_t ret = esp_netif_init();
    if (ret != ESP_OK) return ret;

    ret = esp_event_loop_create_default();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) return ret;

    /* 1. Create AP & STA network interfaces */
    s_ap_netif = esp_netif_create_default_wifi_ap();
    s_sta_netif = esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK) return ret;

    /* 2. Register event handlers */
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                        &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                        &wifi_event_handler, NULL, NULL);

    /* 3. Configure AP parameters */
    wifi_config_t ap_config = {
        .ap = {
            .ssid = PROBE_WIFI_AP_SSID,
            .ssid_len = strlen(PROBE_WIFI_AP_SSID),
            .channel = PROBE_WIFI_AP_CHANNEL,
            .password = PROBE_WIFI_AP_PASS,
            .max_connection = PROBE_WIFI_AP_MAX_CONN,
            .authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg = { .required = false },
        },
    };

    /* 4. Check NVS for stored STA credentials */
    bool has_sta = load_sta_from_nvs(s_sta_ssid, s_sta_pass);

    wifi_config_t sta_config;
    memset(&sta_config, 0, sizeof(sta_config));
    if (has_sta) {
        strncpy((char *)sta_config.sta.ssid, s_sta_ssid, sizeof(sta_config.sta.ssid));
        strncpy((char *)sta_config.sta.password, s_sta_pass, sizeof(sta_config.sta.password));
        sta_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
        sta_config.sta.pmf_cfg.capable = true;
        sta_config.sta.pmf_cfg.required = false;
        s_sta_status = STA_STATUS_CONNECTING;
        ESP_LOGI(TAG, "Found stored Wi-Fi config in NVS: SSID='%s'", s_sta_ssid);
    } else {
        s_sta_status = STA_STATUS_DISABLED;
        ESP_LOGI(TAG, "No stored Wi-Fi config in NVS. Ready for Web provisioning.");
    }

    /* 5. Set AP+STA dual mode (both interfaces active) */
    ret = esp_wifi_set_mode(WIFI_MODE_APSTA);
    if (ret != ESP_OK) return ret;

    ret = esp_wifi_set_config(WIFI_IF_AP, &ap_config);
    if (ret != ESP_OK) return ret;

    if (has_sta) {
        ret = esp_wifi_set_config(WIFI_IF_STA, &sta_config);
        if (ret != ESP_OK) return ret;
    }

    /* Enable 802.11ax (Wi-Fi 6) */
    uint8_t protocol = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AX;
    esp_wifi_set_protocol(WIFI_IF_AP, protocol);
    esp_wifi_set_protocol(WIFI_IF_STA, protocol);

    ret = esp_wifi_start();
    if (ret != ESP_OK) return ret;

    s_ap_active = true;
    ESP_LOGI(TAG, "Wi-Fi APSTA Dual-Mode started.");
    ESP_LOGI(TAG, "  -> Hotspot AP: '%s' (IP: %s, Ch: %d)",
             PROBE_WIFI_AP_SSID, s_ap_ip_str, PROBE_WIFI_AP_CHANNEL);

    return ESP_OK;
}

bool wifi_manager_is_connected(void) {
    return (s_sta_status == STA_STATUS_CONNECTED || s_ap_active);
}

void wifi_manager_get_ip_str(char *buf, size_t max_len) {
    if (s_sta_status == STA_STATUS_CONNECTED && strlen(s_sta_ip_str) > 0) {
        strncpy(buf, s_sta_ip_str, max_len);
    } else {
        strncpy(buf, s_ap_ip_str, max_len);
    }
}

esp_err_t wifi_manager_save_and_connect_sta(const char *ssid, const char *pass) {
    if (!ssid || strlen(ssid) == 0) return ESP_ERR_INVALID_ARG;

    save_sta_to_nvs(ssid, pass);

    strncpy(s_sta_ssid, ssid, sizeof(s_sta_ssid) - 1);
    s_sta_ssid[sizeof(s_sta_ssid) - 1] = '\0';
    if (pass) {
        strncpy(s_sta_pass, pass, sizeof(s_sta_pass) - 1);
        s_sta_pass[sizeof(s_sta_pass) - 1] = '\0';
    } else {
        s_sta_pass[0] = '\0';
    }

    wifi_config_t sta_config;
    memset(&sta_config, 0, sizeof(sta_config));
    strncpy((char *)sta_config.sta.ssid, s_sta_ssid, sizeof(sta_config.sta.ssid));
    strncpy((char *)sta_config.sta.password, s_sta_pass, sizeof(sta_config.sta.password));
    sta_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    sta_config.sta.pmf_cfg.capable = true;
    sta_config.sta.pmf_cfg.required = false;

    esp_wifi_disconnect();
    esp_wifi_set_config(WIFI_IF_STA, &sta_config);

    s_sta_retry_count = 0;
    s_sta_status = STA_STATUS_CONNECTING;
    s_sta_ip_str[0] = '\0';

    ESP_LOGI(TAG, "Connecting to new Target Wi-Fi: '%s'...", s_sta_ssid);
    return esp_wifi_connect();
}

esp_err_t wifi_manager_clear_sta(void) {
    erase_sta_from_nvs();
    esp_wifi_disconnect();
    s_sta_status = STA_STATUS_DISABLED;
    s_sta_ssid[0] = '\0';
    s_sta_pass[0] = '\0';
    s_sta_ip_str[0] = '\0';
    s_sta_rssi = 0;
    ESP_LOGI(TAG, "STA credentials cleared. Reverted to pure AP mode.");
    return ESP_OK;
}

void wifi_manager_get_status(wifi_manager_status_t *out_status) {
    if (!out_status) return;
    strncpy(out_status->ap_ssid, PROBE_WIFI_AP_SSID, sizeof(out_status->ap_ssid));
    strncpy(out_status->ap_ip, s_ap_ip_str, sizeof(out_status->ap_ip));
    out_status->ap_active = s_ap_active;

    out_status->sta_status = s_sta_status;
    strncpy(out_status->sta_ssid, s_sta_ssid, sizeof(out_status->sta_ssid));
    strncpy(out_status->sta_ip, s_sta_ip_str, sizeof(out_status->sta_ip));
    out_status->sta_rssi = s_sta_rssi;
}

int wifi_manager_scan_networks(wifi_scan_item_t *out_list, int max_items) {
    if (!out_list || max_items <= 0) return 0;

    wifi_scan_config_t scan_config = {
        .ssid = NULL,
        .bssid = NULL,
        .channel = 0,
        .show_hidden = false,
        .scan_type = WIFI_SCAN_TYPE_ACTIVE,
        .scan_time = {
            .active = {
                .min = 20,
                .max = 50,
            },
        },
    };

    ESP_LOGI(TAG, "Starting synchronous Wi-Fi scan...");
    esp_err_t err = esp_wifi_scan_start(&scan_config, true);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_scan_start failed: %d", err);
        return 0;
    }

    uint16_t ap_count = 0;
    esp_wifi_scan_get_ap_num(&ap_count);
    if (ap_count == 0) return 0;

    uint16_t fetch_count = (ap_count > max_items) ? max_items : ap_count;
    wifi_ap_record_t *records = malloc(sizeof(wifi_ap_record_t) * fetch_count);
    if (!records) return 0;

    esp_wifi_scan_get_ap_records(&fetch_count, records);

    int count = 0;
    for (int i = 0; i < fetch_count; i++) {
        if (strlen((char *)records[i].ssid) > 0) {
            strncpy(out_list[count].ssid, (char *)records[i].ssid, 32);
            out_list[count].ssid[32] = '\0';
            out_list[count].rssi = records[i].rssi;
            out_list[count].authmode = records[i].authmode;
            count++;
        }
    }

    free(records);
    ESP_LOGI(TAG, "Wi-Fi scan completed. Found %d valid APs.", count);
    return count;
}
