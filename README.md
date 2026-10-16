# Wireless-Probe: 超高速 Wi-Fi 6 无线 CMSIS-DAP 调试器 & 智能流式示波采集仪

[![Platform](https://img.shields.io/badge/Platform-RP2350%20%2B%20ESP32--C5-blue.svg)](https://github.com/MekCraftLi/wireless-probe)
[![Wi-Fi 6](https://img.shields.io/badge/Wireless-Wi--Fi%206%20(802.11ax)-green.svg)](https://github.com/MekCraftLi/wireless-probe)
[![Protocol](https://img.shields.io/badge/Debug%20Protocol-CMSIS--DAP%20v2-orange.svg)](https://github.com/MekCraftLi/wireless-probe)
[![Sampling](https://img.shields.io/badge/Max%20Sampling-13.6k%20Sa%2Fs%20%7C%20217k%20Words%2Fs-red.svg)](docs/special_interfaces_spec.md)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

**Wireless-Probe** 是一款基于 **树莓派 Pico 2 (RP2350)** 与 **乐鑫 ESP32-C5 (Wi-Fi 6 协处理器)** 双芯架构的高性能、超低延迟复合型无线调试器与数据采集仪。它不仅支持标准的 ARM CMSIS-DAP v2 调试协议（单步、断点、固件无线烧录），更突破性地实现了下沉式**硬件级流式自主采样（Autonomous Streaming）引擎**，解决了传统无线调试器无法高速观测变量波形的物理瓶颈。

调试器内部通过 **4-bit Quad-SPI + 硬件流控** 实现两芯片间的微秒级数据搬运，对外通过 **Wi-Fi 6 (802.11ax)** 提供最高 1201 Mbps 吞吐与 <1ms 往返延迟，全面支持各种 ARM Cortex-M 目标主控（STM32、RP2040/RP2350、NXP、GD32 等）。

---

## 核心特性与技术亮点

### 1. 双重功能定位：标准调试器 + 实时示波记录仪
- **标准调试器模式 (100% 兼容 CMSIS-DAP v2)**：有线 USB 免驱即插即用；无线 TCP 模式免驱透传。无缝接入 Keil MDK、IAR、OpenOCD、pyOCD、CLion、VS Code 等主流 IDE，支持 Flash 固件快速烧录与源码级断点单步。
- **高速流式自主采集模式 (Smart Telemetry Engine)**：摆脱传统调试器“一问一答”受制于 Wi-Fi 往返时延的困境，将定拍逻辑下沉至 RP2350 硬件定时器，通过 **UDP 3242** 端口向外推流，采样率高达 **2,000 ~ 13,600+ Sa/s**。

### 2. 双模式高精度采样架构
- **模式一：自主定拍模式（Configurable Pacing）**：
  - 上位机灵活指定采样率（如 2,000 Hz、5,000 Hz 等）；
  - Pico 2 硬件绝对微秒累加定拍（`next_sample_time_us += target_period_us`），实现**真正 0.00 μs 累积漂移**与 <= 1 μs 晶振级抖动控制。
- **模式二：物理线速饱和模式（Wire-Speed Saturation）**：
  - 0 延时全速吞吐。2.0 MHz SWD 时钟下达 **2,717 Sa/s**（43,478 Words/s）；10.0 MHz SWD 时钟下达 **13,698 Sa/s**（**21.7 万字/秒**）。
- **目标单片机零代码侵入（Zero Intrusion）**：
  - 目标 MCU **无需编写任何驱动、无需插入时间戳代码、无需占用单片机中断资源**；
  - 微秒真值时间戳 100% 由探针硬件定时器在 SWD 物理采样瞬间写入 RFC 3550 对齐的 12 字节 RTP 帧头。

### 3. 先进硬件与通信架构
- **4-bit Quad-SPI 协处理总线**：RP2350 PIO 状态机驱动 4-bit QSPI，搭配 ESP32-C5 GDMA 链表流控，通道错误率 **0.00%**。
- **多通道网络服务矩阵 (UFMF 协议栈)**：
  - **`TCP 80`**：**Web 网页配网中心**（响应式网页控制台，支持周边 Wi-Fi 扫描、STA 配置、状态监控与出厂重置）
  - **`TCP 3240`**：CMSIS-DAP 控制通道 + Vendor 扩展自主控制命令（SETUP / PLAY / TEARDOWN / GET_STATS）
  - **`TCP 3241`**：Bulk 高速数据通道（大块内存读写、Flash 固件流式烧录）
  - **`UDP 3242`**：**RTP 零 RTT 高速遥测流通道（微秒时间戳 + 采样数据包）**
  - **`TCP 3243`**：目标 MCU 无线虚拟串口（实时 `printf` 调试输出）

---

## 采样性能基准与时间戳时序实测

在 Wi-Fi 6（802.11ax）空中无线链路下，使用实测工具采集超过 29,000 帧样本的统计指标如下：

| 测试模式 / 规格 | 目标采样率 / 周期 | 实测有效采样率 | 底层总线词率 (Words/s) | 平均采样周期 (Delta t) | 时钟抖动 P90 / P99 | 数据完整性 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **模式一: 2,000 Hz 自主定拍** | 2,000 Sa/s (500.0 μs) | **2,000 Sa/s** | 32,000 Words/s | **500.00 μs** | 1.00 μs / 1.00 μs | 100.0% PASS |
| **模式一: 5,000 Hz 自主定拍** | 5,000 Sa/s (200.0 μs) | **5,000 Sa/s** | 80,000 Words/s | **200.00 μs** | 1.00 μs / 1.00 μs | 100.0% PASS |
| **模式二: 2 MHz SWD 线速饱和** | 2,717 Sa/s (368.1 μs) | **2,717 Sa/s** | **43,472 Words/s** | **368.02 μs** | 1.05 μs / 1.05 μs | 100.0% PASS |
| **模式二: 10 MHz SWD 线速饱和** | 13,586 Sa/s (73.6 μs) | **13,698 Sa/s** | **217,376 Words/s** | **73.01 μs** | 1.61 μs / 1.61 μs | 100.0% PASS |
| **实测: Wi-Fi 空中硬件 UDP 抓包** | 实机空中抓包 | 76 Sa/s (物理挂空状态) | — | 包含 500 μs 硬件步进 | 零丢帧校验 | 100.0% PASS |

> 完整时间戳数据集已归档于 [`test_results/benchmark_timestamps_record.csv`](test_results/benchmark_timestamps_record.csv)。

---

## 硬件接线拓扑 (Hardware Pinout)

### 1. Pico 2 与 ESP32-C5 互联引脚 (QSPI 内部总线)

```
       [Pico 2 (RP2350)]                               [ESP32-C5]
      +-------------------+                           +-------------------+
      | GPIO 18 (Pin 24)  | ------------------------> | GPIO 6  (J1 Pin 7)| (QSPI SCLK)
      | GPIO 17 (Pin 22)  | ------------------------> | GPIO 7  (J1 Pin 8)| (QSPI CS)
      | GPIO 19 (Pin 25)  | <-----------------------> | GPIO 2  (J1 Pin 3)| (QSPI D0)
      | GPIO 20 (Pin 26)  | <-----------------------> | GPIO 3  (J1 Pin 4)| (QSPI D1)
      | GPIO 21 (Pin 27)  | <-----------------------> | GPIO 4  (J3 Pin 8)| (QSPI D2)
      | GPIO 22 (Pin 29)  | <-----------------------> | GPIO 5  (J3 Pin 9)| (QSPI D3)
      |                   |                           |                   |
      | GPIO 27 (Pin 32)  | <------------------------ | GPIO 0  (J1 Pin 1)| (C5_RDY 就绪线)
      | GPIO 26 (Pin 31)  | ------------------------> | GPIO 1  (J1 Pin 2)| (P2_REQ 请求线)
      |                   |                           |                   |
      | GND (Pin 23/28/38)| ------------------------> | GND (公共参考地)  | (必须连接)
      +-------------------+                           +-------------------+
```

### 2. 调试器连向目标单片机 (Target SWD)

```
     [无线调试器 Pico 2 端]                 [目标芯片 (如 STM32 / NXP / GD32)]
    +----------------------+              +-------------------------------+
    | Pin 4 (GPIO 2)       | -----------> | SWCLK (如 PA14)               |
    | Pin 5 (GPIO 3)       | <----------> | SWDIO (如 PA13)               |
    | Pin 2 (GPIO 1)       | -----------> | NRST  (硬件复位引脚)          |
    | Pin 3 (GND)          | -----------> | GND   (必须共地)              |
    | Pin 6 (GPIO 4 / TX)  | -----------> | Target RX (可选)              |
    | Pin 7 (GPIO 5 / RX)  | <----------- | Target TX (无线串口监视)       |
    +----------------------+              +-------------------------------+
```

---

## 快速上手 (Quick Start)

### 步骤 1：烧录固件
仓库 `binaries/` 目录提供了开箱即用的预编译固件：
1. **烧录 Pico 2 固件**：按住 Pico 2 的 `BOOTSEL` 键插入电脑，将 [`binaries/pico2/debugprobe_on_pico2.uf2`](binaries/pico2/debugprobe_on_pico2.uf2) 复制到识别出的磁盘中；
2. **烧录 ESP32-C5 固件**：
   ```bash
   esptool.py -c esp32c5 write_flash 0x2000 binaries/esp32c5/bootloader.bin \
                                     0x8000 binaries/esp32c5/partition-table.bin \
                                     0x10000 binaries/esp32c5/c5-wireless-bridge.bin
   ```

### 步骤 2：连接 Wi-Fi 热点
通电后连接探针发出的 Wi-Fi 6 热点：
* **SSID**：`Mek-Wireless-Probe`
* **密码**：`12345678`
* **探针默认 IP**：`192.168.4.1`

### 步骤 3：Web 网页配网接入局域网路由器（可选，推荐）
如果希望调试器加入您现有的家庭/实验室路由器 Wi-Fi（使电脑无需断开外网）：
1. 电脑连入热点后，浏览器访问 `http://192.168.4.1/` 进入配网中心；
2. 点击“扫描周边 Wi-Fi”，在列表选择您的路由器名称并输入密码，点击“保存配置并连接”；
3. 探针自动接入您的局域网并获取 IP（如 `192.168.1.150`），配置自动存入 Flash。电脑切回常规网络即可走局域网 IP 进行调试；
4. **备用入口常驻**：原本的 `Mek-Wireless-Probe` (192.168.4.1) 热点始终常驻开启，您随时可再次连入热点重新修改网络或一键重置。

### 步骤 4：启动实时 Web 可视化看板
```bash
python tools/probe_visualizer.py
```
在浏览器打开 `http://localhost:5000`，即可体验：
* **模式切换**：模式一（2,000 Hz / 5,000 Hz 自主定拍）与模式二（最大线速饱和）一键切换；
* **实时仪表盘**：SWD 时钟频度、瞬时采样率（SPS）、总线词率与总线带宽利用率实时监控；
* **60 FPS 平滑示波**：正弦波、三角波与 PID PV/SP 实时曲线观测。

### 步骤 5：运行高精度时间戳压测套件
```bash
python tools/record_benchmark_performance.py
```
脚本将自动执行全模式基准测试，并记录每一帧样本的 `probe_seq`、微秒时间戳、Delta t 与抖动输出至 CSV。

---

## 技术文档导航 (Documentation)

* **[特殊扩展接口技术规范文档 (docs/special_interfaces_spec.md)](docs/special_interfaces_spec.md)**  
  详细阐述 CMSIS-DAP Vendor 控制平面（0x80~0x83）、UDP 3242 RTP 遥测协议帧结构、UFMF QSPI 协处理协议及 Python 客户端集成代码。
* **[硬件接线拓扑与电气引脚表 (docs/hardware_wiring.md)](docs/hardware_wiring.md)**  
  包含 Pico 2、ESP32-C5 间 6 线 QSPI 详细引脚映射与 SWD 接线注意事项。
* **[UFMF 帧复用协议规范 (docs/protocol_ufmf.md)](docs/protocol_ufmf.md)**  
  板级 4-bit Quad-SPI DMA 链表与双向流控机制详解。
* **[二次开发与固件编译指南 (docs/development_guide.md)](docs/development_guide.md)**  
  基于 Pico SDK 2.3.1 (FreeRTOS) 与 ESP-IDF v5.5 的工程构建说明。

---

## 开源协议

本项目采用 [MIT 许可证](LICENSE) 开源。欢迎提交 Issue 与 Pull Request！
