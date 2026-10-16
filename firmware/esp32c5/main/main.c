#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "soc/spi_struct.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "ufmf_protocol.h"
#include "wifi_manager.h"
#include "net_sockets.h"
#include "qspi_slave_gdma.h"
#include "web_server.h"

static const char *TAG = "c5_bridge";

static uint16_t s_tx_seq = 0;
static bool s_loopback_echo_mode = false;

/* Callback when network data is received from PC debugger */
static void on_network_rx(ufmf_channel_t ch, const uint8_t *data, size_t len) {
    if (len > UFMF_MAX_PAYLOAD_LEN) {
        ESP_LOGW(TAG, "Network packet too large: %d", (int)len);
        return;
    }

    if (s_loopback_echo_mode) {
        net_sockets_send(ch, data, len);
        return;
    }

    /* Package into UFMF frame for QSPI transmission to Pico 2 */
    static uint8_t qspi_tx_buf[UFMF_HEADER_LEN + UFMF_MAX_PAYLOAD_LEN];
    ufmf_header_t *hdr = (ufmf_header_t *)qspi_tx_buf;
    hdr->magic = UFMF_MAGIC;
    hdr->channel = (uint8_t)ch;
    hdr->seq = s_tx_seq++;
    hdr->flags = UFMF_FLAG_REQ;
    hdr->payload_len = (uint16_t)len;
    hdr->crc16 = 0;
    hdr->reserved = 0;

    memcpy(qspi_tx_buf + UFMF_HEADER_LEN, data, len);
    size_t total_len = UFMF_HEADER_LEN + len;

    qspi_slave_send(qspi_tx_buf, total_len, pdMS_TO_TICKS(1000));
}

/* Background task receiving frames from Pico 2 via QSPI and forwarding to PC */
static void qspi_rx_task(void *pvParameters) {
    uint8_t *qspi_rx_buf = malloc(QSPI_BUFFER_SIZE);
    if (!qspi_rx_buf) {
        ESP_LOGE(TAG, "Failed to allocate QSPI RX buffer");
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "QSPI -> Net forwarding task started");
    int rx_timeouts = 0;
    while (1) {
        int rx_len = qspi_slave_receive(qspi_rx_buf, QSPI_BUFFER_SIZE, pdMS_TO_TICKS(500));
        if (rx_len > 0) {
            rx_timeouts = 0;
            ESP_LOGI(TAG, ">>> QSPI RX SUCCESS! Bytes: %d, Magic: 0x%02X, Ch: 0x%02X, Seq: %d",
                     rx_len, qspi_rx_buf[0], qspi_rx_buf[1], (qspi_rx_buf[2] | (qspi_rx_buf[3] << 8)));
            if (rx_len >= 8) {
                ESP_LOGI(TAG, "    RAW HDR: %02X %02X %02X %02X %02X %02X %02X %02X",
                         qspi_rx_buf[0], qspi_rx_buf[1], qspi_rx_buf[2], qspi_rx_buf[3],
                         qspi_rx_buf[4], qspi_rx_buf[5], qspi_rx_buf[6], qspi_rx_buf[7]);
            }

            if (rx_len >= UFMF_HEADER_LEN) {
                ufmf_header_t *hdr = (ufmf_header_t *)qspi_rx_buf;
                if (hdr->magic == UFMF_MAGIC) {
                    size_t payload_len = hdr->payload_len;
                    if (payload_len <= (size_t)(rx_len - UFMF_HEADER_LEN)) {
                        const uint8_t *payload = qspi_rx_buf + UFMF_HEADER_LEN;
                        net_sockets_send((ufmf_channel_t)hdr->channel, payload, payload_len);
                    }
                }
            }
        } else {
            if (++rx_timeouts >= 6) {
                rx_timeouts = 0;
                ESP_LOGI(TAG, "QSPI RX waiting... (no packet completed yet)");
                if (net_sockets_client_connected(UFMF_CH_CTRL) || net_sockets_client_connected(UFMF_CH_BULK_MEM)) {
                    ESP_LOGW(TAG, "Watchdog: TCP client connected but QSPI idle for >3s, triggering self-healing GDMA reset...");
                    qspi_slave_gdma_reset();
                }
            }
        }
    }
}

