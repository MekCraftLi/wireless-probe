#include <string.h>
#include <stdio.h>
#include "tusb.h"
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include "pico/stdlib.h"
#include "hardware/pio.h"
#include "hardware/dma.h"
#include "hardware/uart.h"
#include "hardware/clocks.h"

#include "qspi_transport.h"
#include "probe_config.h"
#include "DAP_config.h"
#include "DAP.h"
#include "sampler_engine.h"
#include "qspi.pio.h"

#define ESP_SPI_CMD_WRDMA_QUAD  0x23
#define ESP_SPI_CMD_RDDMA_QUAD  0x24
#define ESP_SPI_CMD_WR_END      0x27
#define ESP_SPI_CMD_INT0        0x28

static qspi_transport_stats_t s_stats = {0};

static uint8_t s_qspi_tx_buf[PICO_QSPI_BUFFER_SIZE] __attribute__((aligned(4)));
static uint8_t s_qspi_rx_buf[PICO_QSPI_BUFFER_SIZE] __attribute__((aligned(4)));
static uint8_t s_dap_resp_buf[DAP_PACKET_SIZE] __attribute__((aligned(4)));

static QueueHandle_t s_tx_queue = NULL;
static PIO s_pio = pio1;
static uint s_sm_cmd = 0;
static uint s_sm_tx  = 1;
static uint s_sm_rx  = 2;

static uint s_offset_cmd;
static uint s_offset_tx;
static uint s_offset_rx;
static uint16_t s_seq = 0;

#define QSPI_TX_ITEM_SIZE   1100

typedef struct {
    uint8_t channel;
    uint16_t len;
    uint8_t data[QSPI_TX_ITEM_SIZE];
} tx_queue_item_t;

/* Configure PIO State Machines for Quad-SPI */
static void init_qspi_pio_sm(void) {
    // 1. Load programs into PIO1
    s_offset_cmd = pio_add_program(s_pio, &qspi_cmd_program);
    s_offset_tx  = pio_add_program(s_pio, &qspi_tx_program);
    s_offset_rx  = pio_add_program(s_pio, &qspi_rx_program);

    // CS pin
    gpio_init(PICO_QSPI_PIN_CS);
    gpio_set_dir(PICO_QSPI_PIN_CS, GPIO_OUT);
    gpio_put(PICO_QSPI_PIN_CS, 1);

    // Pin routing for PIO
    pio_gpio_init(s_pio, PICO_QSPI_PIN_CLK);
    for (int i = 0; i < 4; i++) {
        pio_gpio_init(s_pio, PICO_QSPI_PIN_D0 + i);
        gpio_pull_up(PICO_QSPI_PIN_D0 + i);
    }

    const float clkdiv = 6.0f; // ~25MHz PIO / 4 cycles = ~6.25MHz SPI clock for clean signal

    // 1. Command SM: 1-bit on D0, sideset CLK, shift left, no autopull
    pio_sm_config c_cmd = qspi_cmd_program_get_default_config(s_offset_cmd);
    sm_config_set_out_pins(&c_cmd, PICO_QSPI_PIN_D0, 1);
    sm_config_set_sideset_pins(&c_cmd, PICO_QSPI_PIN_CLK);
    sm_config_set_out_shift(&c_cmd, false, false, 32);
    sm_config_set_clkdiv(&c_cmd, clkdiv);
    pio_sm_init(s_pio, s_sm_cmd, s_offset_cmd, &c_cmd);

    // 2. TX SM: 4-bit on D0..D3, sideset CLK, shift left, no autopull
    pio_sm_config c_tx = qspi_tx_program_get_default_config(s_offset_tx);
    sm_config_set_out_pins(&c_tx, PICO_QSPI_PIN_D0, 4);
    sm_config_set_sideset_pins(&c_tx, PICO_QSPI_PIN_CLK);
    sm_config_set_out_shift(&c_tx, false, false, 32);
    sm_config_set_clkdiv(&c_tx, clkdiv);
    pio_sm_init(s_pio, s_sm_tx, s_offset_tx, &c_tx);

    // 3. RX SM: In pins D0..D3, sideset CLK, shift left, no autopush
    pio_sm_config c_rx = qspi_rx_program_get_default_config(s_offset_rx);
    sm_config_set_in_pins(&c_rx, PICO_QSPI_PIN_D0);
    sm_config_set_sideset_pins(&c_rx, PICO_QSPI_PIN_CLK);
    sm_config_set_out_shift(&c_rx, false, false, 32);
    sm_config_set_in_shift(&c_rx, false, false, 32);
    sm_config_set_clkdiv(&c_rx, clkdiv);
    pio_sm_init(s_pio, s_sm_rx, s_offset_rx, &c_rx);
}

