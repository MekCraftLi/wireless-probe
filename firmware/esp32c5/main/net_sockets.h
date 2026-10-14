#ifndef NET_SOCKETS_H
#define NET_SOCKETS_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
#include "ufmf_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

#define PORT_DAP_CTRL       3240
#define PORT_DAP_BULK       3241
#define PORT_DAP_TRACE      3242
#define PORT_DAP_UART       3243

typedef void (*net_rx_callback_t)(ufmf_channel_t ch, const uint8_t *data, size_t len);

esp_err_t net_sockets_init(void);
void net_sockets_set_rx_callback(net_rx_callback_t cb);
int net_sockets_send(ufmf_channel_t ch, const uint8_t *data, size_t len);
bool net_sockets_client_connected(ufmf_channel_t ch);

#ifdef __cplusplus
}
#endif

#endif /* NET_SOCKETS_H */
