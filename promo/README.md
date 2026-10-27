# Two Hours 演示片

55 秒，1920×1080，60fps，H.264 + AAC。采用已确认的 2h 标识与连续纸面镜头，覆盖 Skill 调用、工作与 Cubox 收藏、看板、详情与流程图、口述圈画、AI 整理、同页一次审核及分发回执。

所有内容均为虚构。片中的口述以字幕演绎，音乐与音效为原创程序化合成；没有使用真人录音或个人屏幕录像。真实产品像素来自本仓库 `examples/review` 经制作时的产品 renderer 渲染的画面，保留当时的界面快照，未展示此后新增的自动派发。原话、去向、确认等 UI 为真实前端配合浏览器内虚构 API fixture；所有请求均被拦截，不会录音、批准真实任务或对外发送。

## 文件

- `twohours-demo.mp4`：完整成片。
- `poster.jpg`：视频封面。
- `twohours-preview.gif`：静音缩略预览，三倍速；请以 MP4 判断完整节奏。
- `index.html` + `movie.js`：独立、可 seek 的确定性 Canvas 动画。交互预览按钮在导出时隐藏。
- `capture_ui.py`：仅从仓库 examples/review 构建并捕获虚构 UI。
- `audio.py`：原创配乐和轻音效。
- `render.py`：逐帧 60fps 输出；不会采用屏幕实时录制或补帧。
- `exports.py`：从成片生成 JPEG 封面、小 GIF、音轨与黑帧诊断、编码后抽帧和 SHA-256 清单。
- `导演稿.md`：经批准方向、场景与可核验时间表。
- `validation-result.json`：成片、封面、GIF、QA 和编码抽帧的 SHA-256 与媒体指标。
- `assets/provenance.json`：素材源文件 SHA-256 与被拦截请求记录。
- `stills/times.json`：取帧时间索引。`qa/` 与其余 `stills/` 图像为本地重建产物，不随仓库或发布包分发；下方命令会重新生成。
- `VALIDATION.md`：成片检查指标和实质限制。
- `ASSETS.md`：素材出处与许可；源码遵循仓库根目录的 MIT `LICENSE`。

## 重建

需要 Python 3、Node.js、ffmpeg，以及已安装的 Playwright、NumPy、Pillow 和 Chromium。捕获阶段调用仓库的正常报告 renderer，因此还需本项目已安装的 answer-me-with-html。无需 API key、云服务、真实会话或现有私有 run。

在仓库根目录运行：

```sh
python3 promo/capture_ui.py
python3 promo/audio.py
python3 promo/render.py --stills
python3 promo/render.py
python3 "$HOME/.codex/skills/huashu-art-motion/scripts/qa.py" --project promo --fps 6 --sub-band 0 --out promo/qa
python3 promo/exports.py
```

`capture_ui.py` 创建临时工作目录于 `promo/.build/`，只复制 `examples/review` 的三个输入文件，构建后删除临时 run。`.build/report.html` 是便于审计的最后一份虚构报告快照。

查看可播放 HTML：

```sh
python3 -m http.server 8942 --bind 127.0.0.1 --directory promo
```

然后打开 `http://127.0.0.1:8942/`。这只是本地预览；单击播放按钮后才播放配乐。

## QA

动画暴露 huashu-art-motion 所需的 `ERAS`、`__canvas`、`__ready`、`__total`、`prepare(t)`、`renderFrame(t)` 与 `renderSolo(id,t)` 接口。没有复制无关风格库；直接运行已安装的原始 QA 脚本：

```sh
python3 "$HOME/.codex/skills/huashu-art-motion/scripts/qa.py" \
  --project promo --fps 6 --sub-band 0 --out promo/qa
```

这里的 6fps 只用于 QA 采样；成片每一帧以 60fps 直接渲染。`--sub-band 0` 因本片的文字已经自行排版，不再预留另一个外挂字幕带。UI 截图里的文字不在 Canvas 文本测量范围，必须同时查看抽帧；详见 VALIDATION.md。

确定性针对固定素材、字体和浏览器版本下同一时刻的像素输出。系统字体和 Chromium 版本变化可能改变跨机器的抗锯齿；素材截图已经冻结，正常重渲不会访问外部来源。

整理阶段展示脚本用户在口述后保留同一句原话并添加 A5 文字意见，使用产品已有的“已提交，等待当前会话整理”状态。片中明确写“口述后保存原话与补充文字”，不演示或声称自动转写进文字批注。
