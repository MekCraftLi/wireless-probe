#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "net_sockets.h"

static const char *TAG = "net_sockets";

static int s_listen_fds[4] = {-1, -1, -1, -1};
static int s_client_fds[4] = {-1, -1, -1, -1};
static const int s_ports[4] = {PORT_DAP_CTRL, PORT_DAP_BULK, PORT_DAP_TRACE, PORT_DAP_UART};
static const ufmf_channel_t s_channels[4] = {UFMF_CH_CTRL, UFMF_CH_BULK_MEM, UFMF_CH_TRACE, UFMF_CH_UART};

static int s_udp_fd = -1;
static struct sockaddr_in s_udp_target_addr = {0};
static bool s_udp_target_active = false;

static net_rx_callback_t s_rx_cb = NULL;

void net_sockets_set_rx_callback(net_rx_callback_t cb) {
    s_rx_cb = cb;
}

void net_sockets_set_udp_target(const char *ip_str, uint16_t port) {
    if (!ip_str) return;
    s_udp_target_addr.sin_family = AF_INET;
    s_udp_target_addr.sin_port = htons(port);
    inet_aton(ip_str, &s_udp_target_addr.sin_addr);
    s_udp_target_active = true;
    ESP_LOGI(TAG, "Explicit UDP stream target configured: %s:%d", ip_str, port);
}

static int create_server_socket(int port, bool is_ctrl) {
    int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) {
        ESP_LOGE(TAG, "socket create failed for port %d", port);
        return -1;
    }

    int opt = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &opt, sizeof(opt));

    if (is_ctrl) {
        int tos = 0xB8; /* DSCP EF (Expedited Forwarding) - Voice/Lowest latency QoS */
        setsockopt(fd, IPPROTO_IP, IP_TOS, &tos, sizeof(tos));
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(port);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "bind failed for port %d", port);
        close(fd);
        return -1;
    }

    if (listen(fd, 2) < 0) {
        ESP_LOGE(TAG, "listen failed for port %d", port);
        close(fd);
        return -1;
    }

    ESP_LOGI(TAG, "Listening on TCP port %d (%s)", port, is_ctrl ? "QoS Voice/Ctrl" : "Bulk/Stream");
    return fd;
}

