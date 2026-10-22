# 基于频谱掩蔽的听觉隐蔽音频信息传输方法

[English](readme.md)

一个通过音频传递文字的 Python 研究原型。生成器将文字编码到强连续主音附近的两路微弱通信音中；读取器从 WAV 文件或麦克风输入恢复文字。研究目标是让通信音较难被听众察觉，同时仍可由机器检测。

**当前实现：**可配置的连续正弦掩蔽主音，以及两个固定的相邻通信频率。本仓库不分析音乐、不跟踪变化的频谱主峰、不对音乐挖空，也没有计算心理声学掩蔽阈值。听觉隐蔽性是研究目标，并非本实现已经通过实验确认的性能。

## 功能

- 支持 Unicode 文字，包括中文、英文、换行和表情。
- 导出 PCM WAV、现场播放、WAV 解码和麦克风接收。
- 在大号文字区域逐个显示已解出的完整字符。
- 两个频段传输同一正文，提供逐字节与整帧校验。
- 对未满足核对规则的字符标红。
- 叠加多个额外纯音，分别设置强度，并检查干扰风险。
- 导入、导出 JSON 参数，可选将公开参数附在 WAV 中。
- 黑白 Tkinter 界面；界面标签和诊断信息目前为中文。

## 安装

需要 **Python 3.10+ 和 Tkinter**。`requirements.txt` 列出 NumPy 和 sounddevice。文件合成、解析使用 NumPy 和标准库；播放、录音还需要 sounddevice 及可用的 PortAudio 后端。

### Windows

安装带 Tkinter 的 Python，并确保可以使用 `py` 或 `python`。双击 `安装依赖.cmd` 创建 `.venv` 并安装依赖，再打开 `启动生成器.cmd` 或 `启动读取器.cmd`。两个入口共用本目录的环境，不含用户专属的 Python 路径。

也可以执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe generator.py
.\.venv\Scripts\python.exe reader.py
```

### macOS

安装 Python 3.10+，并确认 Tkinter 可用。打开 `启动生成器.command` 或 `启动读取器.command`。首次启动会创建 `.venv-macos` 并联网安装依赖；也可单独运行 `安装依赖.command`。系统询问时允许麦克风访问。

如果下载或解压时丢失执行权限，在此文件夹打开终端并执行：

```bash
bash 启动生成器.command
bash 启动读取器.command
```

脚本会搜索常见 Python 安装位置和命令搜索路径，也可用 `AUDIO_MORSE_PYTHON` 指定可执行文件。macOS 脚本有语法检查和模拟入口测试覆盖，尚未完成 Mac 实机验证。

### Linux／手动安装

根据发行版需要安装 Tkinter 和 PortAudio 系统包，然后执行：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python generator.py
python reader.py
```

下文命令应使用同一环境中的 Python。Windows 未激活环境时使用 `.\.venv\Scripts\python.exe`；macOS 启动器创建的环境使用 `.venv-macos/bin/python`。

## 快速使用

### 文件解码

1. 打开读取器，保持默认参数。
2. 导入 `demo_你好.wav`，预期显示 `你好`，两路均通过校验。
3. 在生成器输入自己的文字，保存 WAV。
4. 将该文件导入读取器，完整 Unicode 字符会随着解码逐个显示。
5. 改过参数时，请在两端导出、导入 JSON 配置，或启用 WAV 附带公开参数的选项。

`demo_红字校验.wav` 特意在 A 路发送 `ABC`、B 路发送 `AXC`。预期显示 `ABC`，仅中间的 `B` 标红。`demo_额外频率.wav` 叠加三个纯音，配置见 `demo_extra_settings.json`。附带的 `demo_*result.json` 是示例解码结果，不是个人消息。

### 麦克风接收

选择输入设备，并在播放开始**之前**启动接收。先从近距离测试，确保捕获完整前导，两端参数保持一致。降噪、回声消除、自动增益、削波和强烈反射可能导致解码失败。每次接收处理一帧；下一条消息需再次启动接收。

### 叠加纯音

点击生成器中的“额外叠加频率…”。每项分别设置频率，以及相对于主音的振幅分贝。检查器会对已建模的通道重叠、分析泄漏、谐波、互调、重复频率和总振幅过高给出提示。预警不会自动删除所选纯音。必要时，峰值保护等比例缩小整段波形，保持相对振幅不变。这些检查不保证真实设备接收时完全没有干扰。

## 信号与协议

| 参数 | 默认值 |
| --- | --- |
| 采样率 | 48,000 Hz |
| 连续掩蔽主音 | 1,800 Hz |
| A／B 路通信频率 | 1,920 Hz／2,040 Hz |
| 主音峰值 | −12 dBFS |
| 每路通信音相对于主音 | 振幅低 30 dB |
| 摩斯单位时长 | 60 毫秒 |
| 连续开启前导 | 约 1.5 秒 |

