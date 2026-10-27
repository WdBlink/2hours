# 录音处理的内存保护

macOS 上转写、视频扫描及抽帧必须串行通过 `scripts/resource_guard.py`。守护器不加载模型，不安装依赖；使用系统压力读数、`vm_stat`、进程列表与 `proc_pid_rusage`，记录全机观察到的进程峰值。

```bash
python3 SKILL/scripts/resource_guard.py --stage baseline --output RUN/telemetry/baseline --observe 15
python3 SKILL/scripts/resource_guard.py --stage transcribe --output RUN/telemetry/transcribe -- uv run SKILL/vendor/blurt/scripts/transcribe.py run VIDEO --out NEW_OUTPUT --backend sensevoice
```

以上路径均替换为真实绝对路径。每个阶段使用新的输出目录，已有诊断不会覆盖。命令必须在前台运行，不得自行脱离进程组或启动后台任务。先检查退出码和 `summary.json` 的状态，再决定下一阶段；不要使用忽略退出码的并行启动方式。原录音、已有转录和扫描缓存保持不变；诊断重跑写入独立目录，强制扫描时使用录音的独立副本或硬链接。

开始前连续三次检查正常压力。运行期间默认每秒采样，以下任一条件停止本阶段自己的进程组，并以退出码 75 标记工作流暂停：

- 系统内存压力不为 normal。
- 压缩器占用超过物理内存的 40%。
- 相对本阶段开始，swap 使用量或累计换出量增加超过 512 MiB。
- 本阶段进程组的保守内存合计超过 4 GiB。
- 阶段运行超过 300 秒。

停止先发 SIGTERM，2 秒未退出再发 SIGKILL。这样可以释放阶段内存，不能用 SIGSTOP 把占用留在内存里冒充压力解除。守护器不终止 Ollama、其他项目或用户应用。因外部负载暂停时指出对应进程，由用户决定如何处理。采样失败也停止阶段，不降级为无保护运行。

这是工作流暂停，未完成的阶段需在压力恢复后显式重跑；不会自动循环重试或继续后续阶段。中断时已有完整阶段可复用，半成品不算成功。完成状态仍需校验转录长度、扫描帧数与图片等实际输出。

`samples.jsonl` 保存系统状态、阶段进程及当时最大的 25 个进程；`summary.json` 保留所有被观察进程的 RSS、physical footprint 采样峰值与内核 lifetime peak，PID 与启动身份一起用于区分进程。后台进程 lifetime peak 可能早于本次阶段；多进程合计可能重复计算共享内存。快于采样间隔的峰值或已退出的短命子进程可能漏采，不把采样结果称作绝对峰值。默认阈值是保守保护策略，不是经过证明的硬件安全界限，也不能防止一秒内发生的极端内存突增。

目前该守护器只支持 macOS；其他系统会拒绝无保护执行。录屏器与网页渲染进程不在此命令的自动终止范围中；可在审阅期间用不带命令的 observe 模式记录它们，出现告警后停止本轮进一步处理。
