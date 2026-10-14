# ⚡ Wireless-Probe: 超高速 Wi-Fi 6 无线 CMSIS-DAP 调试器

[![Platform](https://img.shields.io/badge/Platform-RP2350%20%2B%20ESP32--C5-blue.svg)](https://github.com/MekCraftLi/wireless-probe)
[![Wi-Fi 6](https://img.shields.io/badge/Wireless-Wi--Fi%206%20(802.11ax)-green.svg)](https://github.com/MekCraftLi/wireless-probe)
[![Protocol](https://img.shields.io/badge/Debug%20Protocol-CMSIS--DAP%20v2-orange.svg)](https://github.com/MekCraftLi/wireless-probe)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

**Wireless-Probe** 是一款基于 **树莓派 Pico 2 (RP2350)** 与 **乐鑫 ESP32-C5 (Wi-Fi 6)** 双芯架构的高性能、超低延迟无线调试器。专为嵌入式开发者解决传统有线调试器线缆缠绕、目标板随运动机构移动、高压隔离调试等痛点设计。

调试器内部通过 **4-bit Quad-SPI (6 线) + 硬件流控** 实现两芯片间的微秒级数据搬运，对外通过 **Wi-Fi 6 (802.11ax)** 提供最高 1201 Mbps 吞吐与 <1ms 往返延迟，全面支持各种 ARM Cortex-M 目标主控（STM32、RP2040/RP2350、NXP、GD32 等）。

---

## 🌟 核心特性

- 🚀 **超低延迟 Wi-Fi 6 通信**：实测网络 RTT 低至 **0.8 ms**，单步调试毫无顿挫感。
- ⚡ **4-bit Quad-SPI 硬件级总线**：RP2350 PIO 驱动 4-bit QSPI，搭配 ESP32-C5 GDMA 链表双缓冲，错误率 **0.00%**。
- 🛠️ **原生 upstream CMSIS-DAP v2 核心**：基于 ARM 官方标准协议，免驱支持所有主流 IDE。
- 🔄 **硬件级 SWD 引擎**：Pico 2 PIO 单周期半双工时序，最高支持 25MHz SWD 时钟，支持硬件复位下连接 (`connect_assert_srst`)。
- 📡 **多通道高速复用 (UFMF 协议)**：
  - **`TCP 3240`**：CMSIS-DAP 控制通道（SWD 指令、寄存器读写、断点）
  - **`TCP 3241`**：Bulk 高速数据通道（大块内存读写、Flash 固件流式烧录）
  - **`TCP 3242`**：实时 Trace 数据流通道
  - **`TCP 3243`**：无线虚拟串口（接收目标芯片 `printf` 实时打印）
- 🔋 **随身无线便携**：单 Type-C 或锂电池供电即可工作。

---

## 📐 硬件接线拓扑 (Hardware Pinout)

### 1. Pico 2 与 ESP32-C5 互联引脚 (QSPI 内部总线)

```
       [Pico 2 (RP2350)]                               [ESP32-C5]
      +-------------------+                           +-------------------+
      | GPIO 16 (Pin 21)  | ------------------------> | GPIO 4  (J3 Pin 8)| (SCLK 时钟线)
      | GPIO 17 (Pin 22)  | ------------------------> | GPIO 5  (J3 Pin 9)| (CS 片选线)
      | GPIO 18 (Pin 24)  | <-----------------------> | GPIO 6  (J1 Pin 7)| (D0 数据线 0)
      | GPIO 19 (Pin 25)  | <-----------------------> | GPIO 23 (J3 Pin 5)| (D1 数据线 1)
      | GPIO 20 (Pin 26)  | <-----------------------> | GPIO 8  (J1 Pin 9)| (D2 数据线 2)
      | GPIO 21 (Pin 27)  | <-----------------------> | GPIO 9  (J1 Pin 10| (D3 数据线 3)
      |                   |                           |                   |
      | GPIO 22 (Pin 29)  | <------------------------ | GPIO 10 (J1 Pin 11| (READY 中断就绪线)
      | GPIO 26 (Pin 31)  | ------------------------> | GPIO 13 (J3 Pin 14| (REQ 请求发送线)
      |                   |                           |                   |
      | GND (Pin 23/28/38)| ------------------------> | GND (公共参考地)  | (★必须连接★)
      +-------------------+                           +-------------------+
```

### 2. 调试器连向目标板 (Target SWD)

```
     [无线调试器 Pico 2 端]                 [目标芯片 (如 STM32 / NXP / GD32)]
    +----------------------+              +-------------------------------+
    | Pin 4 (GPIO 2)       | -----------> | SWCLK (如 PA14)               |
    | Pin 5 (GPIO 3)       | <----------> | SWDIO (如 PA13)               |
    | Pin 2 (GPIO 1)       | -----------> | NRST  (硬件复位，强烈推荐连接)  |
    | Pin 3 (GND)          | -----------> | GND   (★必须共地★)             |
    | Pin 6 (GPIO 4 / TX)  | -----------> | Target RX (可选)              |
    | Pin 7 (GPIO 5 / RX)  | <----------- | Target TX (无线串口监视)       |
    +----------------------+              +-------------------------------+
```

> 更多硬件接线细节与引脚说明，请阅读 [docs/hardware_wiring.md](docs/hardware_wiring.md)。

---

## 🚀 快速上手 (Quick Start)

### 步骤 1：烧录预编译固件
仓库的 `binaries/` 目录下提供了开箱即用的固件文件，直接烧录即可：

1. **烧录 Pico 2 固件**：
   - 按住 Pico 2 上的白色 `BOOTSEL` 键插入电脑。
   - 将 [`binaries/pico2/debugprobe_on_pico2.uf2`](binaries/pico2/debugprobe_on_pico2.uf2) 直接复制到电脑识别出的 `RP2350` 磁盘中。
2. **烧录 ESP32-C5 固件**：
   - 使用 `esptool.py` 一键刷入：
     ```bash
     esptool.py -c esp32c5 write_flash 0x2000 binaries/esp32c5/bootloader.bin \
                                       0x8000 binaries/esp32c5/partition-table.bin \
                                       0x10000 binaries/esp32c5/c5-wireless-bridge.bin
     ```

### 步骤 2：连接 Wi-Fi 热点
调试器通电启动后，搜索并连接其建立的 Wi-Fi 热点：
* **SSID**：`Mek-Wireless-Probe`
* **密码**：`12345678`
* **调试器默认 IP**：`192.168.4.1`

### 步骤 3：验证连通性
在主机运行自动化测试脚本：
```bash
# 1. 查询调试器基本信息
python tools/test_dap_info.py 192.168.4.1

# 2. 识别目标芯片 (读取 DPIDR)
python tools/test_swd_target.py 192.168.4.1

# 3. 访问芯片内核寄存器 (读取 Cortex-M CPUID)
python tools/test_memory_read.py 192.168.4.1

# 4. 监听目标芯片串口输出
python tools/wireless_uart.py 192.168.4.1
```

---

## 🛠️ 二次开发环境搭建 (Secondary Development)

本仓库采用微服务式的双固件源码结构：

```
wireless-probe/
├── docs/                          # 详细硬件连线与协议设计文档
├── firmware/
│   ├── pico2/                     # Pico 2 固件 (CMake + Pico SDK 2.3.1 + FreeRTOS)
│   └── esp32c5/                   # ESP32-C5 固件 (ESP-IDF v5.5)
├── binaries/                      # 预编译固件 (开箱即用)
└── tools/                         # 上位机桥接代理与测试脚本
```

### 1. Pico 2 固件编译
```bash
cd firmware/pico2
cmake -B build -DPICO_BOARD=pico2 -GNinja
ninja -C build
```
生成文件位于 `build/debugprobe_on_pico2.uf2`。

### 2. ESP32-C5 固件编译
```bash
cd firmware/esp32c5
idf.py set-target esp32c5
idf.py build
```

> 详细的工具链版本要求、调试技巧与二次开发指导，请参阅 [docs/development_guide.md](docs/development_guide.md)。

---

## 🔌 上位机 IDE 接入指引

### 1. VS Code / OpenOCD / GDB
在主机后台运行轻量级转发代理：
```bash
python tools/dap_tcp_bridge.py 192.168.4.1
```
随后启动 OpenOCD 即可直接连接：
```bash
openocd -f interface/cmsis-dap.cfg -f target/stm32h7x.cfg -c "adapter speed 10000; init; reset halt"
```

### 2. Keil MDK / STM32CubeIDE / CLion
配合 USBIP 或网络 GDB 服务，可在 Keil 中直接选择 **CMSIS-DAP Debugger**，设备列表中将自动显示 `Debugprobe on Pico (CMSIS-DAP)`，支持 F8 编译下载与源码级单步调试。

### 3. 无线串口监视
在终端直接连接 TCP 3243：
```bash
nc 192.168.4.1 3243
# 或运行 python tools/wireless_uart.py 192.168.4.1
```

---

## 📜 开源协议

本项目采用 [MIT 许可证](LICENSE) 开源。欢迎提交 Issue 与 Pull Request！
