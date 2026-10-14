#ifndef QSPI_SLAVE_GDMA_H
#define QSPI_SLAVE_GDMA_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * User-specified GPIO pin mapping:
 * - D1: GPIO 7 moved to GPIO 23 (avoids Strapping 7)
 * - REQ: GPIO 11 moved to GPIO 13 (avoids UART0 TX 11)
 */
#define PIN_QSPI_SCLK       4
#define PIN_QSPI_CS         5
#define PIN_QSPI_D0         6
#define PIN_QSPI_D1         23
#define PIN_QSPI_D2         8
#define PIN_QSPI_D3         9

#define PIN_FLOW_READY_OUT  10  /* Output from ESP32-C5 to Pico 2 */
#define PIN_FLOW_REQ_IN     13  /* Input from Pico 2 to ESP32-C5 */

/* GDMA single descriptor max transfer limit is 4092 bytes */
#define QSPI_BUFFER_SIZE    4080

typedef struct {
    uint32_t rx_packets;
    uint32_t tx_packets;
    uint32_t rx_bytes;
    uint32_t tx_bytes;
    uint32_t errors;
} qspi_stats_t;

esp_err_t qspi_slave_gdma_init(void);
esp_err_t qspi_slave_send(const uint8_t *data, size_t len, TickType_t timeout);
int qspi_slave_receive(uint8_t *buf, size_t max_len, TickType_t timeout);
void qspi_slave_get_stats(qspi_stats_t *stats);
void qspi_slave_set_ready(bool ready);

#ifdef __cplusplus
}
#endif

#endif /* QSPI_SLAVE_GDMA_H */