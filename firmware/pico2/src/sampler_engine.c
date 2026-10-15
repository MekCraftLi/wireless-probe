#include <string.h>
#include "FreeRTOS.h"
#include "task.h"
#include "pico/stdlib.h"
#include "hardware/timer.h"

#include "sampler_engine.h"
#include "qspi_transport.h"
#include "DAP_config.h"
#include "DAP.h"

#define AP_REG_CSW     ((0U << 2) | DAP_TRANSFER_APnDP)
#define AP_REG_TAR     ((1U << 2) | DAP_TRANSFER_APnDP)
#define AP_REG_DRW_RD  ((3U << 2) | DAP_TRANSFER_RnW | DAP_TRANSFER_APnDP)
#define DP_REG_SELECT  ((2U << 2))
#define DP_REG_RDBUFF  ((3U << 2) | DAP_TRANSFER_RnW)

#define SAMPLER_TASK_PRIO   (tskIDLE_PRIORITY + 3)

static volatile bool s_running = false;
static TaskHandle_t s_sampler_task_handle = NULL;

static uint8_t  s_mode = SAMPLER_MODE_BLOCK;
static uint8_t  s_var_count = 1;
static uint16_t s_burst_words = 128; /* Default 128 words = 512 bytes */
static uint32_t s_sample_rate_hz = 0; /* 0 = Wire Speed Saturation */
static uint32_t s_target_addrs[SAMPLER_MAX_VARS] = {0x20000000};

static uint16_t s_session_id = 0x5341; /* 'SA' */
static uint16_t s_rtp_seq = 0;
static sampler_stats_t s_stats = {0};

static uint8_t s_packet_buf[sizeof(rtp_telemetry_hdr_t) + (SAMPLER_MAX_BURST * 4)] __attribute__((aligned(4)));

/* Execute continuous autoincrement memory block read */
static inline void sampler_read_block_burst(void) {
    rtp_telemetry_hdr_t *hdr = (rtp_telemetry_hdr_t *)s_packet_buf;
    uint32_t *samples = (uint32_t *)(s_packet_buf + sizeof(rtp_telemetry_hdr_t));

    hdr->magic = RTP_VERSION_2;
    hdr->payload_type = RTP_PT_RAW_WORDS;
    hdr->seq = s_rtp_seq++;
    hdr->timestamp_us = to_us_since_boot(get_absolute_time());
    hdr->session_id = s_session_id;
    hdr->sample_count = s_burst_words;

    // 1. Select AP 0 (DP_SELECT = 0)
    uint32_t select_ap = 0x00000000;
    SWD_Transfer(DP_REG_SELECT, &select_ap);

    // 2. Configure AP CSW: 32-bit word, auto-increment single
    uint32_t csw = 0x23000052;
    SWD_Transfer(AP_REG_CSW, &csw);

    // 3. Set TAR start address
    uint32_t start_addr = s_target_addrs[0];
    SWD_Transfer(AP_REG_TAR, &start_addr);

    // 4. Initial read to trigger target AHB/AXI memory bus cycle
    uint32_t dummy = 0;
    SWD_Transfer(AP_REG_DRW_RD, &dummy);

    // 5. Pipelined continuous read of (burst_words - 1) words
    uint16_t words_to_read = s_burst_words;
    for (uint16_t i = 0; i < words_to_read - 1; i++) {
        uint8_t ack = SWD_Transfer(AP_REG_DRW_RD, &samples[i]);
        if (ack != DAP_TRANSFER_OK) {
            s_stats.swd_errors++;
        }
    }

    // 6. Final word retrieved from DP RDBUFF
    SWD_Transfer(DP_REG_RDBUFF, &samples[words_to_read - 1]);

    // 7. Push RTP frame over QSPI to ESP32-C5 (which forwards via UDP)
    uint16_t frame_len = sizeof(rtp_telemetry_hdr_t) + (words_to_read * 4);
    if (!qspi_transport_send_frame(UFMF_CH_TRACE, s_packet_buf, frame_len)) {
        s_stats.buffer_overruns++;
    } else {
        s_stats.total_frames++;
        s_stats.total_samples += words_to_read;
        s_stats.total_bytes += frame_len;
    }
}

/* Execute non-contiguous multi-variable sampling vector */
static inline void sampler_read_multivar_burst(void) {
    rtp_telemetry_hdr_t *hdr = (rtp_telemetry_hdr_t *)s_packet_buf;
    uint32_t *samples = (uint32_t *)(s_packet_buf + sizeof(rtp_telemetry_hdr_t));

    hdr->magic = RTP_VERSION_2;
    hdr->payload_type = RTP_PT_MULTIVAR;
    hdr->seq = s_rtp_seq++;
    hdr->timestamp_us = to_us_since_boot(get_absolute_time());
    hdr->session_id = s_session_id;
    hdr->sample_count = s_var_count;

    // Select AP 0
    uint32_t select_ap = 0x00000000;
    SWD_Transfer(DP_REG_SELECT, &select_ap);

    // Read each variable address
    for (uint8_t v = 0; v < s_var_count; v++) {
        uint32_t addr = s_target_addrs[v];
        SWD_Transfer(AP_REG_TAR, &addr);
        uint32_t dummy = 0;
        SWD_Transfer(AP_REG_DRW_RD, &dummy);
        uint8_t ack = SWD_Transfer(DP_REG_RDBUFF, &samples[v]);
        if (ack != DAP_TRANSFER_OK) {
            s_stats.swd_errors++;
        }
    }

    uint16_t frame_len = sizeof(rtp_telemetry_hdr_t) + (s_var_count * 4);
    if (!qspi_transport_send_frame(UFMF_CH_TRACE, s_packet_buf, frame_len)) {
        s_stats.buffer_overruns++;
    } else {
        s_stats.total_frames++;
        s_stats.total_samples += s_var_count;
        s_stats.total_bytes += frame_len;
    }
}

