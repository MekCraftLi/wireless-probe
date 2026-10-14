#ifndef UFMF_PROTOCOL_H
#define UFMF_PROTOCOL_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define UFMF_MAGIC                  0xA5
#define UFMF_MAX_PAYLOAD_LEN        4096
#define UFMF_HEADER_LEN             12

/* Channel Definitions */
typedef enum {
    UFMF_CH_NONE       = 0x00,
    UFMF_CH_CTRL       = 0x01,  /* Interactive CMSIS-DAP / Debug Commands */
    UFMF_CH_BULK_MEM   = 0x02,  /* High-throughput Flash / RAM Memory Transfer */
    UFMF_CH_TRACE      = 0x03,  /* High-speed Trace / SWO / RTT Stream */
    UFMF_CH_UART       = 0x04,  /* Target UART Console (Virtual COM) */
    UFMF_CH_SYS        = 0x05,  /* System heartbeat, sync, link stats */
} ufmf_channel_t;

/* Flags */
#define UFMF_FLAG_REQ               0x0000
#define UFMF_FLAG_ACK               0x0001
#define UFMF_FLAG_MORE_FRAG         0x0002
#define UFMF_FLAG_PIPELINE_COMMIT   0x0004
#define UFMF_FLAG_URGENT            0x0008
#define UFMF_FLAG_ERROR             0x8000

/* UFMF Frame Header (12 Bytes, 32-bit aligned) */
typedef struct __attribute__((packed)) {
    uint8_t  magic;         /* 0xA5 */
    uint8_t  channel;       /* ufmf_channel_t */
    uint16_t seq;           /* Packet Sequence Number */
    uint16_t flags;         /* Control flags */
    uint16_t payload_len;   /* Length of payload (0 .. UFMF_MAX_PAYLOAD_LEN) */
    uint16_t crc16;         /* CRC16-CCITT (or 0 if skipped) */
    uint16_t reserved;      /* Reserved for alignment */
} ufmf_header_t;

/* Bulk Memory Transfer Payload Header */
typedef struct __attribute__((packed)) {
    uint32_t target_addr;   /* Target address in memory */
    uint32_t total_length;  /* Total bytes to read/write */
    uint8_t  ap_csw;        /* AP CSW config (0: 32-bit increment, 1: 8-bit, etc.) */
    uint8_t  is_write;      /* 1 = Write to target, 0 = Read from target */
    uint16_t chunk_len;     /* Current chunk length in payload */
} ufmf_bulk_mem_hdr_t;

/* System Heartbeat & Flow-Control Payload */
typedef struct __attribute__((packed)) {
    uint32_t uptime_ms;
    uint32_t rx_bytes;
    uint32_t tx_bytes;
    uint16_t rx_free_credits; /* Available buffer slots in bytes/blocks */
    uint8_t  link_status;     /* 0: Disconnected, 1: Connected, 2: Active */
    uint8_t  swd_status;      /* 0: Idle, 1: Running, 2: Halted */
} ufmf_sys_info_t;

/* CRC16-CCITT (Polynomial 0x1021, Init 0xFFFF) */
static inline uint16_t ufmf_calc_crc16(const uint8_t *data, uint32_t len) {
    uint16_t crc = 0xFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc <<= 1;
            }
        }
    }
    return crc;
}

#ifdef __cplusplus
}
#endif

#endif /* UFMF_PROTOCOL_H */
