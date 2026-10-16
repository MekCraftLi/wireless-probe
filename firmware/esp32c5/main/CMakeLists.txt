idf_component_register(SRCS "main.c" "qspi_slave_gdma.c" "wifi_manager.c" "net_sockets.c" "web_server.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_wifi esp_netif nvs_flash esp_driver_spi esp_driver_gpio esp_timer lwip esp_http_server json)
