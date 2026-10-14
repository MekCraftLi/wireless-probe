# 二次开发与环境配置指南 (Secondary Development Guide)

本文档面向希望在现有代码基础上进行功能定制、固件重编译或协议扩展的开发者。

---

## 一、 系统架构总览

```
+-------------------------------------------------------------------------------+
|                                  上位机开发环境                                 |
|   OpenOCD / Keil MDK / STM32CubeIDE / PyOCD / GDB / VS Code Cortex-Debug      |
+-------------------------------------------------------------------------------+
                                        ^
                                        | (TCP/IP over Wi-Fi 6, 802.11ax)
                                        v
+-------------------------------------------------------------------------------+
|                      ESP32-C5 桥接固件 (firmware/esp32c5/)                     |
|  - Wi-Fi 6 AP/STA 管理 (wifi_manager.c)                                        |
|  - 多端口非阻塞 Socket 服务 (net_sockets.c: 3240, 3241, 3242, 3243)             |
|  - QSPI Slave Segment Mode + GDMA 双缓冲驱动 (qspi_slave_gdma.c)              |
+-------------------------------------------------------------------------------+
                                        ^
                                        | (4-bit Quad-SPI, 6.25MHz ~ 50MHz)
                                        v
+-------------------------------------------------------------------------------+
|                       Pico 2 RP2350 固件 (firmware/pico2/)                    |
|  - PIO1 Quad-SPI Master 状态机驱动 (src/qspi.pio, src/qspi_transport.c)        |
|  - FreeRTOS 任务调度 (Core 0: USB/UART, Core 1: QSPI + SWD)                  |
|  - 官方 upstream CMSIS-DAP v2 协议核心 (CMSIS_DAP/CMSIS/DAP/Firmware/)        |
|  - PIO0 硬件级单周期 SWD 引擎 (src/probe.pio, src/sw_dp_pio.c)                 |
+-------------------------------------------------------------------------------+
                                        ^
                                        | (SWD: SWCLK, SWDIO, RESET)
                                        v
+-------------------------------------------------------------------------------+
|                           目标主控芯片 (Target MCU)                            |
|             STM32H7 / STM32F4 / STM32F1 / RP2040 / RP2350 / NXP               |
+-------------------------------------------------------------------------------+
```

---

## 二、 Pico 2 (RP2350) 固件二次开发环境

### 1. 必备工具链与 SDK
* **操作系统**：Windows 10/11, Ubuntu 22.04+, 或 macOS
* **Pico SDK**：**v2.3.1**（RP2350 必须使用 Pico SDK 2.x 以上版本）
* **交叉编译器**：`arm-none-eabi-gcc` (推荐 GCC 14 或以上)
* **构建系统**：`CMake` (>= 3.13) + `Ninja`
* **烧录工具**：`picotool` (v2.3.1+)

### 2. 获取代码与子模块
```bash
git clone --recurse-submodules https://github.com/MekCraftLi/wireless-probe.git
cd wireless-probe/firmware/pico2
```

### 3. 编译步骤
```bash
# 生成 Ninja 构建文件 (指定目标开发板为 pico2)
cmake -B build -DPICO_BOARD=pico2 -GNinja

# 执行编译
ninja -C build
```
编译完成后，会在 `build/` 目录下生成：
* `debugprobe_on_pico2.elf`：ELF 调试文件
* `debugprobe_on_pico2.uf2`：可直接拖拽烧录的 UF2 固件

### 4. 烧录方式
* **方式 A（拖拽刷入）**：按住 Pico 2 的 `BOOTSEL` 按键插入 USB，将 `debugprobe_on_pico2.uf2` 拖入 `RP2350` 磁盘。
* **方式 B（命令行一键刷入）**：
  ```powershell
  # 如果 Pico 2 处于运行状态，自动重启并刷入
  picotool reboot -u -f --vid 0x2e8a --pid 0x000c
  picotool load -x -v build/debugprobe_on_pico2.uf2
  ```

---

## 三、 ESP32-C5 桥接固件二次开发环境

### 1. 必备工具链与 SDK
* **ESP-IDF**：**v5.5** (推荐分支 `release/v5.5`，全面支持 ESP32-C5 RISC-V 架构)
* **编译器**：`riscv32-esp-elf-gcc` (IDF 自动管理)
* **Python**：Python 3.10 ~ 3.12 虚拟环境

### 2. 编译步骤
在 Linux 开发机或配置好 ESP-IDF 的 Windows 环境中：
```bash
cd wireless-probe/firmware/esp32c5

# 激活 ESP-IDF 环境
. $HOME/export.sh   # 或在 Windows 下运行 export.bat

# 设置目标芯片为 ESP32-C5
idf.py set-target esp32c5

# 执行构建
idf.py build
```

### 3. 烧录与串口监视
```bash
# 自动检测串口并烧录 (以 /dev/ttyACM0 为例)
idf.py -p /dev/ttyACM0 flash monitor
```
或者使用随仓库附带的预编译固件进行快速脱机烧录：
```bash
esptool.py -c esp32c5 write_flash 0x2000 binaries/esp32c5/bootloader.bin \
                                  0x8000 binaries/esp32c5/partition-table.bin \
                                  0x10000 binaries/esp32c5/c5-wireless-bridge.bin
```

---

## 四、 核心代码修改与定制指引

### 1. 修改 Wi-Fi 名称、密码或频段
打开 `firmware/esp32c5/main/wifi_manager.h`：
```c
#define PROBE_WIFI_AP_SSID      "Mek-Wireless-Probe"  // 热点名称
#define PROBE_WIFI_AP_PASS      "12345678"            // 热点密码
#define PROBE_WIFI_AP_CHANNEL   6                     // 默认信道 (可修改为 5GHz 信道)
```

### 2. 调整 QSPI 总线时钟频率
打开 `firmware/pico2/src/qspi_transport.c`：
```c
const float clkdiv = 6.0f; // 默认 ~6.25MHz (杜邦线最安全)
// 若使用集成 PCB 底板，可将 clkdiv 调整为 2.0f (~18.75MHz) 或 1.0f (~37.5MHz) 以获得极致吞吐
```

### 3. 修改 SWD 引脚定义
打开 `firmware/pico2/include/board_pico_config.h`：
```c
#define PROBE_PIN_OFFSET 2
#define PROBE_PIN_SWCLK (PROBE_PIN_OFFSET + 0) // 默认 GPIO 2
#define PROBE_PIN_SWDIO (PROBE_PIN_OFFSET + 1) // 默认 GPIO 3
#define PROBE_PIN_RESET 1                      // 默认 GPIO 1
```

---

## 五、 全链路功能自动化验证

在 `tools/` 目录下提供了完整的 Python 自动化测试套件：
1. **测试 DAP 固件协议握手**：
   ```bash
   python tools/test_dap_info.py 192.168.4.1
   ```
2. **测试目标芯片 SWD 识别 (DPIDR)**：
   ```bash
   python tools/test_swd_target.py 192.168.4.1
   ```
3. **测试芯片系统总线内存与 CPUID 访问**：
   ```bash
   python tools/test_memory_read.py 192.168.4.1
   ```
4. **无线串口监视**：
   ```bash
   python tools/wireless_uart.py 192.168.4.1
   ```
