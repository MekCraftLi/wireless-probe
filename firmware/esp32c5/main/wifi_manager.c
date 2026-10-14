#include <string.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "nvs_flash.h"
#include "wifi_manager.h"

static const char *TAG = "wifi_manager";
static esp_netif_t *s_ap_netif = NULL;
static bool s_is_active = false;
static char s_ip_str[32] = "192.168.4.1";

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_AP_STACONNECTED: {
                wifi_event_ap_staconnected_t *event = (wifi_event_ap_staconnected_t *)event_data;
                ESP_LOGI(TAG, "Station connected: AID=%d", event->aid);
                break;
            }
            case WIFI_EVENT_AP_STADISCONNECTED: {
                wifi_event_ap_stadisconnected_t *event = (wifi_event_ap_stadisconnected_t *)event_data;
                ESP_LOGI(TAG, "Station disconnected: AID=%d", event->aid);
                break;
            }
            default:
                break;
        }
    }
}

esp_err_t wifi_manager_init(void) {
    esp_err_t ret = esp_netif_init();
    if (ret != ESP_OK) return ret;

    ret = esp_event_loop_create_default();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) return ret;

    s_ap_netif = esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ret = esp_wifi_init(&cfg);
    if (ret != ESP_OK) return ret;

    ret = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                              &wifi_event_handler, NULL, NULL);
    if (ret != ESP_OK) return ret;

    wifi_config_t wifi_config = {
        .ap = {
            .ssid = PROBE_WIFI_AP_SSID,
            .ssid_len = strlen(PROBE_WIFI_AP_SSID),
            .channel = PROBE_WIFI_AP_CHANNEL,
            .password = PROBE_WIFI_AP_PASS,
            .max_connection = PROBE_WIFI_AP_MAX_CONN,
            .authmode = WIFI_AUTH_WPA2_PSK,
            .pmf_cfg = {
                .required = false,
            },
        },
    };

    ret = esp_wifi_set_mode(WIFI_MODE_AP);
    if (ret != ESP_OK) return ret;

    ret = esp_wifi_set_config(WIFI_IF_AP, &wifi_config);
    if (ret != ESP_OK) return ret;

    /* Enable 802.11ax (Wi-Fi 6) */
    uint8_t protocol = WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_11AX;
    esp_wifi_set_protocol(WIFI_IF_AP, protocol);

    ret = esp_wifi_start();
    if (ret != ESP_OK) return ret;

    s_is_active = true;
    ESP_LOGI(TAG, "Wi-Fi 6 AP started. SSID: '%s', Pass: '%s', Channel: %d, IP: %s",
             PROBE_WIFI_AP_SSID, PROBE_WIFI_AP_PASS, PROBE_WIFI_AP_CHANNEL, s_ip_str);
    return ESP_OK;
}

bool wifi_manager_is_connected(void) {
    return s_is_active;
}

void wifi_manager_get_ip_str(char *buf, size_t max_len) {
    strncpy(buf, s_ip_str, max_len);
}
