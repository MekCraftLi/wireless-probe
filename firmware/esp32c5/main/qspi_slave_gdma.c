#include <string.h>
#include "esp_log.h"
#include "driver/spi_slave_hd.h"
#include "driver/gpio.h"
#include "esp_heap_caps.h"
#include "hal/spi_ll.h"
#include "qspi_slave_gdma.h"

static const char *TAG = "qspi_slave_hd";

#define QSPI_RX_QUEUE_SIZE 4

static qspi_stats_t s_stats = {0};
static uint8_t *s_rx_buffers[QSPI_RX_QUEUE_SIZE] = {0};
static spi_slave_hd_data_t s_rx_trans_pool[QSPI_RX_QUEUE_SIZE] = {0};

static uint8_t *s_dma_tx_buf = NULL;
static spi_slave_hd_data_t s_tx_trans = {0};

void qspi_slave_set_ready(bool ready) {
    gpio_set_level(PIN_FLOW_READY_OUT, ready ? 0 : 1);
}

esp_err_t qspi_slave_gdma_init(void) {
    // 1. Flow control GPIOs
    gpio_config_t io_conf_ready = {
        .pin_bit_mask = (1ULL << PIN_FLOW_READY_OUT),
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf_ready);
    gpio_set_level(PIN_FLOW_READY_OUT, 1);

    gpio_config_t io_conf_req = {
        .pin_bit_mask = (1ULL << PIN_FLOW_REQ_IN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io_conf_req);

    // Pull-downs on SPI bus pins to test physical driving
    gpio_set_pull_mode(PIN_QSPI_CS, GPIO_PULLUP_ONLY);
    gpio_set_pull_mode(PIN_QSPI_D0, GPIO_PULLDOWN_ONLY);
    gpio_set_pull_mode(PIN_QSPI_D1, GPIO_PULLDOWN_ONLY);
    gpio_set_pull_mode(PIN_QSPI_D2, GPIO_PULLDOWN_ONLY);
    gpio_set_pull_mode(PIN_QSPI_D3, GPIO_PULLDOWN_ONLY);

    // 2. Allocate DMA TX buffer (64-byte aligned for cache)
    s_dma_tx_buf = heap_caps_aligned_alloc(64, QSPI_BUFFER_SIZE, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!s_dma_tx_buf) {
        ESP_LOGE(TAG, "Failed to allocate DMA TX buffer");
        return ESP_ERR_NO_MEM;
    }
    memset(s_dma_tx_buf, 0, QSPI_BUFFER_SIZE);

    // 3. Configure 4-bit Quad SPI Slave HD peripheral (Segment Mode)
    spi_bus_config_t buscfg = {
        .mosi_io_num = PIN_QSPI_D0,
        .miso_io_num = PIN_QSPI_D1,
        .sclk_io_num = PIN_QSPI_SCLK,
        .quadwp_io_num = PIN_QSPI_D2,
        .quadhd_io_num = PIN_QSPI_D3,
        .max_transfer_sz = QSPI_BUFFER_SIZE,
        .flags = SPICOMMON_BUSFLAG_QUAD,
    };

    spi_slave_hd_slot_config_t slvcfg = {
        .spics_io_num = PIN_QSPI_CS,
        .mode = 0,
        .command_bits = 8,
        .address_bits = 8,
        .dummy_bits = 8,
        .queue_size = QSPI_RX_QUEUE_SIZE,
        .dma_chan = SPI_DMA_CH_AUTO,
        .flags = 0, // Segment Mode: CMD7 ends WRDMA, CMD8 ends RDDMA
    };

    esp_err_t ret = spi_slave_hd_init(SPI2_HOST, &buscfg, &slvcfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "spi_slave_hd_init failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // 4. Pre-allocate and queue RX transaction pool
    for (int i = 0; i < QSPI_RX_QUEUE_SIZE; i++) {
        s_rx_buffers[i] = heap_caps_aligned_alloc(64, QSPI_BUFFER_SIZE, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
        if (!s_rx_buffers[i]) {
            ESP_LOGE(TAG, "Failed to allocate RX DMA buffer %d", i);
            return ESP_ERR_NO_MEM;
        }
        memset(s_rx_buffers[i], 0, QSPI_BUFFER_SIZE);

        s_rx_trans_pool[i].data = s_rx_buffers[i];
        s_rx_trans_pool[i].len = QSPI_BUFFER_SIZE;
        s_rx_trans_pool[i].flags = SPI_SLAVE_HD_TRANS_DMA_BUFFER_ALIGN_AUTO;

        ret = spi_slave_hd_queue_trans(SPI2_HOST, SPI_SLAVE_CHAN_RX, &s_rx_trans_pool[i], portMAX_DELAY);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "Failed to pre-queue RX trans %d: %s", i, esp_err_to_name(ret));
            return ret;
        }
    }

    ESP_LOGI(TAG, "Hardware Quad-SPI Slave (Segment Mode) initialized! Queue: %d x %d B. CLK:%d, CS:%d, D0-D3:%d,%d,%d,%d",
             QSPI_RX_QUEUE_SIZE, QSPI_BUFFER_SIZE, PIN_QSPI_SCLK, PIN_QSPI_CS, PIN_QSPI_D0, PIN_QSPI_D1, PIN_QSPI_D2, PIN_QSPI_D3);
    return ESP_OK;
}

esp_err_t qspi_slave_send(const uint8_t *data, size_t len, TickType_t timeout) {
    if (len > QSPI_BUFFER_SIZE) len = QSPI_BUFFER_SIZE;
    size_t align_len = (len + 3) & ~3;
    memcpy(s_dma_tx_buf, data, len);

    s_tx_trans.data = s_dma_tx_buf;
    s_tx_trans.len = align_len;
    s_tx_trans.flags = SPI_SLAVE_HD_TRANS_DMA_BUFFER_ALIGN_AUTO;

    esp_err_t ret = spi_slave_hd_queue_trans(SPI2_HOST, SPI_SLAVE_CHAN_TX, &s_tx_trans, timeout);
    if (ret != ESP_OK) return ret;

    gpio_set_level(PIN_FLOW_READY_OUT, 0); // Assert ready (low)

    spi_slave_hd_data_t *ret_trans = NULL;
    ret = spi_slave_hd_get_trans_res(SPI2_HOST, SPI_SLAVE_CHAN_TX, &ret_trans, timeout);

    gpio_set_level(PIN_FLOW_READY_OUT, 1); // Deassert ready (high)

    if (ret == ESP_OK) {
        s_stats.tx_packets++;
        s_stats.tx_bytes += len;
    } else {
        s_stats.errors++;
    }
    return ret;
}

int qspi_slave_receive(uint8_t *buf, size_t max_len, TickType_t timeout) {
    spi_slave_hd_data_t *ret_trans = NULL;
    esp_err_t ret = spi_slave_hd_get_trans_res(SPI2_HOST, SPI_SLAVE_CHAN_RX, &ret_trans, timeout);

    if (ret == ESP_OK && ret_trans) {
        size_t rx_bytes = ret_trans->trans_len;
        if (rx_bytes > max_len) rx_bytes = max_len;

        if (rx_bytes > 0) {
            memcpy(buf, ret_trans->data, rx_bytes);
            s_stats.rx_packets++;
            s_stats.rx_bytes += rx_bytes;
        }

        // Re-queue the descriptor to keep the RX pool stocked
        ret_trans->trans_len = 0;
        ret_trans->len = QSPI_BUFFER_SIZE;
        ret_trans->flags = SPI_SLAVE_HD_TRANS_DMA_BUFFER_ALIGN_AUTO;
        spi_slave_hd_queue_trans(SPI2_HOST, SPI_SLAVE_CHAN_RX, ret_trans, portMAX_DELAY);

        return (int)rx_bytes;
    }

    if (ret != ESP_OK && ret != ESP_ERR_TIMEOUT) {
        ESP_LOGW(TAG, "spi_slave_hd_get_trans_res returned %s", esp_err_to_name(ret));
    }

    return -1;
}

void qspi_slave_get_stats(qspi_stats_t *stats) {
    if (stats) *stats = s_stats;
}
