// Remielle_OpenPet — opencode 状态转发插件
// 安装位置: ~/.config/opencode/plugins/remielle-openpet.js (自动加载, 无需注册到 opencode.json)
// 作用: 订阅 opencode 事件总线, 把归一化事件通过 TCP 127.0.0.1:28888 推送给桌宠。
//
// 事件形状 (与 opencode 1.16+ / oc-claw 验证一致, 官方文档确认事件名未变):
//   - session.created:        properties.info.id / .directory / .parentID
//   - session.status:         properties.sessionID, properties.status.type ("busy"|"idle")
//   - session.idle:           会话完成 (部分版本直接发这个)
//   - message.updated:        properties.info.{id, sessionID, role}
//   - message.part.updated:   properties.part.{sessionID, type, text, tool, state.status}
//   - permission.asked / question.asked: 等待用户 (映射为 waiting)
//
// 输出协议 (每行一个 JSON, 所有非子任务会话都会转发, 带 sessionId):
//   {"t":"busy", ...}        会话开始处理(用户提交 prompt)
//   {"t":"reasoning", ...}   assistant 文本流(推理中)
//   {"t":"tool_run", name}   工具开始执行
//   {"t":"tool_done", name}  工具执行完成
//   {"t":"waiting", why}     权限/提问等待用户
//   {"t":"idle"}             会话完成
//
// 多会话: opencode (TUI/Desktop/run) 可同时开多个会话, 甚至多个 opencode
// 进程并存。本插件转发所有非子任务会话的事件; "前台会话"仲裁由桌宠端统一
// 完成(跨进程唯一仲裁点), 桌宠始终显示最近活动的会话状态。
// 子任务会话(task 工具派生, 携带 parentID)不转发, 避免子任务刷屏。

import net from "node:net";
import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";

const PORT = 28888;
const HOST = "127.0.0.1";

// ── 随 opencode 启动桌宠 ─────────────────────────────────────
// opencode 初始化插件时, 若桌宠未运行则 spawn 并传入本进程 PID (--parent)。
// 桌宠端每 0.325 秒检查该 PID: 进程消失(正常退出/强杀)后桌宠自行退出。
// 只拉起不管理: 手动 start.bat 启动的常驻桌宠不受影响(端口探测跳过)。
//
// 桌宠脚本位置: install.py 部署到 opencode 标准配置目录
//   ~/.config/opencode/remielle-openpet/pet.py — 零硬编码, 跨平台
// 可用环境变量 remielle_openpet_SCRIPT 覆盖(高级用法)。
const PET_SCRIPT = process.env.remielle_openpet_SCRIPT
  || path.join(os.homedir(), ".config", "opencode", "remielle-openpet", "pet.py");

function isPetRunning(cb) {
  const sock = net.createConnection({ host: HOST, port: PORT });
  let settled = false;
  const finish = (ok) => {
    if (settled) return;
    settled = true;
    try { sock.destroy(); } catch {}
    cb(ok);
  };
  sock.setTimeout(500);
  sock.on("connect", () => finish(true));
  sock.on("error", () => finish(false));
  sock.on("timeout", () => finish(false));
}

function launchPet() {
  isPetRunning((running) => {
    if (running) return; // 桌宠已在运行(手动启动或已被其他 opencode 进程拉起)
    try {
      // Windows 用 pythonw(无控制台窗口), Unix 用 python3
      const python = process.platform === "win32" ? "pythonw" : "python3";
      const child = spawn(python, [PET_SCRIPT, "--parent", String(process.pid)], {
        cwd: path.dirname(PET_SCRIPT),
        detached: true,
        stdio: "ignore",
        windowsHide: true,
      });
      child.unref();
    } catch {}
  });
}

// ── 事件转发 ─────────────────────────────────────────────────

// 子任务会话 id — 不转发
const subtaskSessions = new Set();
// 会话 id -> cwd
const sessionCwd = new Map();
// messageID -> { role, sessionID }, 用于判断文本部分属于用户还是 assistant
const msgRoles = new Map();
// 会话 id -> { pendingWait, lastSent } (per-session 状态)
const sessionMeta = new Map();

function smeta(sid) {
  let m = sessionMeta.get(sid);
  if (!m) {
    m = { pendingWait: false, lastSent: null };
    sessionMeta.set(sid, m);
  }
  return m;
}

// fire-and-forget: 桌宠未运行时绝不能拖慢 opencode
function send(payload) {
  let sock;
  try {
    sock = net.createConnection({ host: HOST, port: PORT });
  } catch {
    return;
  }
  const done = () => {
    try { sock.destroy(); } catch {}
  };
  sock.setTimeout(1000);
  sock.on("error", done);
  sock.on("timeout", done);
  sock.on("connect", () => {
    try { sock.end(JSON.stringify(payload) + "\n"); } catch { done(); }
  });
}

