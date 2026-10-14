#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PROBE_WIFI_AP_SSID      "Mek-Wireless-Probe"
#define PROBE_WIFI_AP_PASS      "12345678"
#define PROBE_WIFI_AP_CHANNEL   6
#define PROBE_WIFI_AP_MAX_CONN  4

esp_err_t wifi_manager_init(void);
bool wifi_manager_is_connected(void);
void wifi_manager_get_ip_str(char *buf, size_t max_len);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_MANAGER_H */