static void net_server_task(void *pvParameters) {
    uint8_t *rx_buf = malloc(4096);
    if (!rx_buf) {
        ESP_LOGE(TAG, "Failed to allocate net rx buffer");
        vTaskDelete(NULL);
        return;
    }

    for (int i = 0; i < 4; i++) {
        s_listen_fds[i] = create_server_socket(s_ports[i], (i == 0));
    }

    /* Initialize UDP Telemetry Streaming Socket (Port 3242) */
    s_udp_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s_udp_fd >= 0) {
        int opt = 1;
        setsockopt(s_udp_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
        int sndbuf = 65536;
        setsockopt(s_udp_fd, SOL_SOCKET, SO_SNDBUF, &sndbuf, sizeof(sndbuf));
        int tos = 0xB8; /* DSCP EF (Lowest latency QoS) */
        setsockopt(s_udp_fd, IPPROTO_IP, IP_TOS, &tos, sizeof(tos));

        struct sockaddr_in udp_bind_addr;
        memset(&udp_bind_addr, 0, sizeof(udp_bind_addr));
        udp_bind_addr.sin_family = AF_INET;
        udp_bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);
        udp_bind_addr.sin_port = htons(PORT_DAP_TRACE);

        if (bind(s_udp_fd, (struct sockaddr *)&udp_bind_addr, sizeof(udp_bind_addr)) == 0) {
            ESP_LOGI(TAG, "Listening on UDP port %d (RTP Zero-RTT Stream)", PORT_DAP_TRACE);
        } else {
            ESP_LOGE(TAG, "UDP bind failed for port %d", PORT_DAP_TRACE);
        }
    }

    while (1) {
        fd_set readfds;
        FD_ZERO(&readfds);
        int max_fd = -1;

        for (int i = 0; i < 4; i++) {
            if (s_listen_fds[i] >= 0) {
                FD_SET(s_listen_fds[i], &readfds);
                if (s_listen_fds[i] > max_fd) max_fd = s_listen_fds[i];
            }
            if (s_client_fds[i] >= 0) {
                FD_SET(s_client_fds[i], &readfds);
                if (s_client_fds[i] > max_fd) max_fd = s_client_fds[i];
            }
        }

        if (s_udp_fd >= 0) {
            FD_SET(s_udp_fd, &readfds);
            if (s_udp_fd > max_fd) max_fd = s_udp_fd;
        }

        struct timeval tv = {.tv_sec = 1, .tv_usec = 0};
        int sel = select(max_fd + 1, &readfds, NULL, NULL, &tv);
        if (sel <= 0) continue;

        /* Check UDP registration ping */
        if (s_udp_fd >= 0 && FD_ISSET(s_udp_fd, &readfds)) {
            struct sockaddr_in from_addr;
            socklen_t from_len = sizeof(from_addr);
            int n = recvfrom(s_udp_fd, rx_buf, 4096, 0, (struct sockaddr *)&from_addr, &from_len);
            if (n > 0) {
                s_udp_target_addr = from_addr;
                s_udp_target_active = true;
                ESP_LOGI(TAG, "UDP stream registered via incoming datagram from [%s:%d]",
                         inet_ntoa(from_addr.sin_addr), ntohs(from_addr.sin_port));
            }
        }

        /* Check new connections */
        for (int i = 0; i < 4; i++) {
            if (s_listen_fds[i] >= 0 && FD_ISSET(s_listen_fds[i], &readfds)) {
                struct sockaddr_in client_addr;
                socklen_t client_len = sizeof(client_addr);
                int client = accept(s_listen_fds[i], (struct sockaddr *)&client_addr, &client_len);
                if (client >= 0) {
                    if (s_client_fds[i] >= 0) {
                        close(s_client_fds[i]);
                    }
                    int nodelay = 1;
                    setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
                    s_client_fds[i] = client;
                    ESP_LOGI(TAG, "Client connected to port %d [%s:%d]",
                             s_ports[i], inet_ntoa(client_addr.sin_addr), ntohs(client_addr.sin_port));

                    /* If control channel connected, auto-register client as UDP streaming target */
                    if (i == 0) {
                        s_udp_target_addr.sin_family = AF_INET;
                        s_udp_target_addr.sin_addr = client_addr.sin_addr;
                        s_udp_target_addr.sin_port = htons(PORT_DAP_TRACE);
                        s_udp_target_active = true;
                        ESP_LOGI(TAG, "Auto-configured UDP Telemetry stream destination: %s:%d",
                                 inet_ntoa(s_udp_target_addr.sin_addr), ntohs(s_udp_target_addr.sin_port));
                    }
                }
            }
        }

        /* Check client data */
        for (int i = 0; i < 4; i++) {
            if (s_client_fds[i] >= 0 && FD_ISSET(s_client_fds[i], &readfds)) {
                int n = recv(s_client_fds[i], rx_buf, 4096, 0);
                if (n > 0) {
                    if (s_rx_cb) {
                        s_rx_cb(s_channels[i], rx_buf, n);
                    }
                } else if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) {
                    ESP_LOGI(TAG, "Client disconnected from port %d", s_ports[i]);
                    close(s_client_fds[i]);
                    s_client_fds[i] = -1;
                }
            }
        }
    }
}

esp_err_t net_sockets_init(void) {
    xTaskCreate(net_server_task, "net_server", 4096, NULL, 5, NULL);
    return ESP_OK;
}

int net_sockets_send(ufmf_channel_t ch, const uint8_t *data, size_t len) {
    /* Route high-speed Telemetry Stream (Trace channel) over zero-RTT UDP */
    if (ch == UFMF_CH_TRACE && s_udp_fd >= 0 && s_udp_target_active) {
        return sendto(s_udp_fd, data, len, 0, (struct sockaddr *)&s_udp_target_addr, sizeof(s_udp_target_addr));
    }

    int idx = -1;
    for (int i = 0; i < 4; i++) {
        if (s_channels[i] == ch) {
            idx = i;
            break;
        }
    }
    if (idx < 0 || s_client_fds[idx] < 0) return -1;
    return send(s_client_fds[idx], data, len, 0);
}

bool net_sockets_client_connected(ufmf_channel_t ch) {
    if (ch == UFMF_CH_TRACE && s_udp_target_active) return true;
    int idx = -1;
    for (int i = 0; i < 4; i++) {
        if (s_channels[i] == ch) {
            idx = i;
            break;
        }
    }
    return (idx >= 0 && s_client_fds[idx] >= 0);
}
