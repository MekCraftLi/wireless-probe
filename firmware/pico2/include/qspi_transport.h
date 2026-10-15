#ifndef QSPI_TRANSPORT_H
#define QSPI_TRANSPORT_H

#include <stdint.h>
#include <stdbool.h>
#include "pico/stdlib.h"
#include "hardware/spi.h"
#include "hardware/dma.h"
#include "ufmf_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Hardware Pin Definitions for Pico 2 (RP2350) */
#define PICO_QSPI_PIN_CLK     16
#define PICO_QSPI_PIN_CS      17
#define PICO_QSPI_PIN_D0      18
#define PICO_QSPI_PIN_D1      19
#define PICO_QSPI_PIN_D2      20
#define PICO_QSPI_PIN_D3      21

#define PICO_PIN_C5_RDY       22   /* Input from ESP32-C5 (Low = Ready) */
#define PICO_PIN_P2_REQ       26   /* Output to ESP32-C5 (Low = Request) */

#define PICO_QSPI_BAUDRATE    (25 * 1000 * 1000)  /* 25MHz initial, up to 50-60MHz */
#define PICO_QSPI_BUFFER_SIZE 4096

typedef struct {
    uint32_t tx_packets;
    uint32_t rx_packets;
    uint32_t tx_bytes;
    uint32_t rx_bytes;
    uint32_t dropped_packets;
    bool c5_ready;
} qspi_transport_stats_t;

void qspi_transport_init(void);
void qspi_bus_force_sync(void);
void qspi_transport_start_task(void);
bool qspi_transport_send_frame(ufmf_channel_t ch, const uint8_t *payload, uint16_t len);
void qspi_transport_get_stats(qspi_transport_stats_t *stats);

#ifdef __cplusplus
}
#endif

#endif /* QSPI_TRANSPORT_H */