/* Autonomous high-speed sampling worker task running on Core 1 */
static void sampler_worker_task(void *pvParameters) {
    uint32_t last_calc_time = to_ms_since_boot(get_absolute_time());
    uint32_t last_sample_cnt = 0;

    while (1) {
        if (!s_running) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        uint64_t t_start = time_us_64();

        if (s_mode == SAMPLER_MODE_BLOCK) {
            sampler_read_block_burst();
        } else {
            sampler_read_multivar_burst();
        }

        // Periodic SPS computation
        uint32_t now_ms = to_ms_since_boot(get_absolute_time());
        if (now_ms - last_calc_time >= 500) {
            uint32_t delta_samples = s_stats.total_samples - last_sample_cnt;
            uint32_t delta_ms = now_ms - last_calc_time;
            s_stats.wire_rate_sps = (delta_samples * 1000) / delta_ms;
            last_sample_cnt = s_stats.total_samples;
            last_calc_time = now_ms;
        }

        // Pacing: if sample_rate_hz == 0, wire speed saturation (0 delay)!
        if (s_sample_rate_hz > 0) {
            uint32_t target_period_us = 1000000 / s_sample_rate_hz;
            uint64_t elapsed_us = time_us_64() - t_start;
            if (elapsed_us < target_period_us) {
                busy_wait_us_32((uint32_t)(target_period_us - elapsed_us));
            }
        }
    }
}

void sampler_engine_init(void) {
    memset(&s_stats, 0, sizeof(s_stats));
    s_running = false;
    s_rtp_seq = 0;
}

void sampler_engine_start_task(void) {
    xTaskCreate(sampler_worker_task, "sampler", 512, NULL, SAMPLER_TASK_PRIO, &s_sampler_task_handle);
#if (configNUMBER_OF_CORES > 1)
    if (s_sampler_task_handle) {
        vTaskCoreAffinitySet(s_sampler_task_handle, (1 << 1)); /* Core 1 affinity */
    }
#endif
}

uint32_t sampler_process_vendor_command(const uint8_t *request, uint8_t *response) {
    uint8_t cmd = request[0];
    uint32_t resp_len = 1;

    response[0] = cmd;

    switch (cmd) {
        case RTSP_CMD_SETUP: {
            /* Format: [0x80] [mode:1B] [var_count:1B] [burst_words:2B] [rate_hz:4B] [addrs:N*4B] */
            s_mode = request[1];
            s_var_count = request[2];
            if (s_var_count == 0 || s_var_count > SAMPLER_MAX_VARS) s_var_count = 1;

            s_burst_words = request[3] | ((uint16_t)request[4] << 8);
            if (s_burst_words == 0 || s_burst_words > SAMPLER_MAX_BURST) s_burst_words = SAMPLER_DEFAULT_BURST;

            s_sample_rate_hz = (uint32_t)request[5] |
                               ((uint32_t)request[6] << 8) |
                               ((uint32_t)request[7] << 16) |
                               ((uint32_t)request[8] << 24);

            const uint32_t *addrs = (const uint32_t *)(request + 9);
            for (uint8_t i = 0; i < s_var_count; i++) {
                s_target_addrs[i] = addrs[i];
            }

            s_session_id++;
            s_rtp_seq = 0;
            memset(&s_stats, 0, sizeof(s_stats));

            response[1] = 0; // Status OK
            response[2] = (uint8_t)(s_session_id & 0xFF);
            response[3] = (uint8_t)(s_session_id >> 8);

            // Return estimated theoretical wire rate limit (SPS)
            // At 10MHz SWD: 46 clocks/word = 217391 SPS in block mode, 108695 SPS in multivar mode
            uint32_t max_sps = (s_mode == SAMPLER_MODE_BLOCK) ? 217391 : (108695 / s_var_count);
            memcpy(response + 4, &max_sps, 4);

            resp_len = 8;
            break;
        }

        case RTSP_CMD_PLAY: {
            s_running = true;
            if (s_sampler_task_handle) {
                xTaskNotifyGive(s_sampler_task_handle);
            }
            response[1] = 0; // OK
            resp_len = 2;
            break;
        }

        case RTSP_CMD_TEARDOWN: {
            s_running = false;
            response[1] = 0; // OK
            resp_len = 2;
            break;
        }

        case RTSP_CMD_GET_STATS: {
            /* Return telemetry statistics */
            memcpy(response + 1, &s_stats.total_frames, 4);
            memcpy(response + 5, &s_stats.total_samples, 4);
            memcpy(response + 9, &s_stats.total_bytes, 4);
            memcpy(response + 13, &s_stats.swd_errors, 4);
            memcpy(response + 17, &s_stats.buffer_overruns, 4);
            memcpy(response + 21, &s_stats.wire_rate_sps, 4);
            resp_len = 25;
            break;
        }

        default:
            response[1] = 0xFF; // Unsupported
            resp_len = 2;
            break;
    }

    return (resp_len | (1U << 16));
}

void sampler_engine_get_stats(sampler_stats_t *out_stats) {
    if (out_stats) {
        *out_stats = s_stats;
    }
}

bool sampler_engine_is_running(void) {
    return s_running;
}
