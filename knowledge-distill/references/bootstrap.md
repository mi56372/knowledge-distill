# 工具链 bootstrap（阶段 0 判定缺依赖时必读）

素材蒸馏的可执行性取决于工具链。本文件只写"用什么做、怎么装、怎么验、装不上怎么退"，不写推测。

## 铁律

1. **先探测，再安装**：每项能力先跑 `--version` 或一次最小样例，确认已存在再跳过安装。
2. **装完必须跑最小样例**：抽 1 帧、转 10 秒音频、读 1 张表——跑通才能把相关结论标 Observed。
3. **装不上不硬做**：该维度结论降级为 Tentative，或直接写进 unknowns 并给"建议探针"，禁止用目测、常识、"通常来说"顶替。
4. **不猜路径**：可执行文件位置用 `Get-Command <name>`（Windows）/ `command -v <name>`（POSIX）拿，不手写绝对路径。

## 能力 → 命令 → 验证 → 退回

| 能力 | Windows | POSIX | 最小样例验证 | 装不上时的退回 |
|---|---|---|---|---|
| Python 运行时 | `python --version` | `python3 --version` | 能打印 3.9+ 版本号 | 无 Python 则无法实算表格/跑校验，直接告知用户补装 |
| 视频抽帧 | `ffmpeg -i in.mp4 -vf "fps=1/5" -q:v 2 frames\%04d.jpg` | 同左（路径 `/`） | 输出目录出现 ≥1 张 jpg 且能打开 | 抽帧失败 → 画面类结论全部 unknown，只能做口播维度 |
| 关键段精定位 | `ffmpeg -ss 00:02:30 -t 30 -i in.mp4 -vf fps=2 seg\%04d.jpg`（`-ss` 放在 `-i` 前，快） | 同左 | 段内帧数 ≈ 时长×2 | 同上 |
| 音轨抽取 | `ffmpeg -i in.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav` | 同左 | 生成 wav 且 `ffprobe` 显示 16k 单声道 | 无音轨 → 判定"素材缺声道"，向用户索取字幕/讲稿 |
| ASR 转写 | 使用下方隔离环境安装 `faster-whisper==1.2.0` | 同左 | 转 10 秒样例，确认输出带时间戳且中文可读 | 不可用 → 视频/音频素材终止，改用"用户提供字幕或 text 导出" |
| 词级/句级时间戳 | faster-whisper 传 `word_timestamps=True`，落 srt/json | 同左 | 抽查 1 句，时间戳与音频对得上 | 无时间戳 → 定位只能写"约第 N 段"，相关结论降 Tentative |
| 屏幕 OCR | `tesseract img.png out -l chi_sim+eng --psm 6`（Windows 用 UB-Mannheim 发行版，安装时勾选 chi_sim 语言包） | `tesseract` 同参数 | 识别出的文本 ≥ 图中可见文字的 80% | 识别不清 → 该区域按 `[unclear]` 原样记录，不猜内容 |
| 表格实算 | 使用下方隔离环境安装 `pandas==2.2.3`、`duckdb==1.1.3`、`openpyxl==3.1.5` | 同左 | `python -c "import pandas, duckdb, openpyxl"` 无报错 | 无实算 → 大表结论只能标 Tentative |
| PDF 取文 | 使用下方隔离环境安装 `pymupdf==1.24.10`（或 poppler 的 `pdftotext -layout in.pdf out.txt`） | 同左 | 取出的字符数与页数匹配 | 扫描件无文本层 → 转补丁 C 走 OCR |
| 网页抓取 | `curl -sL "<url>" -o page.html` + webfetch | `curl -sL` | page.html 内含正文文本节点 | 只拿到 SPA 骨架 → 走 probe-web.md 的登录墙判定 |

## Python 依赖隔离

安装第三方 Python 包前先说明影响并取得用户同意。必须在 OUTPUT_PATH **之外**的专用虚拟环境中安装，禁止全局 `pip install`，也不得把虚拟环境放进待交付产物。

- Windows PowerShell：
  ```powershell
  $venv = Join-Path $env:TEMP "knowledge-distill-venv"
  python -m venv $venv
  & (Join-Path $venv "Scripts\python.exe") -m pip install "faster-whisper==1.2.0"
  ```
- POSIX：
  ```bash
  venv="${TMPDIR:-/tmp}/knowledge-distill-venv"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install "faster-whisper==1.2.0"
  ```
- 上例只安装 ASR 依赖；表格实算与 PDF 取文按表中固定版本替换包名。虚拟环境已存在时先核对版本，不删除重建。
- 表中版本是可复现基线；需要升级时整体记录新版本并重跑对应最小样例，不在素材处理过程中临时升级。

## 版本与编码注意

- 涉及软件版本的操作性结论，必须记录演示/抓取时的版本号（`ffmpeg -version` 第一行），无版本信息标 unknown。
- Windows 中文路径：命令里一律加引号；PowerShell 用 `-LiteralPath` 防止通配符误伤。
- 输出统一 UTF-8：PowerShell 先 `$OutputEncoding = [Text.Encoding]::UTF8`，避免中文台账乱码。

## fallback（不要走的路）

- 当依赖装不上时：不要改用手工目测顶替实算——目测结论只能标 Tentative，且必须写明"未实算"。
- 当转写稿只有 ASR 文本、没有画面事实时：不要把"口播提到但画面未捕获"的内容写进结论，该处标 unknown 并给截图/录屏探针。
- 当工具跑通但样例输出明显错误时：不要因为"命令成功"就标 Observed——命令成功不等于结果正确，必须核对样例输出。
