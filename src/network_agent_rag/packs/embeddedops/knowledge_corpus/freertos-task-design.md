# FreeRTOS 任务设计规范

## 任务划分原则

1. 按功能内聚划分：采集、通信、控制、日志各一个任务。
2. 阻塞操作必须放独立任务，禁止在 idle 任务或定时器回调中阻塞。
3. 任务栈大小通过 `uxTaskGetStackHighWaterMark` 实测后加 30% 余量。
4. 任务间通信用队列（xQueue），禁止全局变量无保护共享。

## 推荐任务架构（传感采集节点）

```
sensor_task      优先级 4  周期 1s    读取 SPI/I2C 传感器
transmit_task    优先级 3  事件驱动   MQTT/HTTP 上报
health_task      优先级 2  周期 30s   心跳、看门狗喂狗
OTA_task         优先级 1  事件驱动   固件升级（空闲时执行）
```

## 优先级反转与互斥

- 共享外设（如同一 I2C 控制器）必须使用互斥锁（xSemaphoreCreateMutex）。
- 互斥锁必须启用优先级继承（FreeRTOS Mutex 默认启用）。
- 临界区（taskENTER_CRITICAL）内禁止任何阻塞调用。

## 常见死锁案例

现象：task A 持有 spi_mutex 等待 i2c_mutex，task B 持有 i2c_mutex 等待 spi_mutex，两个任务全部挂起，看门狗复位。

根因：锁获取顺序不一致。

解决方案：全局规定锁获取顺序（按地址升序或统一先取 spi_mutex）；使用 `xSemaphoreTake` 带超时并在超时后释放已持有的锁重试。

## 看门狗要求

- 启用硬件看门狗（如 ESP32 TWDT），周期 5s。
- 每个周期任务必须喂狗或注册监视器。
- OTA 写 Flash 期间必须 `disable_core0_WDT` 并在完成后恢复。
