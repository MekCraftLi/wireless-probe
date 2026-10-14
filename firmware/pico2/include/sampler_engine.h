#ifndef SAMPLER_ENGINE_H
#define SAMPLER_ENGINE_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* RTP Header Constants */
#define RTP_VERSION_2           0x80
#define RTP_PT_RAW_WORDS        0x60    /* Continuous memory block of 32-bit words */
#define RTP_PT_MULTIVAR         0x61    /* Multi-variable vector */

/* RTSP-Style Control Commands (CMSIS-DAP Vendor Extension IDs) */
#define RTSP_CMD_SETUP          0x80    /* Configure target addresses, mode, rate */
#define RTSP_CMD_PLAY           0x81    /* Start autonomous high-speed sampling */
#define RTSP_CMD_TEARDOWN       0x82    /* Stop autonomous sampling */
#define RTSP_CMD_GET_STATS      0x83    /* Query runtime statistics */

/* Sampling Modes */
#define SAMPLER_MODE_BLOCK      0x00    /* Saturate SWD with autoincrement TAR block read */
#define SAMPLER_MODE_MULTIVAR   0x01    /* Arbitrary non-contiguous variable addresses */

#define SAMPLER_MAX_VARS        32
#define SAMPLER_DEFAULT_BURST   128     /* 128 words = 512 bytes per packet */
#define SAMPLER_MAX_BURST       256     /* 256 words = 1024 bytes per packet */

#pragma pack(push, 1)

/* RTP Telemetry Header (12 Bytes, RFC 3550 aligned) */
typedef struct {
    uint8_t  magic;          /* 0x80 (RTP v2) */
    uint8_t  payload_type;   /* 0x60 (Raw) or 0x61 (Multivar) */
    uint16_t seq;            /* 16-bit monotonic sequence counter */
    uint32_t timestamp_us;   /* Hardware microsecond timestamp */
    uint16_t session_id;     /* Session identifier */
    uint16_t sample_count;   /* Number of 32-bit values in payload */
} rtp_telemetry_hdr_t;

/* Telemetry Runtime Statistics */
typedef struct {
    uint32_t total_frames;
    uint32_t total_samples;
    uint32_t total_bytes;
    uint32_t swd_errors;
    uint32_t buffer_overruns;
    uint32_t wire_rate_sps;  /* Instantaneous samples/sec */
} sampler_stats_t;

#pragma pack(pop)

/* Core Engine APIs */
void sampler_engine_init(void);
void sampler_engine_start_task(void);
uint32_t sampler_process_vendor_command(const uint8_t *request, uint8_t *response);
void sampler_engine_get_stats(sampler_stats_t *out_stats);
bool sampler_engine_is_running(void);

#ifdef __cplusplus
}
#endif

#endif /* SAMPLER_ENGINE_H */