static void qspi_pio_send_cmd_dummy(uint8_t cmd) {
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_cmd, PICO_QSPI_PIN_D0, 1, true);
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_cmd, PICO_QSPI_PIN_CLK, 1, true);

    pio_sm_clear_fifos(s_pio, s_sm_cmd);
    pio_sm_restart(s_pio, s_sm_cmd);
    pio_sm_exec(s_pio, s_sm_cmd, pio_encode_jmp(s_offset_cmd));
    pio_sm_set_enabled(s_pio, s_sm_cmd, true);

    // Push 32-bit word: bits 31..24 = cmd, bits 23..16 = 0 (addr), bits 15..8 = 0 (dummy)
    pio_sm_put_blocking(s_pio, s_sm_cmd, ((uint32_t)cmd << 24));

    // Wait for the PIO program to complete all 24 bits and push completion token to RX FIFO
    pio_sm_get_blocking(s_pio, s_sm_cmd);

    pio_sm_set_enabled(s_pio, s_sm_cmd, false);
}

static void qspi_pio_write_quad_payload(const uint8_t *data, size_t len) {
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_tx, PICO_QSPI_PIN_D0, 4, true);
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_tx, PICO_QSPI_PIN_CLK, 1, true);

    pio_sm_clear_fifos(s_pio, s_sm_tx);
    pio_sm_restart(s_pio, s_sm_tx);
    pio_sm_exec(s_pio, s_sm_tx, pio_encode_jmp(s_offset_tx));
    pio_sm_set_enabled(s_pio, s_sm_tx, true);

    size_t word_count = (len + 3) / 4;
    const uint32_t *words = (const uint32_t *)data;

    // Send word and wait for its completion token synchronously (prevents FIFO overflow)
    for (size_t i = 0; i < word_count; i++) {
        pio_sm_put_blocking(s_pio, s_sm_tx, __builtin_bswap32(words[i]));
        pio_sm_get_blocking(s_pio, s_sm_tx);
    }

    pio_sm_set_enabled(s_pio, s_sm_tx, false);
}

static void qspi_pio_read_quad_payload(uint8_t *data, size_t len) {
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_rx, PICO_QSPI_PIN_D0, 4, false);
    pio_sm_set_consecutive_pindirs(s_pio, s_sm_rx, PICO_QSPI_PIN_CLK, 1, true);

    pio_sm_clear_fifos(s_pio, s_sm_rx);
    pio_sm_restart(s_pio, s_sm_rx);
    pio_sm_exec(s_pio, s_sm_rx, pio_encode_jmp(s_offset_rx));
    pio_sm_set_enabled(s_pio, s_sm_rx, true);

    size_t word_count = (len + 3) / 4;
    uint32_t *words = (uint32_t *)data;
    for (size_t i = 0; i < word_count; i++) {
        pio_sm_put_blocking(s_pio, s_sm_rx, 0); // Pushes dummy word to clock in 8 nibbles
        uint32_t val = pio_sm_get_blocking(s_pio, s_sm_rx);
        words[i] = __builtin_bswap32(val);
    }

    pio_sm_set_enabled(s_pio, s_sm_rx, false);
}