/* System monitor / status display task */
static void status_monitor_task(void *pvParameters) {
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(2000));
        char ip_str[32] = {0};
        wifi_manager_get_ip_str(ip_str, sizeof(ip_str));

        qspi_stats_t q_stats;
        qspi_slave_get_stats(&q_stats);

        bool ctrl_connected = net_sockets_client_connected(UFMF_CH_CTRL);
        bool bulk_connected = net_sockets_client_connected(UFMF_CH_BULK_MEM);

        int cs = gpio_get_level(PIN_QSPI_CS);
        int clk = gpio_get_level(PIN_QSPI_SCLK);
        int d0 = gpio_get_level(PIN_QSPI_D0);
        int d1 = gpio_get_level(PIN_QSPI_D1);
        int d2 = gpio_get_level(PIN_QSPI_D2);
        int d3 = gpio_get_level(PIN_QSPI_D3);
        int req = gpio_get_level(PIN_FLOW_REQ_IN);
        int rdy = gpio_get_level(PIN_FLOW_READY_OUT);

        int g7 = gpio_get_level(7);
        int g0 = gpio_get_level(0);
        int g1 = gpio_get_level(1);
        int g2 = gpio_get_level(2);
        int g3 = gpio_get_level(3);
        int g24 = gpio_get_level(24);
        int g25 = gpio_get_level(25);

        uint32_t last_cmd = GPSPI2.slave1.slv_last_command;
        uint32_t bitlen = GPSPI2.slave1.slv_data_bitlen;
        uint32_t dma_int = GPSPI2.dma_int_raw.val;

        ESP_LOGI(TAG, "[Status] IP: %s | Clients: Ctrl=%d Bulk=%d | QSPI: TX=%lu RX=%lu Err=%lu",
                 ip_str, ctrl_connected, bulk_connected,
                 (unsigned long)q_stats.tx_packets, (unsigned long)q_stats.rx_packets, (unsigned long)q_stats.errors);
        ESP_LOGI(TAG, "[BusPins] CS:%d CLK:%d D0:%d D1:%d D2(8):%d D3:%d | REQ:%d RDY:%d",
                 cs, clk, d0, d1, d2, d3, req, rdy);
        ESP_LOGI(TAG, "[CandPins] G7:%d G0:%d G1:%d G2:%d G3:%d G24:%d G25:%d | HW_Cmd:0x%02X Bits:%lu Intr:0x%04lX",
                 g7, g0, g1, g2, g3, g24, g25, (unsigned int)last_cmd, (unsigned long)bitlen, (unsigned long)dma_int);

        char pin_buf[32];
        for (int p = 0; p <= 28; p++) {
            pin_buf[p] = gpio_get_level(p) ? '1' : '0';
        }
        pin_buf[29] = '\0';
        ESP_LOGI(TAG, "[ALL GPIOs 0..28]: %s", pin_buf);
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, "  ESP32-C5 High-Speed Wireless Probe Bridge v1.0  ");
    ESP_LOGI(TAG, "==================================================");

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(wifi_manager_init());
    ESP_ERROR_CHECK(web_server_init());

    net_sockets_set_rx_callback(on_network_rx);
    ESP_ERROR_CHECK(net_sockets_init());

    ret = qspi_slave_gdma_init();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "QSPI init returned %d", ret);
    }

    xTaskCreate(qspi_rx_task, "qspi_rx", 4096, NULL, 6, NULL);
    xTaskCreate(status_monitor_task, "monitor", 2048, NULL, 2, NULL);

    ESP_LOGI(TAG, "Bridge initialisation complete. Ready for connections.");
}
