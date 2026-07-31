#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remielle_OpenPet — 连接 opencode 的状态切换桌宠（蕾米埃尔·丹）

原理
----
1. opencode 侧: ~/.config/opencode/plugins/remielle-openpet.js 订阅 opencode 事件总线,
   把归一化事件通过 TCP(127.0.0.1:28888) 单行 JSON 推送到本程序。
2. 本程序: PySide6 透明无边框置顶窗口, 预加载全部 GIF 帧, 按状态机切换动画。

事件协议 (插件 → 桌宠, 每行一个 JSON):
    {"t":"busy",     "sessionId":"...", "cwd":"..."}        会话开始处理(用户提交)
    {"t":"reasoning","sessionId":"...", "cwd":"..."}        首个推理/正文输出(推理开始)
    {"t":"tool_run", "name":"bash"}                         工具开始执行
    {"t":"tool_done","name":"bash"}                         工具执行完成
    {"t":"waiting",  "why":"permission"}                    权限/提问等待用户
    {"t":"idle"}                                            会话完成(idle)

启停: 插件以 --parent <opencode PID> 拉起桌宠, 桌宠每 3 秒检测父进程,
      PID 消失(正常退出/强杀)后自行退出。手动启动(start.bat)则常驻。

状态机 (对应素材 Instruction.txt):
    idle                待命 / 推理完成 5 秒后
    thinking            思考时(提交后模型思考 / 工具完成后思考下一步)
    wait                等待工具执行完成 / 权限提问等待用户
    reasoning_short     开始推理的 5 秒内(首个推理/正文输出到达)
    reasoning_long_playing  开始推理的 5 秒后
    done                推理完成(显示 5 秒后回 idle)
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

# Windows 进程检测依赖 ctypes(仅 win32 可用)
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImageReader, QPixmap
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget

BASE_DIR = Path(__file__).resolve().parent
GIF_DIR = BASE_DIR / "gifs"
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "pet.log"

# 状态 -> GIF 文件
STATE_FILES = {
    "idle": "idle.gif",
    "thinking": "thinking.gif",
    "wait": "wait.gif",
    "done": "done.gif",
    "reasoning_short": "reasoning_short.gif",
    "reasoning_long_playing": "reasoning_long_playing.gif",
}

DEFAULT_CONFIG = {
    "port": 28888,               # 插件推送端口
    "scale": 0.8,                # 显示缩放(相对 GIF 原始尺寸)
    "always_on_top": True,       # 置顶
    "x": None,                   # 窗口位置(首次启动定位屏幕右下)
    "y": None,
    "thinking_sec": 2.0,         # 不再使用(thinking 由事件驱动时长), 保留兼容
    "reasoning_switch_sec": 5.0, # 推理累计时长, 超过后切 reasoning_long_playing
    "done_hold_sec": 5.0,        # done 显示时长, 之后回 idle
}


def pid_alive(pid):
    """检查进程是否存在, 跨平台:
    - Windows: OpenProcess + exe 名校验(防 PID 复用误判)
    - Linux/macOS: os.kill(pid, 0) 信号探测"""
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return "opencode" in buf.value.lower()
            return True  # 查询失败但进程存在, 保守视为存活
        finally:
            kernel32.CloseHandle(h)
    # Unix: signal 0 探测 — 成功=存在, ProcessLookupError=不存在, PermissionError=存在但无权限
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            logging.exception("config.json 解析失败, 使用默认配置")
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        logging.exception("保存 config.json 失败")


def load_gif_frames(path, scale):
    """读取 GIF 全部帧(已合成, 含透明)与帧延迟(毫秒)。"""
    reader = QImageReader(str(path))
    frames, delays = [], []
    while True:
        img = reader.read()
        if img.isNull():
            break
        if scale != 1.0:
            img = img.scaled(
                max(1, int(img.width() * scale)),
                max(1, int(img.height() * scale)),
                Qt.KeepAspectRatio, Qt.SmoothTransformation)
        frames.append(QPixmap.fromImage(img))
        # GIF 延迟为 0 的帧(常见于加速过的动图)补一个下限, 避免超速闪烁
        delays.append(max(reader.nextImageDelay(), 20))
    return frames, delays