void qspi_bus_force_sync(void) {
    // 1. Release CS (CS = 1) and deassert REQ (REQ = 1)
    gpio_put(PICO_QSPI_PIN_CS, 1);
    gpio_put(PICO_PIN_P2_REQ, 1);
    busy_wait_us_32(50);

    // 2. Pulse SCLK 64 times with CS=1 to flush slave shift register bit counter
    gpio_set_function(PICO_QSPI_PIN_CLK, GPIO_FUNC_SIO);
    gpio_set_dir(PICO_QSPI_PIN_CLK, GPIO_OUT);
    for (int i = 0; i < 64; i++) {
        gpio_put(PICO_QSPI_PIN_CLK, 1);
        busy_wait_us_32(1);
        gpio_put(PICO_QSPI_PIN_CLK, 0);
        busy_wait_us_32(1);
    }
    pio_gpio_init(s_pio, PICO_QSPI_PIN_CLK);
    busy_wait_us_32(50);

    // 3. Clear PIO FIFOs and restart all state machines
    pio_sm_set_enabled(s_pio, s_sm_cmd, false);
    pio_sm_set_enabled(s_pio, s_sm_tx, false);
    pio_sm_set_enabled(s_pio, s_sm_rx, false);
    pio_sm_clear_fifos(s_pio, s_sm_cmd);
    pio_sm_clear_fifos(s_pio, s_sm_tx);
    pio_sm_clear_fifos(s_pio, s_sm_rx);

    // 4. Force termination of any stuck WRDMA transaction on ESP32-C5
    gpio_put(PICO_QSPI_PIN_CS, 0);
    busy_wait_us_32(5);
    qspi_pio_send_cmd_dummy(ESP_SPI_CMD_WR_END);
    gpio_put(PICO_QSPI_PIN_CS, 1);
    busy_wait_us_32(10);

    // 5. Force termination of any stuck RDDMA transaction on ESP32-C5
    gpio_put(PICO_QSPI_PIN_CS, 0);
    busy_wait_us_32(5);
    qspi_pio_send_cmd_dummy(ESP_SPI_CMD_INT0);
    gpio_put(PICO_QSPI_PIN_CS, 1);
    busy_wait_us_32(50);
}

void qspi_transport_init(void) {
    s_tx_queue = xQueueCreate(8, sizeof(tx_queue_item_t));

    // 1. Flow control GPIOs
    gpio_init(PICO_PIN_C5_RDY);
    gpio_set_dir(PICO_PIN_C5_RDY, GPIO_IN);
    gpio_pull_up(PICO_PIN_C5_RDY);

    gpio_init(PICO_PIN_P2_REQ);
    gpio_set_dir(PICO_PIN_P2_REQ, GPIO_OUT);
    gpio_put(PICO_PIN_P2_REQ, 1);

    // 2. PIO QSPI setup
    init_qspi_pio_sm();

    // 3. Force bus flush & synchronization on boot
    qspi_bus_force_sync();
}

bool qspi_transport_send_frame(ufmf_channel_t ch, const uint8_t *payload, uint16_t len) {
    if (!s_tx_queue || len > QSPI_TX_ITEM_SIZE) return false;

    tx_queue_item_t item;
    item.channel = (uint8_t)ch;
    item.len = len;
    if (payload && len > 0) {
        memcpy(item.data, payload, len);
    }

    if (ch == UFMF_CH_CTRL || ch == UFMF_CH_SYS) {
        /* High-priority control plane: send to front, drop stale trace item if queue full */
        if (xQueueSendToFront(s_tx_queue, &item, pdMS_TO_TICKS(50)) != pdTRUE) {
            tx_queue_item_t discard;
            xQueueReceive(s_tx_queue, &discard, 0);
            xQueueSendToFront(s_tx_queue, &item, 0);
        }
        return true;
    } else {
        /* Data plane: non-blocking, drop on backpressure */
        if (xQueueSend(s_tx_queue, &item, 0) != pdTRUE) {
            s_stats.dropped_packets++;
            return false;
        }
        return true;
    }
}

