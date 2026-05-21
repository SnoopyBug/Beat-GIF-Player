import json
import math
import os
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageSequence, ImageQt
from PySide6.QtCore import QObject, Qt, QTimer, Signal, QPoint
from PySide6.QtGui import QPixmap, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget, QMessageBox


@dataclass
class AppConfig:
    # 单个 GIF 路径；如果 fig_folder 中有 GIF，会优先使用 fig_folder 的 GIF 列表。
    gif_path: str = ""

    # 新增：GIF 文件夹。鼠标左键短按图片时，会切换到该文件夹中的下一个 GIF。
    fig_folder: str = "fig"
    left_click_next_gif: bool = True
    drag_threshold: int = 6

    beats_per_gif: int = 4
    sensitivity: float = 1.45
    min_bpm: float = 60.0
    max_bpm: float = 200.0
    initial_bpm: float = 120.0
    sample_rate: int = 44100
    block_size: int = 1024

    # 窗口固定尺寸。GIF 会按比例缩放并完整放入这个区域。
    window_width: int = 360
    window_height: int = 360
    # 兼容旧配置：如果 window_width/window_height 未设置时才建议使用 window_scale。
    window_scale: float = 1.0
    start_x: int = 200
    start_y: int = 200
    always_on_top: bool = True

    # 兼容旧配置：当前版本已改为循环播放，不再根据触发立即重播。
    restart_on_trigger: bool = False
    close_button_autohide_ms: int = 3000

    # 是否让 GIF 每一轮重新开始的时刻尽量对齐到节拍网格。
    align_loop_to_beat: bool = True
    # 每一轮允许相对名义节拍周期最大拉伸/压缩比例。0.25 表示最多 ±25%。
    # 设大一些更容易“贴拍”，设小一些画面速度更平滑。
    max_loop_time_adjust_ratio: float = 0.25


def load_config(path: str = "config.json") -> AppConfig:
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到配置文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return AppConfig(**raw)


def _fit_size_preserve_aspect(src_w: int, src_h: int, dst_w: int, dst_h: int) -> Tuple[int, int]:
    """返回保持比例后，能够完整放入 dst_w × dst_h 的尺寸。"""
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    dst_w = max(1, int(dst_w))
    dst_h = max(1, int(dst_h))
    ratio = min(dst_w / src_w, dst_h / src_h)
    return max(1, int(src_w * ratio)), max(1, int(src_h * ratio))


def pil_frame_to_pixmap(
    img: Image.Image,
    target_size: Optional[Tuple[int, int]] = None,
    scale: float = 1.0,
) -> QPixmap:
    frame = img.convert("RGBA")
    w, h = frame.size

    if target_size is not None:
        tw, th = _fit_size_preserve_aspect(w, h, target_size[0], target_size[1])
        if (tw, th) != (w, h):
            frame = frame.resize((tw, th), Image.LANCZOS)
    elif scale != 1.0:
        frame = frame.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    qimage = ImageQt.ImageQt(frame)
    return QPixmap.fromImage(qimage)


def load_gif_frames(
    gif_path: str,
    target_size: Optional[Tuple[int, int]] = None,
    scale: float = 1.0,
) -> Tuple[List[QPixmap], List[int]]:
    if not os.path.exists(gif_path):
        raise FileNotFoundError(f"GIF 文件不存在：{gif_path}")

    im = Image.open(gif_path)
    frames: List[QPixmap] = []
    durations: List[int] = []

    # 保留 dispose/透明信息的常用做法：逐帧 convert RGBA。
    # 对少量复杂 GIF，Pillow 的逐帧结果可能与浏览器略有差异，但足够用于悬浮显示。
    for frame in ImageSequence.Iterator(im):
        duration = int(frame.info.get("duration", 80))
        if duration <= 0:
            duration = 80
        durations.append(duration)
        frames.append(pil_frame_to_pixmap(frame.copy(), target_size=target_size, scale=scale))

    if not frames:
        raise ValueError("GIF 未包含可播放帧。")
    return frames, durations


class BeatSignals(QObject):
    # 参数：名义 GIF 播放总时长(秒)、当前估计 BPM、最近一次 beat 的 perf_counter 时间戳、beat 计数。
    # 当前版本不在 beat 到来时强制重播，只在 GIF 一轮结束后调整下一轮速度和相位。
    bpm_updated = Signal(float, float, float, int)
    status = Signal(str)
    error = Signal(str)


