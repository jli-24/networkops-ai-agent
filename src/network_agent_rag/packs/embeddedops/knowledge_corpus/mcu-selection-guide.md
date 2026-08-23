# MCU 选型指南

## 选型维度

选择 MCU 时需要评估以下维度：

1. 主频与算力：简单传感器采集 80MHz 足够；边缘推理需要 240MHz 以上。
2. 内存：FreeRTOS + Wi-Fi 协议栈至少需要 300KB RAM；裸机采集任务 32KB 即可。
3. 外设接口：确认 SPI/I2C/UART/CAN 控制器数量是否满足外设数量需求。
4. 无线能力：Wi-Fi/BLE/LoRa 根据组网距离与功耗预算选择。
5. 生态与工具链：Arduino Framework 上手快；ESP-IDF/Zephyr 适合量产与 RTOS 需求。
6. 供货与成本：BOM 成本按千件价格核算。

## 常见型号对比

| 型号 | 主频 | RAM | Flash | 无线 | 适合场景 |
| --- | --- | --- | --- | --- | --- |
| ESP32-S3 | 240MHz | 512KB | 8-16MB | Wi-Fi + BLE | IoT 节点、边缘 AI |
| ESP32 | 240MHz | 520KB | 4MB | Wi-Fi + BLE | Wi-Fi 采集节点 |
| STM32F103 | 72MHz | 20-64KB | 64-512KB | 无 | 工业 PLC、CAN 节点 |
| STM32F407 | 168MHz | 192KB | 1MB | 无 | 电机控制、数字电源 |
| nRF52840 | 64MHz | 256KB | 1MB | BLE | 低功耗蓝牙传感 |
| Raspberry Pi RP2040 | 133MHz | 264KB | 外置 | 无 | 教育、低成本控制 |
| Arduino Nano (ATmega328P) | 16MHz | 2KB | 32KB | 无 | 原型验证、简单控制 |

## 选型决策规则

- 需要 Wi-Fi 上云 → ESP32 / ESP32-S3。
- 需要 CAN 总线与工业环境 → STM32F103 / STM32F407。
- 电池供电且只传 BLE → nRF52840。
- 教学 Demo 或快速原型 → Arduino / RP2040。
- 电机控制需要高级定时器 → STM32F407。

## BOM 生成要求

hardware_design.json 中的 BOM 必须包含：位号、器件名、封装、数量、单价（千件）、替代料。传感器与通信模组必须列出供电电压与接口类型。
