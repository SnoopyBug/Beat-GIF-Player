# Beat GIF Player

一个 Windows Python 小应用：监听系统正在播放的音乐，实时估计节奏/BPM，并让 GIF 悬浮窗持续循环播放。当前版本不会在鼓点处强制从第一帧重播，而是 **每次 GIF 完整播放一遍后，根据最近估计的 BPM 调整下一轮播放速度，并尽量让下一次回到第一帧的时刻贴近节拍网格**，画面更自然。

## 功能

- Windows 系统音频 WASAPI loopback 监听。
- 实时能量峰值检测 + BPM 平滑估计。
- 无边框、置顶、可拖动 GIF 悬浮窗。
- 窗口大小固定，由 `window_width` / `window_height` 配置。
- GIF 会保持原始宽高比，完整适配到固定窗口尺寸内，居中显示。
- GIF 一直循环播放。
- 每轮 GIF 播放结束后，按最近 BPM 调整下一轮速度。
- 一轮 GIF 的目标时长 = `beats_per_gif × 当前估计每拍时长`。
- 可选节拍对齐：让下一次 GIF 回到第一帧的时刻尽量靠近 `beats_per_gif` 对应的节拍边界。
- 右键点击 GIF 后显示“关闭”按钮。
- 使用 `config.json` 配置。

## 安装

建议使用 Python 3.10+。

```bash
cd beat_gif_player
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

编辑 `config.json`：

```json
{
  "gif_path": "C:/Users/you/Pictures/your.gif",
  "beats_per_gif": 4,
  "sensitivity": 1.45,
  "initial_bpm": 120,
  "window_width": 360,
  "window_height": 360,
  "align_loop_to_beat": true,
  "max_loop_time_adjust_ratio": 0.25
}
```

常用参数：

- `gif_path`：本地 GIF 文件路径。Windows 路径建议使用 `/`，例如 `C:/Users/you/Desktop/a.gif`。
- `beats_per_gif`：GIF 完整播放一遍对应多少拍。比如 BPM=120 且 `beats_per_gif=4`，一轮 GIF 时长为 2 秒。
- `sensitivity`：节奏触发灵敏度，越小越容易检测到拍点，越大越保守。建议范围：`1.1 ~ 2.2`。
- `initial_bpm`：程序刚启动、还没估计出 BPM 时的默认播放速度。
- `min_bpm` / `max_bpm`：限制 BPM 估计范围，避免误判成过慢或过快。
- `window_width`：悬浮窗固定宽度，单位像素。
- `window_height`：悬浮窗固定高度，单位像素。
- `block_size`：音频块大小。`512` 响应更快，`1024` 平衡，`2048` 更稳但延迟更高。
- `close_button_autohide_ms`：右键显示“关闭”按钮后的自动隐藏时间，单位毫秒。
- `align_loop_to_beat`：是否启用 GIF 循环边界贴拍。启用后不会中途重播，而是在一轮结束时调整下一轮时长，使下一次回到第一帧尽量靠近节拍。
- `max_loop_time_adjust_ratio`：每轮允许相对名义时长的最大拉伸/压缩比例。默认 `0.25` 表示最多 ±25%。调大更贴拍但速度变化更明显；调小更平滑但对齐更慢。

## 运行

```bash
cd Beat-GIF-Player
python main.py
```

先播放音乐，再启动程序通常更容易选中正确的系统回放设备。

## 当前播放策略

旧策略：每到触发点就立即从第一帧重新播放 GIF。  
新策略：GIF 一直循环播放；检测器只更新最近 BPM 和最近 beat 相位；当 GIF 播完一整轮、准备回到第一帧时，才调整下一轮的帧间隔。

如果 `align_loop_to_beat=true`，程序会预测下一次 `beats_per_gif` 拍边界，并在 `max_loop_time_adjust_ratio` 允许的范围内拉伸/压缩下一轮 GIF，让下一次回到第一帧的时刻尽量贴近节拍。这样不会在播放中途突然跳回第一帧，画面更自然。

## 注意

1. 这个版本使用实时能量峰值检测，适合鼓点/节奏明显的音乐。复杂音乐可能会误判或漏判。
2. 程序启动后通常需要 2~4 个拍子来稳定 BPM，启动初期会使用 `initial_bpm`。
3. 如果无法捕获系统声音，请确认 Windows 输出设备正常，并尝试重启程序。
4. 某些蓝牙/虚拟声卡设备可能无法被 `soundcard` 正确 loopback 捕获。
5. GIF 会按比例缩放到 `window_width × window_height` 内，不会拉伸变形；如果窗口比例与 GIF 比例不同，会留出透明空白。
