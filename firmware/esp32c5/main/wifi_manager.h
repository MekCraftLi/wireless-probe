#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_wifi_types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROBE_WIFI_AP_SSID          "Mek-Wireless-Probe"
#define PROBE_WIFI_AP_PASS          "12345678"
#define PROBE_WIFI_AP_CHANNEL       6
#define PROBE_WIFI_AP_MAX_CONN      4

#define WIFI_SCAN_MAX_APS           20

typedef enum {
    STA_STATUS_DISABLED = 0,
    STA_STATUS_CONNECTING,
    STA_STATUS_CONNECTED,
    STA_STATUS_FAILED
} sta_status_t;

typedef struct {
    char ssid[33];
    int8_t rssi;
    wifi_auth_mode_t authmode;
} wifi_scan_item_t;

typedef struct {
    char ap_ssid[33];
    char ap_ip[16];
    bool ap_active;
    sta_status_t sta_status;
    char sta_ssid[33];
    char sta_ip[16];
    int8_t sta_rssi;
} wifi_manager_status_t;

/* Core Wi-Fi management APIs */
esp_err_t wifi_manager_init(void);
bool wifi_manager_is_connected(void);
void wifi_manager_get_ip_str(char *buf, size_t max_len);

/* STA configuration & NVS storage */
esp_err_t wifi_manager_save_and_connect_sta(const char *ssid, const char *pass);
esp_err_t wifi_manager_clear_sta(void);
void wifi_manager_get_status(wifi_manager_status_t *out_status);

/* Wi-Fi network scanning */
int wifi_manager_scan_networks(wifi_scan_item_t *out_list, int max_items);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_MANAGER_H */