通信音通过开关状态传递信息，启停边沿经过平滑处理。主音和可选额外纯音在传输期间保持连续。

```text
0.25 秒主音
连续开启前导
20 组 10，共 40 个码元
32 位同步字
帧头：版本、模式、UTF-8 字节数、正文 CRC32、帧头 CRC16
逐字节正文：短同步序列＋两个摩斯十六进制槽＋CRC8
0.25 秒尾部主音
```

文字先转为 UTF-8 字节，再转换为十六进制数字，使用 `0–9` 和 `A–F` 的标准摩斯符号发送，并不是直接使用中文摩斯字母表。每个正文字节占 68 个单位，默认每字节约 4.08 秒，另有帧开销。默认发送 `你好` 约需 36.56 秒。上限为 256 个 UTF-8 字节，以及 10 分钟总音频。

读取器采用加窗联合音调拟合、相对振幅归一化、基于前导的门限、主音频率／时钟偏差估计，以及逐字节重新同步来缓解干扰。

## 校验与红字

两个通道独立解码。接收期间，黑字表示两路对应字节一致且 CRC8 通过，整帧 CRC32 尚待确认；最终颜色还会结合整帧校验结果更新。

- 两路内容不同，或对应字节缺失、无效时，相关字符标红。
- 显示优先采用完整正文 CRC32 通过的一路，否则显示可用候选。
- 两路完整正文 CRC32 均未通过时，所有已显示字符标红。
- 只有两路完整帧校验均通过且没有红字，`verified` 才为真。
- 接收中断或文件截断可以产生部分文字，但不会报告为完整通过校验。

界面保存文字时，会写出 TXT 和旁边的 `.validation.json` 报告。纯文本不保留颜色。CRC 检测偶然误码，不提供纠错或消息认证；两路一致也不是绝对正确的保证。

## 命令行

```bash
python generator.py --message "Hello, 你好!" --output message.wav
python reader.py --input message.wav --json result.json
python generator.py --message "HELLO" --play
python generator.py --message "HELLO" --include-settings --output message.wav
python generator.py --message "HELLO" --config demo_settings.json --output message.wav
python generator.py --message "HELLO" --tone 500:-18 --tone 730:-18 --output layered.wav
python reader.py --input message.wav --config demo_settings.json
python selftest.py
```

读取器退出码 `0` 表示完整通过校验，`2` 表示已解码但未完全通过校验。找不到合法帧等失败以非零错误退出。默认使用 WAV 内附参数覆盖接收参数，除非指定 `--ignore-settings`。生成器命令行只有指定 `--include-settings` 才附带参数。可选元数据仅包含公开信号参数，不包含原文；正文从波形恢复。

## 限制与测试

- **尚未通过听音实验确认听觉隐蔽性。**数字振幅低并不直接证明掩蔽成立；是否可察觉取决于听众、播放音量、设备和信号设置。
- 本项目是信息隐藏与信号编码实验，不是加密系统；频率和协议可以被分析。
- 本版使用合成纯音，不使用 MP3／WAV／OGG 音乐作载体，没有音乐动态跟踪、掩蔽阈值模型或专用超声波模式。
- 文件输入支持未压缩的 8／16／24／32 位整数 PCM WAV，不支持 MP3、OGG 或浮点 WAV。有损转换可能破坏数据。
- 没有前向纠错或自动重传功能。
- `selftest.py` 包含 39 个确定性用例，覆盖波形往返、Unicode、流式接收、分歧标红、噪声、回声、增益／时钟变化、截断及额外纯音检查。测试不播放声音，也不访问麦克风。模拟结果不证明真实房间通信效果或不可听性。

## 仓库与发布说明

| 文件 | 用途 |
| --- | --- |
| `generator.py`、`reader.py` | 图形界面和命令行入口 |
| `modem.py` | 协议、合成、PCM WAV 读写、解码 |
| `ui_common.py` | 共用控件、参数和音频设备支持 |
| `launcher.py`、`windows_launch.cmd` | 共用程序入口和 Windows 环境启动器 |
| `*.cmd`、`*.command`、`mac_launch.sh` | 平台启动与安装脚本 |
| `selftest.py`、`requirements.txt` | 测试和 Python 依赖 |
| `demo_*` | 合成音频、公开参数和示例结果 |

发布副本不包含随包的平台专用依赖、字节码缓存或历史生成的测试报告。原来的独立 Mac 说明已合并到本文件及英文版中。`.gitignore` 排除本地环境、可选 `vendor/`、日志及常见生成文件，同时保留附带示例。个人录音、解码消息请放在已忽略的 `recordings/`、`outputs/` 或 `decoded/` 目录。忽略规则不会将已跟踪的文件从 Git 索引或历史中移除。

## 许可证

项目尚未选择许可证。本次整理没有代为指定许可证，也没有虚构作者身份。第三方依赖具有各自的许可证，安装时单独获取。