static void process_incoming_frame(const uint8_t *frame_data, size_t total_len) {
    if (total_len < UFMF_HEADER_LEN) return;

    const ufmf_header_t *hdr = (const ufmf_header_t *)frame_data;
    if (hdr->magic != UFMF_MAGIC) return;

    s_stats.rx_packets++;
    s_stats.rx_bytes += total_len;

    const uint8_t *payload = frame_data + UFMF_HEADER_LEN;
    uint16_t payload_len = hdr->payload_len;

    if (hdr->channel == UFMF_CH_CTRL && payload_len > 0) {
        uint32_t resp_len = 0;
        if (payload[0] >= 0x80 && payload[0] <= 0x8F) {
            resp_len = sampler_process_vendor_command(payload, s_dap_resp_buf);
        } else {
            resp_len = DAP_ProcessCommand(payload, s_dap_resp_buf);
        }
        if (resp_len > 0) {
            qspi_transport_send_frame(UFMF_CH_CTRL, s_dap_resp_buf, (uint16_t)resp_len);
        }
    } else if (hdr->channel == UFMF_CH_SYS) {
        ufmf_sys_info_t info = {
            .uptime_ms = to_ms_since_boot(get_absolute_time()),
            .rx_bytes = s_stats.rx_bytes,
            .tx_bytes = s_stats.tx_bytes,
            .link_status = 2,
            .swd_status = 1
        };
        qspi_transport_send_frame(UFMF_CH_SYS, (const uint8_t *)&info, sizeof(info));
    }
}