function emit(sessionId, t, extra) {
  if (!sessionId) return;
  if (subtaskSessions.has(sessionId)) return;

  // per-session 去重: 流式 part 更新很频繁, 只发每个会话每种事件的第一个
  const m = smeta(sessionId);
  const dedupKey = t + "|" + JSON.stringify(extra || {});
  if (dedupKey === m.lastSent) return;
  m.lastSent = dedupKey;

  send(Object.assign({ t, sessionId, cwd: sessionCwd.get(sessionId) || "" }, extra || {}));
}

export const RemielleOpenPet = async (ctx) => {
  // opencode 启动时确保桌宠在运行
  launchPet();
  const initCwd = ctx && typeof ctx.directory === "string" ? ctx.directory : "";
  return {
    event: async ({ event }) => {
      try {
        if (!event || typeof event.type !== "string") return;
        const t = event.type;
        const p = event.properties || {};

        switch (t) {
          case "session.created": {
            const info = p.info || {};
            if (!info.id) return;
            sessionCwd.set(info.id, info.directory || initCwd || "");
            // 子任务会话不转发
            if (info.parentID) subtaskSessions.add(info.id);
            return;
          }

          case "session.status": {
            const sid = p.sessionID;
            if (!sid) return;
            const type = p.status && p.status.type;
            if (type === "idle") {
              smeta(sid).pendingWait = false;
              emit(sid, "idle");
            } else if (type === "busy") {
              emit(sid, "busy");
            }
            return;
          }

          // 部分版本直接发 session.idle 而不走 session.status
          case "session.idle": {
            const sid = p.sessionID;
            if (sid) smeta(sid).pendingWait = false;
            emit(sid, "idle");
            return;
          }

          case "session.error": {
            const sid = p.sessionID;
            if (sid) smeta(sid).pendingWait = false;
            emit(sid, "idle");
            return;
          }

          case "message.updated": {
            const info = p.info || {};
            if (info && info.id && info.sessionID) {
              msgRoles.set(info.id, { role: info.role, sessionID: info.sessionID });
              if (msgRoles.size > 200) msgRoles.delete(msgRoles.keys().next().value);
            }
            return;
          }

          case "message.part.updated": {
            const part = p.part;
            if (!part || typeof part !== "object") return;

            if (part.type === "text") {
              const meta = msgRoles.get(part.messageID);
              if (!meta) return;
              if (meta.role === "user" && part.text) {
                // 用户提交了 prompt — 会话开始处理
                emit(meta.sessionID, "busy");
              } else if (meta.role === "assistant" && part.text) {
                // 无思维链模型的正文输出 = 推理开始
                emit(meta.sessionID, "reasoning");
              }
              return;
            }

            if (part.type === "reasoning") {
              // 思维链输出 = 推理开始(流式, per-session 去重后仅首个到达桌宠)
              emit(part.sessionID, "reasoning");
              return;
            }

            if (part.type === "tool") {
              const sid = part.sessionID;
              if (!sid) return;
              const st = part.state && part.state.status;
              const toolName = part.tool || "";
              // question/ask 这类交互工具在等待用户期间会一直保持 running
              // 并持续推送 part 更新 — 必须映射为 waiting, 不能覆盖回工作状态
              const tl = toolName.toLowerCase();
              const interactive = tl === "question" || tl === "ask"
                || tl.includes("question") || tl.includes("ask_user");
              const m = smeta(sid);
              if (st === "running" || st === "pending") {
                if (interactive || m.pendingWait) {
                  emit(sid, "waiting", { why: "permission" });
                } else {
                  emit(sid, "tool_run", toolName ? { name: toolName } : null);
                }
              } else if (st === "completed" || st === "error") {
                m.pendingWait = false;
                emit(sid, "tool_done", toolName ? { name: toolName } : null);
              }
              return;
            }
            return;
          }

          // 权限/提问 — opencode 阻塞等待用户在 TUI 选择
          case "permission.asked":
          case "question.asked": {
            const sid = p.sessionID;
            if (sid) smeta(sid).pendingWait = true;
            emit(sid, "waiting", { why: "permission" });
            return;
          }

          case "permission.replied":
          case "question.replied":
          case "question.rejected": {
            const sid = p.sessionID;
            if (sid) smeta(sid).pendingWait = false;
            emit(sid, "tool_done");
            return;
          }

          default:
            return;
        }
      } catch {}
    },
  };
};

export default RemielleOpenPet;
