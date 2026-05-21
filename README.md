# Beat GIF Player

一个 Windows 悬浮 GIF 播放器。程序会监听系统正在播放的音乐，实时估计节奏和 BPM，让 GIF 按节奏循环播放，并尽量让每轮 GIF 的起始点贴近节拍。

## 功能

- 监听 Windows 系统音频
- 根据音乐节奏估计 BPM
- GIF 无边框、置顶、可拖动显示
- GIF 按比例适配固定窗口大小
- 每轮 GIF 播放完后，根据最新 BPM 调整下一轮速度
- 左键点击切换 `fig` 文件夹中的下一个 GIF
- 右键显示“关闭”按钮

## 环境

推荐：

```bash
Windows 10 / Windows 11
Python 3.10+
````

安装依赖：

```bash
pip install numpy pillow PySide6 soundcard
```

或：

```bash
pip install -r requirements.txt
```

## 项目结构

```text
beat_gif_player/
├── main.py
├── config.json
├── requirements.txt
├── README.md
└── gif/
    ├── 1.gif
    ├── 2.gif
    └── 3.gif
```

## 运行

```bash
python main.py
```

## 鼠标操作

```text
左键点击：切换下一个 GIF
左键拖动：移动窗口
右键点击：显示关闭按钮
点击关闭：退出程序
```

## config.json 示例

```json
{
  "fig_folder": "gif",
  "left_click_next_gif": true,
  "drag_threshold": 6,
  "gif_path": "gif/11.gif",
  "beats_per_gif": 2,
  "sensitivity": 1.45,
  "min_bpm": 60,
  "max_bpm": 200,
  "initial_bpm": 120,
  "sample_rate": 44100,
  "block_size": 1024,
  "window_width": 100,
  "window_height": 100,
  "window_scale": 1.0,
  "start_x": 200,
  "start_y": 200,
  "always_on_top": true,
  "restart_on_trigger": false,
  "close_button_autohide_ms": 3000,
  "align_loop_to_beat": true,
  "max_loop_time_adjust_ratio": 0.25
}

```

## 参数说明

### GIF 相关

| 参数                    | 含义                       |
| --------------------- | ------------------------ |
| `gif_path`            | 默认 GIF 路径，可为空            |
| `fig_folder`          | GIF 文件夹，左键点击时依次切换其中的 GIF |
| `left_click_next_gif` | 是否启用左键切换 GIF             |
| `drag_threshold`      | 区分点击和拖动的像素阈值             |

### 节奏相关

| 参数              | 含义                      |
| --------------- | ----------------------- |
| `beats_per_gif` | GIF 一轮对应多少拍，默认 2        |
| `sensitivity`   | 节奏检测灵敏度，越小越敏感           |
| `min_bpm`       | 允许估计的最低 BPM             |
| `max_bpm`       | 允许估计的最高 BPM             |
| `initial_bpm`   | 启动时尚未检测到 BPM 前使用的默认 BPM |

### 音频相关

| 参数            | 含义                     |
| ------------- | ---------------------- |
| `sample_rate` | 音频采样率，常用 44100 或 48000 |
| `block_size`  | 音频块大小，越大越稳定但延迟越高       |

### 窗口相关

| 参数                         | 含义                |
| -------------------------- | ----------------- |
| `window_width`             | 悬浮窗宽度             |
| `window_height`            | 悬浮窗高度             |
| `window_scale`             | 兼容旧版本，一般保持 1.0    |
| `start_x`                  | 窗口初始横坐标           |
| `start_y`                  | 窗口初始纵坐标           |
| `always_on_top`            | 是否置顶              |
| `close_button_autohide_ms` | 右键关闭按钮自动隐藏时间，单位毫秒 |

### 播放策略相关

| 参数                           | 含义                   |
| ---------------------------- | -------------------- |
| `restart_on_trigger`         | 兼容旧版本，当前建议保持 `false` |
| `align_loop_to_beat`         | 是否让 GIF 每轮起点尽量贴近节拍   |
| `max_loop_time_adjust_ratio` | 每轮 GIF 最大速度调整比例      |

## 调参建议

如果 GIF 反应不明显：

```json
"sensitivity": 1.25
```

如果误触发太多：

```json
"sensitivity": 1.8
```

如果音频采集不稳定：

```json
"sample_rate": 48000,
"block_size": 2048
```

如果 GIF 速度变化太明显：

```json
"max_loop_time_adjust_ratio": 0.15
```

如果不想贴拍，只想按 BPM 调速：

```json
"align_loop_to_beat": false
```

## 常见提示

如果看到：

```text
QWindowsContext: OleInitialize() failed
```

但程序能正常运行，可以忽略。

如果看到：

```text
SoundcardRuntimeWarning: data discontinuity in recording
```

表示音频采集有短暂不连续。偶尔出现可以忽略；频繁出现时可增大 `block_size`。

```
```
