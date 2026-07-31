# Remielle_OpenPet — opencode 状态桌宠

> 本项目由深度求索官方接口 DeepSeek-V4-Flash-0731 / DeepSeek-V4-Pro 在 OpenCode 制作
基于 [oc-claw](https://github.com/rainnoon/oc-claw) 的状态监控思路，为 [opencode](https://opencode.ai) CLI / Desktop 制作的桌宠。
素材取自绝区零官方的蕾米埃尔·丹（Remielle Dan）表情包，根据 opencode 实时状态切换动图。

## 工作原理

```
opencode (CLI/Desktop)                Remielle_OpenPet (Python + PySide6)
┌──────────────────────────┐    TCP       ┌──────────────────────────┐
│ remielle-openpet.js 插件 ├── 28888 ────▶│ pet.py                   │
│ 订阅事件总线             │ 单行JSON     │ 事件服务器 → 多会话状态机│
│ busy/reasoning/tool/     │              │ → 前台仲裁 → 动画切换    │
│ waiting/idle             │              │ idle/wait/thinking/      │
└──────────────────────────┘              │ reasoning_*/done         │
                                          └──────────────────────────┘
```

- **插件**：`~/.config/opencode/plugins/remielle-openpet.js`（opencode 自动加载，无需修改 opencode.json），订阅会话/消息/工具/权限事件并转发到本地 TCP 端口。
- **桌宠**：透明无边框置顶窗口，预加载全部 GIF 帧后无缝切换，可拖动、右键菜单调整。

## 安装

```
python install.py
```

脚本会自动：
1. 检查 Python 版本、安装依赖（PySide6）
2. 部署桌宠到 `~/.config/opencode/remielle-openpet/`（pet.py + 动图素材）
3. 安装插件到 `~/.config/opencode/plugins/`
4. 生成启动脚本 `start.bat`（Windows）/ `start.sh`（macOS/Linux）

然后**重启 opencode**（插件在启动时加载）。之后无需手动开桌宠。

**零硬编码**：插件通过 `os.homedir() + .config/opencode/remielle-openpet/pet.py` 定位桌宠，源码不包含任何用户特定路径；项目可放在任意位置、可删除（卸载：`python uninstall.py`）。

## 状态映射

| opencode 事件 | 动画 | 说明 |
|---|---|---|
| 用户提交 prompt | `thinking` | 模型思考中（直到首个输出） |
| 首个推理/正文输出 | `reasoning_short` → `reasoning_long_playing` | 推理开始 5s 内 → 5s 后 |
| 工具执行中 / 权限等待 | `wait` | 等待工具完成 / 等待用户选择 |
| 工具完成 | `thinking` | 模型思考下一步 |
| 会话完成 (idle) | `done` → 5s 后 `idle` | 推理完成 → 待命 |

## 多会话支持

opencode TUI / Desktop 可同时开多个会话，甚至多个 opencode 进程并存。桌宠维护**每个会话独立的状态机**，前台仲裁在桌宠端统一完成：

- **前台 = 最近有事件的会话**：任何会话的新活动（提交/推理/工具/完成）都会使其成为前台，桌宠实时跟随
- 会话完成显示 `done`；其他会话有活动则立即切换过去
- 子任务会话（`task` 工具派生）不参与，避免子任务刷屏

## 启停行为

- **随 opencode 启停**：opencode 启动时插件拉起桌宠（若未运行）并传入自身 PID；桌宠每 0.325 秒检测父进程，PID 消失（正常退出/强杀）后自行退出
- **手动常驻**：运行部署目录的 `start.bat`（Windows）/ `start.sh`（macOS/Linux）启动的桌宠常驻，不随 opencode 启停

## 使用

- **拖动**：鼠标左键拖动窗口
- **右键菜单**：查看当前状态 / 缩放 / 置顶 / 预览动画 / 退出
- **配置**：部署目录 `config.json`（端口、缩放、位置、各阶段时长）
- **日志**：部署目录 `pet.log`

## 项目结构（安装源）

```
Remielle_OpenPet/
├── install.py           # 一键安装
├── uninstall.py         # 卸载
├── start.bat            # 源码模式手动启动 (Windows)
├── start.sh             # 源码模式手动启动 (macOS/Linux)
├── pet.py               # 桌宠主程序
├── remielle-openpet.js  # opencode 插件（安装时复制, 无硬编码路径）
└── gifs/                # 动图素材
```

安装后运行时文件在 `~/.config/opencode/remielle-openpet/`（含 config.json、pet.log、start.bat / start.sh）。

## 平台支持

| 平台 | 进程检测 | 桌宠启动 | 启动脚本 |
|---|---|---|---|
| Windows | OpenProcess + exe 校验 | `pythonw`（无控制台） | `start.bat` |
| macOS / Linux | `os.kill(pid, 0)` 信号探测 | `python3` | `start.sh` |

- 安装、插件、事件转发、状态机逻辑完全跨平台（`~/.config/opencode/` 为 opencode 官方标准配置目录）
- macOS 首次运行需授予辅助功能权限（如窗口置顶被系统拦截）
- Linux 需桌面环境（X11/Wayland）与 `python3-pyqt6` 或 pip 安装的 PySide6

## 注意

- 插件转发所有非子任务会话的事件（带 `sessionId`），前台会话仲裁由桌宠统一完成，跨进程（TUI/Desktop/run）一致。
- 动图素材来自 `E:\HornLeaf_T\Desktop\新建文件夹\蕾米动图`（含 `Instruction.txt` 状态说明）。
- 升级安装：重跑 `python install.py` 即可（保留已有 config.json 配置）。