class EventServer(QObject):
    """TCP 事件接收端: 监听插件推送, 按行解析 JSON 并转发为信号。"""

    eventReceived = Signal(dict)

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.cfg_port = port
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._buffers = {}  # socket -> bytes 行缓冲
        if not self._server.listen(QHostAddress.LocalHost, port):
            logging.error("监听端口 %d 失败(可能桌宠已运行?)", port)

    def _on_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))

    def _on_ready_read(self, sock):
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf += sock.readAll().data()
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    self.eventReceived.emit(event)
            except Exception:
                logging.warning("无法解析事件: %r", line[:200])
        self._buffers[sock] = buf

    def _on_disconnected(self, sock):
        self._buffers.pop(sock, None)

    def heartbeat(self):
        """监听自愈: 端口意外丢失时重新监听。"""
        if not self._server.isListening():
            logging.warning("监听丢失, 重新监听 %d", self.cfg_port)
            self._server.listen(QHostAddress.LocalHost, self.cfg_port)


class SessionState(QObject):
    """单个会话的状态机: 把归一化事件映射为动画状态。"""

    stateChanged = Signal(str)

    def __init__(self, sid, cfg, parent=None):
        super().__init__(parent)
        self.sid = sid
        self.cfg = cfg
        self._state = "idle"
        self._last_event_time = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(lambda: self._set_state("idle"))

    @property
    def state(self):
        return self._state

    def _set_state(self, name):
        if name == self._state:
            return
        self._state = name
        logging.info("会话 %s 状态 -> %s", self.sid[:12], name)
        self.stateChanged.emit(name)

    def heartbeat(self):
        """兜底: 该会话长时间无事件时回到 idle, 避免卡在中间状态。"""
        if self._state != "idle" and time.monotonic() - self._last_event_time > 120:
            logging.info("会话 %s 无事件超过 120s, 回到 idle", self.sid[:12])
            self._timer.stop()
            self._set_state("idle")

    def handle_event(self, event):
        self._last_event_time = time.monotonic()
        t = event.get("t")
        try:
            if t == "busy":          # 用户提交, 模型开始思考(等待输出)
                self._timer.stop()
                self._set_state("thinking")
            elif t == "reasoning":   # 首个推理/文本输出, 推理开始
                self._enter_reasoning()
            elif t == "tool_run":    # 工具执行中
                self._timer.stop()
                self._set_state("wait")
            elif t == "tool_done":   # 工具完成, 模型重新思考下一步
                self._timer.stop()
                self._set_state("thinking")
            elif t == "waiting":     # 权限/提问等待用户
                self._timer.stop()
                self._set_state("wait")
            elif t == "idle":        # 会话完成
                self._timer.stop()
                self._set_state("done")
                self._done_timer.start(int(self.cfg["done_hold_sec"] * 1000))
        except Exception:
            logging.exception("会话 %s 处理事件失败: %r", self.sid[:12], event)

    def _enter_reasoning(self):
        """推理输出阶段: reasoning_short(5秒内) -> reasoning_long_playing。
        已在推理输出中则保持, 不重置计时(流式输出会持续到达 reasoning 事件)。"""
        if self._state in ("reasoning_short", "reasoning_long_playing"):
            return
        self._timer.stop()
        self._set_state("reasoning_short")
        self._timer.start(int(self.cfg["reasoning_switch_sec"] * 1000))

    def _on_timer(self):
        if self._state == "reasoning_short":
            self._set_state("reasoning_long_playing")