class BeatDetector(threading.Thread):
    def __init__(self, cfg: AppConfig, signals: BeatSignals):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.signals = signals
        self.stop_event = threading.Event()

        self.energy_history = deque(maxlen=48)
        self.beat_times = deque(maxlen=16)
        self.beat_count = 0
        self.smoothed_interval: Optional[float] = None
        self.last_beat_time = 0.0
        self.prev_energy = 0.0
        self.prev_above = False

    def stop(self) -> None:
        self.stop_event.set()

    def _choose_loopback_microphone(self):
        # 放在线程内导入，避免 Qt 主线程初始化时和 Windows COM 初始化顺序冲突。
        import soundcard as sc

        speaker = sc.default_speaker()
        microphones = sc.all_microphones(include_loopback=True)

        # 优先选择和默认扬声器名称相近的 loopback device。
        speaker_name = (speaker.name or "").lower()
        loopbacks = [m for m in microphones if "loopback" in (m.name or "").lower()]
        for mic in loopbacks:
            mic_name = (mic.name or "").lower()
            if speaker_name and (speaker_name in mic_name or mic_name in speaker_name):
                return mic

        if loopbacks:
            return loopbacks[0]

        # soundcard 在部分系统上不是显式命名 loopback，但 include_loopback=True 仍可能返回可用设备。
        for mic in microphones:
            mic_name = (mic.name or "").lower()
            if speaker_name and (speaker_name in mic_name or mic_name in speaker_name):
                return mic

        if microphones:
            return microphones[0]
        raise RuntimeError("没有找到可用于系统音频监听的 loopback 设备。")

    def _estimate_interval(self, now: float) -> Optional[float]:
        if len(self.beat_times) < 3:
            return None
        times = list(self.beat_times)
        intervals = np.diff(times)
        min_interval = 60.0 / self.cfg.max_bpm
        max_interval = 60.0 / self.cfg.min_bpm
        valid = intervals[(intervals >= min_interval) & (intervals <= max_interval)]
        if len(valid) < 2:
            return None
        median_interval = float(np.median(valid))
        if self.smoothed_interval is None:
            self.smoothed_interval = median_interval
        else:
            self.smoothed_interval = 0.85 * self.smoothed_interval + 0.15 * median_interval
        return self.smoothed_interval

    def _process_audio_block(self, mono: np.ndarray) -> None:
        # RMS energy；加一个极小值避免 log/除法问题。
        rms = float(np.sqrt(np.mean(mono * mono) + 1e-12))
        energy = math.log1p(rms * 100.0)

        self.energy_history.append(energy)
        if len(self.energy_history) < 12:
            self.prev_energy = energy
            return

        hist = np.array(self.energy_history, dtype=np.float32)
        mean = float(np.mean(hist))
        std = float(np.std(hist))
        threshold = mean + self.cfg.sensitivity * std

        now = time.perf_counter()
        min_interval = 60.0 / self.cfg.max_bpm

        # 上升沿 + 自适应阈值 + refractory，避免同一个鼓点连续触发。
        above = energy > threshold
        rising = energy > self.prev_energy * 1.04
        enough_gap = (now - self.last_beat_time) >= min_interval

        if above and rising and not self.prev_above and enough_gap:
            self.last_beat_time = now
            self.beat_times.append(now)
            self.beat_count += 1

            interval = self._estimate_interval(now)
            if interval is not None:
                bpm = 60.0 / interval
                target_duration = interval * max(1, self.cfg.beats_per_gif)
                # 只更新“下一轮 GIF 循环”的目标时长，不打断当前播放。
                self.signals.bpm_updated.emit(float(target_duration), float(bpm), float(now), int(self.beat_count))
                self.signals.status.emit(
                    f"Beat {self.beat_count} | BPM≈{bpm:.1f} | next GIF loop={target_duration:.2f}s"
                )

        self.prev_above = above
        self.prev_energy = energy

    def run(self) -> None:
        try:
            mic = self._choose_loopback_microphone()
            self.signals.status.emit(f"音频输入：{mic.name}")
            with mic.recorder(samplerate=self.cfg.sample_rate, blocksize=self.cfg.block_size) as recorder:
                while not self.stop_event.is_set():
                    data = recorder.record(numframes=self.cfg.block_size)
                    if data is None or len(data) == 0:
                        continue
                    # shape: [frames, channels]
                    mono = np.mean(data, axis=1).astype(np.float32)
                    self._process_audio_block(mono)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class GifWindow(QWidget):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.window_width = max(1, int(cfg.window_width))
        self.window_height = max(1, int(cfg.window_height))

        self.frame_index = 0
        self.frames: List[QPixmap] = []
        self.original_durations_ms: List[int] = []
        self.scaled_durations_ms: List[int] = []

        self.drag_offset: Optional[QPoint] = None
        self.press_global_pos: Optional[QPoint] = None
        self.did_drag = False

        # 当前版本：GIF 一直循环播放。检测到的 BPM 只会更新 pending_target_total_sec，
        # 下一次 GIF 完整播完并回到第一帧时再应用新速度。
        initial_bpm = min(max(float(cfg.initial_bpm), float(cfg.min_bpm)), float(cfg.max_bpm))
        initial_target_total_sec = max(0.12, max(1, cfg.beats_per_gif) * 60.0 / initial_bpm)
        self.current_target_total_sec = initial_target_total_sec
        self.pending_target_total_sec = initial_target_total_sec
        self.current_bpm = initial_bpm
        self.last_beat_time: Optional[float] = None
        self.last_beat_count: Optional[int] = None

        self.gif_files = self._collect_gif_files()
        self.current_gif_index = self._find_initial_gif_index()
        self._load_current_gif(update_label=False, restart_timer=False)

        flags = Qt.FramelessWindowHint | Qt.Tool
        if cfg.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("background: transparent;")
        self.setFixedSize(self.window_width, self.window_height)
        self.label.resize(self.size())
        self.move(cfg.start_x, cfg.start_y)
        if self.frames:
            self.label.setPixmap(self.frames[0])

        self.close_button = QPushButton("关闭", self)
        self.close_button.setStyleSheet(
            "QPushButton { background: rgba(30, 30, 30, 210); color: white; "
            "border: 1px solid rgba(255,255,255,160); border-radius: 8px; padding: 6px 12px; }"
            "QPushButton:hover { background: rgba(180, 40, 40, 230); }"
        )
        self.close_button.clicked.connect(QApplication.instance().quit)
        self.close_button.hide()

        self.hide_close_timer = QTimer(self)
        self.hide_close_timer.setSingleShot(True)
        self.hide_close_timer.timeout.connect(self.close_button.hide)

        self.play_timer = QTimer(self)
        self.play_timer.setSingleShot(True)
        self.play_timer.timeout.connect(self._next_frame)

    def _collect_gif_files(self) -> List[str]:
        """收集 fig_folder 下的所有 GIF；若 gif_path 存在但不在文件夹内，也加入列表。"""
        files: List[str] = []

        fig_folder = Path(self.cfg.fig_folder).expanduser()
        if fig_folder.exists() and fig_folder.is_dir():
            for p in sorted(fig_folder.iterdir(), key=lambda x: x.name.lower()):
                if p.is_file() and p.suffix.lower() == ".gif":
                    files.append(str(p.resolve()))

        gif_path = (self.cfg.gif_path or "").strip()
        if gif_path:
            p = Path(gif_path).expanduser()
            if p.exists() and p.is_file() and p.suffix.lower() == ".gif":
                resolved = str(p.resolve())
                if resolved not in files:
                    files.insert(0, resolved)

        if not files:
            if gif_path:
                raise FileNotFoundError(
                    f"没有找到可用 GIF。请检查 gif_path 或 fig_folder。\n"
                    f"gif_path: {self.cfg.gif_path}\nfig_folder: {self.cfg.fig_folder}"
                )
            raise FileNotFoundError(
                f"没有找到可用 GIF。请在 {self.cfg.fig_folder} 文件夹中放入 .gif 文件，"
                "或在 config.json 中设置 gif_path。"
            )
        return files

    def _find_initial_gif_index(self) -> int:
        gif_path = (self.cfg.gif_path or "").strip()
        if not gif_path:
            return 0
        p = Path(gif_path).expanduser()
        if not p.exists():
            return 0
        resolved = str(p.resolve())
        try:
            return self.gif_files.index(resolved)
        except ValueError:
            return 0

    def _current_gif_path(self) -> str:
        return self.gif_files[self.current_gif_index]

    def _load_current_gif(self, update_label: bool = True, restart_timer: bool = True) -> None:
        gif_path = self._current_gif_path()
        frames, durations = load_gif_frames(
            gif_path,
            target_size=(self.window_width, self.window_height),
            scale=self.cfg.window_scale,
        )
        self.frames = frames
        self.original_durations_ms = durations
        self.frame_index = 0
        self.scaled_durations_ms = self._make_scaled_durations(self.current_target_total_sec)

        if update_label and hasattr(self, "label") and self.frames:
            self.label.setPixmap(self.frames[0])

        if restart_timer and hasattr(self, "play_timer"):
            self.play_timer.stop()
            delay = self.scaled_durations_ms[0] if self.scaled_durations_ms else 80
            self.play_timer.start(delay)

        print(f"当前 GIF：{gif_path}")

    def show_next_gif(self) -> None:
        if len(self.gif_files) <= 1:
            print("fig 文件夹中只有一个 GIF，无法切换。")
            return

        old_index = self.current_gif_index
        for _ in range(len(self.gif_files)):
            self.current_gif_index = (self.current_gif_index + 1) % len(self.gif_files)
            try:
                self._load_current_gif(update_label=True, restart_timer=True)
                return
            except Exception as exc:
                bad_path = self._current_gif_path()
                print(f"加载 GIF 失败，跳过：{bad_path}\n原因：{exc}")
                # 继续尝试下一个。
                continue

        self.current_gif_index = old_index
        QMessageBox.warning(self, "GIF 切换失败", "fig 文件夹中的 GIF 都无法加载。")

    def _position_close_button(self) -> None:
        self.close_button.adjustSize()
        x = max(0, (self.width() - self.close_button.width()) // 2)
        y = 8
        self.close_button.move(x, y)
        self.close_button.raise_()

    def _make_scaled_durations(self, target_total_sec: float) -> List[int]:
        original_total_ms = sum(self.original_durations_ms)
        if original_total_ms <= 0:
            original_total_ms = 80 * len(self.frames)
        target_total_ms = max(120, int(target_total_sec * 1000))

        # 强制完整播放一遍时长 = 目标节拍周期；每帧最少 8ms，避免 0ms。
        scaled = [max(8, int(d / original_total_ms * target_total_ms)) for d in self.original_durations_ms]

        # 修正四舍五入误差：把差值补到最后一帧。
        diff = target_total_ms - sum(scaled)
        if scaled:
            scaled[-1] = max(8, scaled[-1] + diff)
        return scaled

    def start_loop(self) -> None:
        """启动后立即循环播放 GIF。"""
        self.frame_index = 0
        if self.frames:
            self.label.setPixmap(self.frames[0])
        self.play_timer.stop()
        delay = self.scaled_durations_ms[0] if self.scaled_durations_ms else 80
        self.play_timer.start(delay)

    def update_tempo(self, target_total_sec: float, bpm: float, beat_time: float, beat_count: int) -> None:
        """
        记录最新 BPM 和节拍相位；不重启、不跳帧。
        GIF 到达一轮边界时，会用这些信息调整下一轮的总时长，
        让下一次回到第 0 帧的时刻尽量贴近 beats_per_gif 的节拍边界。
        """
        self.pending_target_total_sec = max(0.12, float(target_total_sec))
        self.current_bpm = float(bpm)
        self.last_beat_time = float(beat_time)
        self.last_beat_count = int(beat_count)

    def _duration_to_next_beat_grid(self, now: float, nominal_duration: float) -> Optional[float]:
        """
        根据最近一次 beat 的时间戳和 BPM，预测下一个 beats_per_gif 边界距离现在还有多久。
        这里的“边界”不是音乐小节强拍，只是从程序检测到的 beat 计数开始，每 beats_per_gif 拍形成的网格。
        """
        if self.last_beat_time is None or self.last_beat_count is None or self.current_bpm <= 1e-6:
            return None

        beats_per_gif = max(1, int(self.cfg.beats_per_gif))
        beat_interval = 60.0 / float(self.current_bpm)
        if beat_interval <= 0:
            return None

        # 搜索未来若干个 beat，找到最近的“每 beats_per_gif 拍”的网格点。
        # 若当前距离下一个网格过近，会选择再下一个网格，避免下一轮 GIF 突然极短。
        min_duration = max(0.12, nominal_duration * 0.35)
        best_duration = None
        for m in range(1, beats_per_gif * 4 + 1):
            future_count = self.last_beat_count + m
            if future_count % beats_per_gif != 0:
                continue
            future_time = self.last_beat_time + m * beat_interval
            duration = future_time - now
            if duration >= min_duration:
                best_duration = duration
                break

        return best_duration

    def _apply_pending_tempo_at_loop_boundary(self) -> None:
        nominal = max(0.12, float(self.pending_target_total_sec))
        chosen = nominal

        if self.cfg.align_loop_to_beat:
            now = time.perf_counter()
            aligned = self._duration_to_next_beat_grid(now, nominal)
            if aligned is not None:
                # 为了避免速度突变，每轮最多只允许一定比例的拉伸/压缩。
                # 如果想更贴拍，可以把 max_loop_time_adjust_ratio 调大；如果想更平滑，可以调小。
                ratio = max(0.0, float(self.cfg.max_loop_time_adjust_ratio))
                low = nominal * max(0.10, 1.0 - ratio)
                high = nominal * (1.0 + ratio)
                chosen = min(max(aligned, low), high)

        self.current_target_total_sec = chosen
        self.scaled_durations_ms = self._make_scaled_durations(self.current_target_total_sec)

    def _next_frame(self) -> None:
        if not self.frames:
            return

        self.frame_index += 1
        if self.frame_index >= len(self.frames):
            # 一轮播放结束：此时才根据最近 BPM 调整下一轮速度，避免播放中途突变/重启。
            self._apply_pending_tempo_at_loop_boundary()
            self.frame_index = 0

        self.label.setPixmap(self.frames[self.frame_index])
        delay = self.scaled_durations_ms[self.frame_index] if self.scaled_durations_ms else 80
        self.play_timer.start(delay)

    def resizeEvent(self, event):
        self.label.resize(self.size())
        self._position_close_button()
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.press_global_pos = event.globalPosition().toPoint()
            self.did_drag = False
            event.accept()
        elif event.button() == Qt.RightButton:
            self._position_close_button()
            self.close_button.show()
            self.hide_close_timer.start(max(500, self.cfg.close_button_autohide_ms))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            current_pos = event.globalPosition().toPoint()
            if self.press_global_pos is not None:
                distance = (current_pos - self.press_global_pos).manhattanLength()
                if distance >= max(1, int(self.cfg.drag_threshold)):
                    self.did_drag = True

            if self.did_drag:
                self.move(current_pos - self.drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            was_click = not self.did_drag
            self.drag_offset = None
            self.press_global_pos = None
            self.did_drag = False

            if was_click and self.cfg.left_click_next_gif:
                self.show_next_gif()

            event.accept()
        else:
            self.drag_offset = None
            self.press_global_pos = None
            self.did_drag = False
            super().mouseReleaseEvent(event)


class AppController(QObject):
    def __init__(self, cfg: AppConfig, window: GifWindow):
        super().__init__()
        self.cfg = cfg
        self.window = window
        self.signals = BeatSignals()
        self.detector = BeatDetector(cfg, self.signals)

        self.signals.bpm_updated.connect(self.window.update_tempo)
        self.signals.error.connect(self.on_error)
        self.signals.status.connect(self.on_status)

    def start(self) -> None:
        self.window.start_loop()
        self.detector.start()

    def stop(self) -> None:
        self.detector.stop()

    def on_error(self, msg: str) -> None:
        QMessageBox.critical(
            self.window,
            "音频监听错误",
            "无法监听系统音频。\n\n"
            f"错误信息：{msg}\n\n"
            "建议：确认正在使用 Windows 输出设备播放音乐；若使用蓝牙/虚拟声卡，尝试切换到默认扬声器后重启程序。",
        )

    def on_status(self, msg: str) -> None:
        print(msg)


def main() -> int:
    cfg = load_config("config.json")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    try:
        window = GifWindow(cfg)
    except Exception as exc:
        QMessageBox.critical(None, "GIF 加载错误", str(exc))
        return 1

    controller = AppController(cfg, window)
    app.aboutToQuit.connect(controller.stop)
    window.show()
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())