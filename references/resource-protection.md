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

同一台机器同时只跑一个转写。transcribe.py 在加载模型前取得 `~/.2hours/recorder/run/asr.lock`（不与独立 Blurt 共用）；守护器在 300 秒计时开始前代为排队并把锁交给子进程，排队时间不计入 300 秒，`summary.json` 记录 `asr_lock_wait_seconds`。排队时 stderr 打印 `asr: queued`；默认最多等 30 分钟，可用 `TWOHOURS_ASR_LOCK_TIMEOUT`（秒，正数）调整。等不到锁时退出码 3，状态为 `lock_timeout`，阶段命令没有启动，也没有 `command.log`；告诉用户稍后重试，不当作内存压力暂停。

这是工作流暂停，未完成的阶段需在压力恢复后显式重跑；不会自动循环重试或继续后续阶段。中断时已有完整阶段可复用，半成品不算成功。完成状态仍需校验转录长度、扫描帧数与图片等实际输出。

`samples.jsonl` 保存系统状态、阶段进程及当时最大的 25 个进程；`summary.json` 保留所有被观察进程的 RSS、physical footprint 采样峰值与内核 lifetime peak，PID 与启动身份一起用于区分进程。后台进程 lifetime peak 可能早于本次阶段；多进程合计可能重复计算共享内存。快于采样间隔的峰值或已退出的短命子进程可能漏采，不把采样结果称作绝对峰值。默认阈值是保守保护策略，不是经过证明的硬件安全界限，也不能防止一秒内发生的极端内存突增。

守护器支持 macOS 和 Windows；其他系统会拒绝无保护执行。Windows 把目标放进原生 Job Object（kill-on-close，不允许 breakaway），目标命令仅在启动器加入 Job 后启动，由目标直接或经子进程创建的后代都留在 Job 中；经 WMI、计划任务、COM/Shell 激活或服务等 Job 之外的代理启动的进程不在 Job 内，守护器既不限制也不会终止它们。超时、压力告警或守护进程退出时释放整组资源，不终止无关进程。Windows 使用真实 RAM、系统 commit charge、pagefile 和阶段进程内存；RAM 或 commit 达到 90% 视为压力告警。不可用的压缩内存和累计换出量记为 null，不模拟 macOS 计数器。依赖 psutil，可通过项目虚拟环境或 uv run 安装。录屏器与网页渲染进程不在此命令的自动终止范围中；可在审阅期间用不带命令的 observe 模式记录它们，出现告警后停止本轮进一步处理。