class PetEngine(QObject):
    """多会话状态仲裁: 维护每个会话的状态机, 前台会话(最近有事件)驱动动画。

    前台规则: 任何会话的新事件都使其成为前台 — 桌宠始终反映"最近活动"
    的会话。TUI/Desktop 同时开多个会话甚至多个 opencode 进程时, 桌宠
    跟随用户当前正在交互的会话; 会话完成(idle)后显示 done, 其他会话
    有活动则立即切换过去。"""

    stateChanged = Signal(str)
    requestExit = Signal()  # 父进程(启动者)消失, 请求退出

    def __init__(self, cfg, parent_pid=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.parent_pid = parent_pid  # 启动本桌宠的 opencode 进程 PID(None = 手动启动, 常驻)
        self._sessions = {}    # sessionId -> SessionState
        self._front_sid = None  # 当前前台会话
        self._parent_missing = 0  # 连续未检测到父进程的次数
        if parent_pid:
            self._parent_timer = QTimer(self)
            self._parent_timer.timeout.connect(self._check_parent)
            self._parent_timer.start(325)  # 每 0.325 秒检查父进程

    @property
    def state(self):
        front = self._sessions.get(self._front_sid) if self._front_sid else None
        return front.state if front else "idle"

    def _check_parent(self):
        """父进程(启动本桌宠的 opencode)消失 → 自行退出。
        连续 2 次确认(约 0.65 秒), 避免瞬时检测误差。"""
        if not self.parent_pid:
            return
        if pid_alive(self.parent_pid):
            self._parent_missing = 0
            return
        self._parent_missing += 1
        if self._parent_missing >= 2:
            logging.info("启动者进程(pid=%d)已退出, 桌宠自动关闭", self.parent_pid)
            self.requestExit.emit()

    def heartbeat(self):
        """兜底: 各会话长时间无事件时回到 idle。"""
        for st in self._sessions.values():
            st.heartbeat()

    def handle_event(self, event):
        sid = event.get("sessionId") or "_default"
        st = self._sessions.get(sid)
        if st is None:
            st = SessionState(sid, self.cfg, self)
            st.stateChanged.connect(self._on_session_state_changed)
            self._sessions[sid] = st
            logging.info("新会话: %s (当前会话数 %d)", sid[:12], len(self._sessions))
        # 最新事件的会话成为前台(桌宠跟随最近活动的会话)
        self._front_sid = sid
        st.handle_event(event)

    def _on_session_state_changed(self, state):
        st = self.sender()
        if st is self._sessions.get(self._front_sid):
            self.stateChanged.emit(state)


class PetWindow(QWidget):
    """透明置顶桌宠窗口。"""

    def __init__(self, cfg, parent_pid=None):
        super().__init__()
        self.cfg = cfg
        self._scale = float(cfg.get("scale", 0.8))
        self._frame_cache = {}   # (state, scale) -> (frames, delays)
        self._state = None
        self._frame_index = 0
        self._drag_offset = None
        self._preview_mode = False  # 手动预览中(新状态到来会立即打断)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._exit_preview)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)

        self.engine = PetEngine(cfg, parent_pid=parent_pid, parent=self)
        self.engine.stateChanged.connect(self._on_state_changed)

        self._build_menu()
        self._apply_always_on_top()
        self._restore_position()

        # 先预加载 idle 并显示, 避免窗口空白
        self._on_state_changed("idle")

    # ── 动画播放 ──────────────────────────────────────────────
    def _frames(self, state):
        key = (state, self._scale)
        if key not in self._frame_cache:
            path = GIF_DIR / STATE_FILES[state]
            frames, delays = load_gif_frames(path, self._scale)
            if not frames:
                logging.error("GIF 加载失败: %s", path)
                raise RuntimeError(f"GIF 加载失败: {path}")
            self._frame_cache[key] = (frames, delays)
        return self._frame_cache[key]

    def _play(self, state):
        self._state = state
        self._frame_index = 0
        frames, delays = self._frames(state)
        self._label.setPixmap(frames[0])
        self._label.resize(frames[0].size())
        self.resize(frames[0].size())
        self._timer.start(max(delays[0], 20))

    def _next_frame(self):
        frames, delays = self._frames(self._state)
        self._frame_index = (self._frame_index + 1) % len(frames)
        self._label.setPixmap(frames[self._frame_index])
        self._timer.start(max(delays[self._frame_index], 20))

    def _on_state_changed(self, state):
        # opencode 有任何新状态 → 立即打断预览, 保证状态机永远不被预览卡住
        if self._preview_mode:
            self._preview_mode = False
            self._preview_timer.stop()
        self._play(state)

    def reload_scale(self):
        """缩放变化后重载所有帧缓存并重播当前动画。"""
        self._frame_cache.clear()
        if self._state:
            self._play(self._state)

    # ── 窗口行为 ──────────────────────────────────────────────
    def _apply_always_on_top(self):
        flags = self.windowFlags()
        if self.cfg.get("always_on_top", True):
            self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
        self.show()

    def _restore_position(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            # 默认贴屏幕右下角
            x = screen.right() - 500
            y = screen.bottom() - 500
        self.move(int(x), int(y))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.cfg["x"], self.cfg["y"] = self.x(), self.y()
            save_config(self.cfg)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self._menu.exec(event.globalPos())

    # ── 右键菜单 ──────────────────────────────────────────────
    def _build_menu(self):
        self._menu = QMenu(self)
        self._status_action = self._menu.addAction("状态: idle")
        self._status_action.setEnabled(False)
        self._menu.addSeparator()

        scale_menu = self._menu.addMenu("缩放")
        for s in (0.5, 0.75, 1.0):
            act = QAction(f"{int(s * 100)}%", self)
            act.setCheckable(True)
            act.setChecked(abs(self._scale - s) < 0.01)
            act.triggered.connect(lambda _, v=s: self._set_scale(v))
            scale_menu.addAction(act)
        self._scale_actions = scale_menu.actions()

        self._top_action = QAction("置顶", self)
        self._top_action.setCheckable(True)
        self._top_action.setChecked(self.cfg.get("always_on_top", True))
        self._top_action.toggled.connect(self._toggle_top)
        self._menu.addAction(self._top_action)

        preview_menu = self._menu.addMenu("预览动画")
        for name in STATE_FILES:
            act = QAction(name, self)
            act.triggered.connect(lambda _, n=name: self._preview(n))
            preview_menu.addAction(act)
        self._menu.addSeparator()
        self._menu.addAction("退出", QApplication.quit)

    def _set_scale(self, s):
        self._scale = s
        self.cfg["scale"] = s
        save_config(self.cfg)
        for act in self._scale_actions:
            act.setChecked(abs(self._scale - float(act.text()[:-1]) / 100) < 0.01)
        self.reload_scale()

    def _toggle_top(self, checked):
        self.cfg["always_on_top"] = checked
        save_config(self.cfg)
        self._apply_always_on_top()

    def _preview(self, name):
        """手动预览动画: 3 秒无活动后恢复自动; 有新状态立即切回。"""
        self._preview_mode = True
        self._preview_timer.start(3000)
        self._play(name)

    def _exit_preview(self):
        if not self._preview_mode:
            return
        self._preview_mode = False
        self._play(self.engine.state)


def main():
    # --parent <pid>: 由 opencode 插件拉起, 父进程消失后桌宠自行退出;
    # 手动启动(start.bat)不带此参数, 桌宠常驻。
    parent_pid = None
    if "--parent" in sys.argv:
        idx = sys.argv.index("--parent")
        if idx + 1 < len(sys.argv):
            try:
                parent_pid = int(sys.argv[idx + 1])
            except ValueError:
                parent_pid = None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout) if sys.stdout else logging.NullHandler(),
        ],
    )
    logging.info("Remielle_OpenPet 启动(parent_pid=%s), 工作目录: %s", parent_pid, BASE_DIR)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    cfg = load_config()
    window = PetWindow(cfg, parent_pid=parent_pid)

    server = EventServer(int(cfg.get("port", 28888)), app)
    server.eventReceived.connect(window.engine.handle_event)
    window.engine.requestExit.connect(app.quit)

    # 心跳: 监听自愈 + 兜底状态(长时间无事件时回 idle)
    heartbeat = QTimer(app)
    heartbeat.timeout.connect(server.heartbeat)
    heartbeat.timeout.connect(window.engine.heartbeat)
    heartbeat.start(15000)

    logging.info("监听 127.0.0.1:%d 等待 opencode 事件", int(cfg.get("port", 28888)))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