static void qspi_worker_task(void *pvParameters) {
    uint32_t last_heartbeat = 0; // Send initial heartbeat immediately

    while (1) {
        bool c5_ready = (gpio_get(PICO_PIN_C5_RDY) == 0);
        s_stats.c5_ready = c5_ready;

        uint32_t now = to_ms_since_boot(get_absolute_time());
        static uint32_t last_diag_print = 0;
        if (tud_cdc_connected() && (now - last_diag_print > 1000)) {
            last_diag_print = now;
            char diag[128];
            int dlen = snprintf(diag, sizeof(diag), "[Pico2] C5_RDY(GP22)=%d | TX_pkts=%lu RX_pkts=%lu Drop=%lu | REQ(GP26)=%d\r\n",
                               gpio_get(PICO_PIN_C5_RDY), s_stats.tx_packets, s_stats.rx_packets, s_stats.dropped_packets,
                               gpio_get(PICO_PIN_P2_REQ));
            tud_cdc_write(diag, dlen);
            tud_cdc_write_flush();
        }

        tx_queue_item_t item;
        bool has_tx = (xQueueReceive(s_tx_queue, &item, 0) == pdTRUE);

        // Check target UART
        uint8_t uart_rx_chunk[64];
        uint16_t uart_cnt = 0;
        while (uart_is_readable(PROBE_UART_INTERFACE) && uart_cnt < sizeof(uart_rx_chunk)) {
            uart_rx_chunk[uart_cnt++] = uart_getc(PROBE_UART_INTERFACE);
        }
        if (uart_cnt > 0) {
            qspi_transport_send_frame(UFMF_CH_UART, uart_rx_chunk, uart_cnt);
        }

        // Periodic heartbeat ping to C5 if idle for > 1s
        now = to_ms_since_boot(get_absolute_time());
        if (!has_tx && (now - last_heartbeat > 1000)) {
            last_heartbeat = now;
            ufmf_sys_info_t info = {
                .uptime_ms = now,
                .rx_bytes = s_stats.rx_bytes,
                .tx_bytes = s_stats.tx_bytes,
                .link_status = 2,
                .swd_status = 1
            };
            qspi_transport_send_frame(UFMF_CH_SYS, (const uint8_t *)&info, sizeof(info));
            has_tx = (xQueueReceive(s_tx_queue, &item, 0) == pdTRUE);
        }

        if (has_tx) {
            // Master WRITE transaction in 4-bit Quad mode
            memset(s_qspi_tx_buf, 0, PICO_QSPI_BUFFER_SIZE);
            ufmf_header_t *hdr = (ufmf_header_t *)s_qspi_tx_buf;
            hdr->magic = UFMF_MAGIC;
            hdr->channel = item.channel;
            hdr->seq = s_seq++;
            hdr->flags = UFMF_FLAG_ACK;
            hdr->payload_len = item.len;
            hdr->crc16 = 0;
            hdr->reserved = 0;

            if (item.len > 0) {
                memcpy(s_qspi_tx_buf + UFMF_HEADER_LEN, item.data, item.len);
            }
            size_t total_len = UFMF_HEADER_LEN + item.len;
            size_t burst_len = (total_len + 15) & ~15;

            gpio_put(PICO_PIN_P2_REQ, 0); // Assert REQ
            gpio_put(PICO_QSPI_PIN_CS, 0); // Assert CS
            busy_wait_us_32(1);
            qspi_pio_send_cmd_dummy(ESP_SPI_CMD_WRDMA_QUAD);
            qspi_pio_write_quad_payload(s_qspi_tx_buf, burst_len);
            gpio_put(PICO_QSPI_PIN_CS, 1); // Deassert CS

            busy_wait_us_32(2);

            // Terminate DMA write transaction on ESP32-C5 slave HD (triggers EOF interrupt)
            gpio_put(PICO_QSPI_PIN_CS, 0);
            busy_wait_us_32(1);
            qspi_pio_send_cmd_dummy(ESP_SPI_CMD_WR_END);
            gpio_put(PICO_QSPI_PIN_CS, 1);
            gpio_put(PICO_PIN_P2_REQ, 1); // Deassert REQ
            busy_wait_us_32(2);

            s_stats.tx_packets++;
            s_stats.tx_bytes += burst_len;
            last_heartbeat = now;
        }

        if (c5_ready) {
            // Master READ transaction in 4-bit Quad mode
            size_t read_len = 544; // Read chunk (sufficient for UFMF header + 512B DAP packet)
            gpio_put(PICO_QSPI_PIN_CS, 0);
            busy_wait_us_32(1);
            qspi_pio_send_cmd_dummy(ESP_SPI_CMD_RDDMA_QUAD);
            qspi_pio_read_quad_payload(s_qspi_rx_buf, read_len);
            gpio_put(PICO_QSPI_PIN_CS, 1);

            busy_wait_us_32(2);

            // Terminate DMA read transaction on ESP32-C5 slave HD
            gpio_put(PICO_QSPI_PIN_CS, 0);
            busy_wait_us_32(1);
            qspi_pio_send_cmd_dummy(ESP_SPI_CMD_INT0);
            gpio_put(PICO_QSPI_PIN_CS, 1);
            busy_wait_us_32(2);

            process_incoming_frame(s_qspi_rx_buf, read_len);
        }

        if (!has_tx && !c5_ready) {
            vTaskDelay(pdMS_TO_TICKS(1));
        }
    }
}

void qspi_transport_start_task(void) {
    TaskHandle_t h = NULL;
    xTaskCreate(qspi_worker_task, "qspi_worker", 1024, NULL, 4, &h);
#if (configNUMBER_OF_CORES > 1)
    if (h) {
        vTaskCoreAffinitySet(h, (1 << 1));
    }
#endif
}

void qspi_transport_get_stats(qspi_transport_stats_t *stats) {
    if (stats) *stats = s_stats;
}
