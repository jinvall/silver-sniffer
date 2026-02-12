#include <stdio.h>
#include <string.h>
#include <inttypes.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "esp_timer.h"

#include "mbedtls/base64.h"

#define WIFI_CHANNEL_MIN 1
#define WIFI_CHANNEL_MAX 13
#define CHANNEL_HOP_INTERVAL_MS 500

#define SNIFFER_QUEUE_LEN 128
#define MAX_FRAME_LEN 512

typedef struct {
    int64_t ts_us;
    int8_t  rssi;
    uint8_t channel;
    uint16_t len;
    uint8_t bssid[6];
    char    ssid[33];           // null-terminated SSID (max 32 bytes)
    uint8_t frame[MAX_FRAME_LEN];
} sniffer_frame_t;

static uint8_t current_channel = WIFI_CHANNEL_MIN;
static QueueHandle_t sniffer_queue = NULL;

static void mac_to_str(const uint8_t *mac, char *buf, size_t len)
{
    if (len < 18) return;
    snprintf(buf, len, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static void oui_to_vendor(const uint8_t *mac, char *buf, size_t len)
{
    if (len == 0) return;

    uint32_t oui = ((uint32_t)mac[0] << 16) |
                   ((uint32_t)mac[1] << 8)  |
                   ((uint32_t)mac[2]);

    const char *vendor = "Unknown";

    switch (oui) {
        case 0xF4F5E8: vendor = "Apple";      break;
        case 0xD85D4C: vendor = "Ubiquiti";   break;
        case 0xF0B429: vendor = "Google";     break;
        case 0xB827EB: vendor = "RaspberryPi";break;
        case 0x00163E: vendor = "Cisco";      break;
        case 0xF8D111: vendor = "TP-Link";    break;
        case 0x001E2A: vendor = "Netgear";    break;
        default:       vendor = "Unknown";    break;
    }

    strncpy(buf, vendor, len - 1);
    buf[len - 1] = '\0';
}

static void wifi_sniffer_packet_handler(void *buf, wifi_promiscuous_pkt_type_t type)
{
    const wifi_promiscuous_pkt_t *ppkt = (wifi_promiscuous_pkt_t *)buf;
    const wifi_pkt_rx_ctrl_t *rx = &ppkt->rx_ctrl;
    const uint8_t *payload = ppkt->payload;
    uint16_t len = rx->sig_len;

    if (len == 0 || len > MAX_FRAME_LEN) return;

    sniffer_frame_t frame;
    memset(&frame, 0, sizeof(frame));

    frame.ts_us = esp_timer_get_time();
    frame.rssi = rx->rssi;
    frame.channel = rx->channel;
    frame.len = len;
    memcpy(frame.frame, payload, len);
    frame.ssid[0] = '\0';  // default: hidden / unknown

    // -----------------------------
    // BSSID extraction
    // -----------------------------
    if (len >= 24) {
        uint16_t fc = payload[0] | (payload[1] << 8);
        uint8_t fc_type = (fc >> 2) & 0x3;
        uint8_t to_ds = (fc >> 8) & 0x1;
        uint8_t from_ds = (fc >> 9) & 0x1;

        const uint8_t *addr1 = payload + 4;
        const uint8_t *addr2 = payload + 10;
        const uint8_t *addr3 = payload + 16;
        const uint8_t *bssid = NULL;

        if (fc_type == 0) {
            // management
            bssid = addr3;
        } else if (fc_type == 2) {
            // data
            if (!to_ds && !from_ds) {
                bssid = addr2;
            } else if (!to_ds && from_ds) {
                bssid = addr2;
            } else if (to_ds && !from_ds) {
                bssid = addr1;
            }
        }

        if (bssid) {
            memcpy(frame.bssid, bssid, 6);
        }

        // -----------------------------
        // SSID extraction (Mgmt frames)
        // Beacon (subtype 8)
        // Probe Response (subtype 5)
        // Probe Request (subtype 4)
        // -----------------------------
        uint8_t fc_subtype = (fc >> 4) & 0xF;

        if (fc_type == 0) {
            int offset = 24; // mgmt header

            if (fc_subtype == 8 || fc_subtype == 5) {
                // Beacon / Probe Response: header (24) + fixed params (12)
                offset = 24 + 12;
            } else if (fc_subtype == 4) {
                // Probe Request: header only, tagged params start at 24
                offset = 24;
            }

            while (offset + 2 < len) {
                uint8_t tag = payload[offset];
                uint8_t tag_len = payload[offset + 1];

                if (offset + 2 + tag_len > len) {
                    break; // malformed
                }

                if (tag == 0) { // SSID element
                    if (tag_len > 0 && tag_len < 33) {
                        memcpy(frame.ssid, &payload[offset + 2], tag_len);
                        frame.ssid[tag_len] = '\0';
                    } else {
                        strcpy(frame.ssid, "(hidden)");
                    }
                    break;
                }

                offset += 2 + tag_len;
            }
        }
    }

    if (sniffer_queue) {
        (void)xQueueSend(sniffer_queue, &frame, 0);
    }
}

static void sniffer_output_task(void *arg)
{
    sniffer_frame_t frame;
    char bssid_str[18];
    char vendor_str[32];

    uint8_t b64_buf[4 * ((MAX_FRAME_LEN + 2) / 3)];
    size_t b64_len = 0;

    for (;;) {
        if (xQueueReceive(sniffer_queue, &frame, portMAX_DELAY) == pdTRUE) {
            mac_to_str(frame.bssid, bssid_str, sizeof(bssid_str));
            oui_to_vendor(frame.bssid, vendor_str, sizeof(vendor_str));

            b64_len = sizeof(b64_buf);
            int ret = mbedtls_base64_encode(
                b64_buf, b64_len, &b64_len,
                frame.frame, frame.len
            );
            if (ret != 0) {
                continue;
            }

            b64_buf[b64_len] = '\0';

            printf(
                "{"
                "\"type\":\"wifi\","
                "\"ts_us\":%" PRId64 ","
                "\"rssi\":%d,"
                "\"channel\":%u,"
                "\"frame_len\":%u,"
                "\"bssid\":\"%s\","
                "\"ssid\":\"%s\","
                "\"vendor\":\"%s\","
                "\"frame_b64\":\"%s\""
                "}\n",
                frame.ts_us,
                frame.rssi,
                frame.channel,
                (unsigned)frame.len,
                bssid_str,
                frame.ssid[0] ? frame.ssid : "(hidden)",
                vendor_str,
                (char *)b64_buf
            );
            fflush(stdout);
        }
    }
}

static void channel_hop_task(void *arg)
{
    while (1) {
        current_channel++;
        if (current_channel > WIFI_CHANNEL_MAX) {
            current_channel = WIFI_CHANNEL_MIN;
        }
        esp_wifi_set_channel(current_channel, WIFI_SECOND_CHAN_NONE);
        vTaskDelay(pdMS_TO_TICKS(CHANNEL_HOP_INTERVAL_MS));
    }
}

static void wifi_sniffer_init(void)
{
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_NULL));
    ESP_ERROR_CHECK(esp_wifi_start());

    wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT | WIFI_PROMIS_FILTER_MASK_DATA
    };

    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&filter));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(&wifi_sniffer_packet_handler));

    current_channel = WIFI_CHANNEL_MIN;
    ESP_ERROR_CHECK(esp_wifi_set_channel(current_channel, WIFI_SECOND_CHAN_NONE));

    xTaskCreatePinnedToCore(channel_hop_task, "channel_hop_task", 4096, NULL, 5, NULL, 0);
    xTaskCreatePinnedToCore(sniffer_output_task, "sniffer_output_task", 6144, NULL, 4, NULL, 1);
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    sniffer_queue = xQueueCreate(SNIFFER_QUEUE_LEN, sizeof(sniffer_frame_t));
    wifi_sniffer_init();
}

