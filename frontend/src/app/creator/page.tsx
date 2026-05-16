"use client";

import { useMemo, useState } from "react";

type TaskStatus = "running" | "paused" | "cancelled" | "completed";
type MessageRole = "assistant" | "user" | "system";

interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  linkedTaskId?: string;
}

interface CreatorThread {
  id: string;
  title: string;
  active?: boolean;
}

interface WorkflowTask {
  id: string;
  title: string;
  stage: "strategy" | "generation" | "completed";
  status: TaskStatus;
  progress: number;
}

const initialThreads: CreatorThread[] = [
  { id: "chat-outdoor", title: "户外选题文案生成", active: true },
  { id: "chat-brand-copy", title: "品牌文案优化" }
];

const initialMessages: ChatMessage[] = [
  {
    id: "msg-welcome",
    role: "assistant",
    text: "你好，我是品牌内容增长助手。你可以让我生成选题、内容策略、笔记草稿，也可以在后台任务执行时继续补充约束。"
  }
];

function createId(prefix: string) {
  return `${prefix}-${Math.random().toString(16).slice(2, 10)}`;
}

function inferTaskIntent(text: string) {
  return /生成|选题|策略|文案|笔记|脚本|内容/.test(text);
}

function renderStage(stage: WorkflowTask["stage"]) {
  if (stage === "strategy") return "策略生成";
  if (stage === "generation") return "笔记生成";
  return "已完成";
}

function renderStatus(status: TaskStatus) {
  if (status === "running") return "运行中";
  if (status === "paused") return "已暂停";
  if (status === "cancelled") return "已中断";
  return "已完成";
}

export default function CreatorPage() {
  const [threads, setThreads] = useState(initialThreads);
  const [messages, setMessages] = useState(initialMessages);
  const [input, setInput] = useState("");
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const [activeMenuId, setActiveMenuId] = useState<string | null>(null);
  const [task, setTask] = useState<WorkflowTask | null>(null);

  const activeThread = useMemo(
    () => threads.find((thread) => thread.active) ?? threads[0],
    [threads]
  );

  function selectThread(threadId: string) {
    setThreads((current) =>
      current.map((thread) => ({ ...thread, active: thread.id === threadId }))
    );
    setActiveMenuId(null);
  }

  function startNewThread() {
    const next: CreatorThread = {
      id: createId("chat"),
      title: "新的创作对话",
      active: true
    };
    setThreads((current) => [
      next,
      ...current.map((thread) => ({ ...thread, active: false }))
    ]);
    setMessages(initialMessages);
    setTask(null);
  }

  function appendMessage(message: Omit<ChatMessage, "id">) {
    setMessages((current) => [...current, { ...message, id: createId("msg") }]);
  }

  function startWorkflowTask(text: string) {
    const nextTask: WorkflowTask = {
      id: createId("job"),
      title: text.length > 22 ? `${text.slice(0, 22)}...` : text,
      stage: "strategy",
      status: "running",
      progress: 28
    };
    setTask(nextTask);
    appendMessage({
      role: "assistant",
      linkedTaskId: nextTask.id,
      text: "我已把这个需求转成后台创作任务。任务执行时你仍然可以继续补充要求，我会把补充内容作为同一对话线程里的上下文。"
    });
  }

  function sendMessage(textOverride?: string) {
    const text = (textOverride ?? input).trim();
    if (!text) return;

    setInput("");
    appendMessage({ role: "user", text });

    if (inferTaskIntent(text) && (!task || task.status !== "running")) {
      startWorkflowTask(text);
      return;
    }

    if (task?.status === "running") {
      appendMessage({
        role: "assistant",
        text: "已收到补充信息。当前后台任务会继续执行；真实后端接入后，这类消息会先进入 intent router，判断是补充约束、查进度、暂停任务还是普通问答。"
      });
      return;
    }

    appendMessage({
      role: "assistant",
      text: "已收到。这个版本先保留为前端对话原型，后续会接入轻量 chat endpoint 和 workflow session。"
    });
  }

  function pauseTask() {
    if (!task || task.status !== "running") return;
    setTask({ ...task, status: "paused" });
    appendMessage({
      role: "system",
      text: "任务已暂停：后续后端会将 queued/retrying 任务置为 paused，running 任务在阶段边界检查 pause/cancel flag。"
    });
  }

  function resumeTask() {
    if (!task || !["paused", "cancelled"].includes(task.status)) return;
    setTask({
      ...task,
      status: "running",
      stage: task.progress >= 60 ? "generation" : "strategy",
      progress: Math.max(task.progress, 58)
    });
    appendMessage({
      role: "system",
      text: "任务已恢复：后续真实实现会调用 resume，把 paused job 重新放回队列。"
    });
  }

  function finishTask() {
    if (!task) return;
    setTask({ ...task, status: "completed", stage: "completed", progress: 100 });
    appendMessage({
      role: "assistant",
      text: "任务完成。这里会展示策略摘要、候选提案和生成笔记，并支持回写到选题库或发布记录。"
    });
  }

  function beginEdit(message: ChatMessage) {
    setEditingMessageId(message.id);
    setEditingText(message.text);
  }

  function cancelEdit() {
    setEditingMessageId(null);
    setEditingText("");
  }

  function resendEdited(messageId: string) {
    const messageIndex = messages.findIndex((message) => message.id === messageId);
    if (messageIndex < 0) return;
    setMessages((current) => current.slice(0, messageIndex));
    setEditingMessageId(null);
    sendMessage(editingText);
    setEditingText("");
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] min-h-[640px] overflow-hidden rounded-none border-x border-line bg-white">
      <aside className="hidden w-[280px] shrink-0 flex-col border-r border-line bg-slate-50 md:flex">
        <div className="flex-1 overflow-y-auto p-4">
          <button
            type="button"
            onClick={startNewThread}
            className="mb-4 flex w-full items-center justify-center rounded-lg border border-line bg-white px-3 py-2 text-sm font-medium text-ink transition hover:bg-slate-100"
          >
            + 新建对话
          </button>
          <div className="space-y-1">
            {threads.map((thread) => (
              <div
                key={thread.id}
                className={[
                  "relative flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 text-sm",
                  thread.active ? "bg-white font-medium text-ink shadow-sm" : "text-slate-600 hover:bg-white"
                ].join(" ")}
                onClick={() => selectThread(thread.id)}
              >
                <span className="min-w-0 flex-1 truncate">{thread.title}</span>
                <button
                  type="button"
                  aria-label="对话操作"
                  onClick={(event) => {
                    event.stopPropagation();
                    setActiveMenuId(activeMenuId === thread.id ? null : thread.id);
                  }}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-lg text-slate-400 hover:bg-slate-100 hover:text-ink"
                >
                  ...
                </button>
                {activeMenuId === thread.id ? (
                  <div className="absolute right-2 top-9 z-10 w-36 rounded-lg border border-line bg-white py-1 text-sm shadow-panel">
                    <button type="button" className="block w-full px-4 py-2 text-left hover:bg-slate-50">
                      置顶
                    </button>
                    <button type="button" className="block w-full px-4 py-2 text-left hover:bg-slate-50">
                      重命名
                    </button>
                    <button type="button" className="block w-full px-4 py-2 text-left hover:bg-slate-50">
                      分享
                    </button>
                    <button type="button" className="block w-full px-4 py-2 text-left text-danger hover:bg-dangerBg">
                      删除
                    </button>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>

        <div className="border-t border-line p-4">
          <label className="mb-1 block text-xs font-medium uppercase text-quiet">AI Models</label>
          <select className="mb-3 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-ink">
            <option>GPT-4o</option>
            <option>Claude 3.5</option>
            <option>DeepSeek</option>
            <option>通义千问</option>
          </select>
          <label className="mb-1 block text-xs font-medium uppercase text-quiet">API Key</label>
          <input
            className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink outline-none focus:border-ink"
            placeholder="本地开发密钥"
            type="password"
          />
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-line px-5 py-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <p className="text-xs uppercase text-quiet">Creator Thread</p>
              <h1 className="mt-1 text-xl font-semibold text-ink">{activeThread.title}</h1>
            </div>
            {task ? (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-slate-50 px-3 py-2 text-sm">
                <span className="font-medium text-ink">{renderStage(task.stage)}</span>
                <span className="text-quiet">{renderStatus(task.status)}</span>
                <span className="text-quiet">{task.progress}%</span>
                <button
                  type="button"
                  onClick={pauseTask}
                  disabled={task.status !== "running"}
                  className="rounded-md border border-line bg-white px-2 py-1 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  停止
                </button>
                <button
                  type="button"
                  onClick={resumeTask}
                  disabled={!["paused", "cancelled"].includes(task.status)}
                  className="rounded-md border border-line bg-white px-2 py-1 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  恢复
                </button>
                <button
                  type="button"
                  onClick={finishTask}
                  disabled={task.status !== "running"}
                  className="rounded-md border border-line bg-white px-2 py-1 text-xs text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  完成
                </button>
              </div>
            ) : null}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-5">
            {messages.map((message) => {
              const isUser = message.role === "user";
              const isSystem = message.role === "system";
              const editing = editingMessageId === message.id;
              return (
                <div
                  key={message.id}
                  className={[
                    "group flex",
                    isUser ? "justify-end" : "justify-start"
                  ].join(" ")}
                >
                  <div
                    className={[
                      "relative max-w-[82%] rounded-xl px-4 py-3 text-sm leading-6",
                      isUser ? "bg-slate-100 text-ink" : "",
                      message.role === "assistant" ? "bg-[#eef2f5] text-ink" : "",
                      isSystem ? "border border-line bg-white text-quiet" : ""
                    ].join(" ")}
                  >
                    {editing ? (
                      <div className="w-[min(680px,70vw)]">
                        <textarea
                          value={editingText}
                          onChange={(event) => setEditingText(event.target.value)}
                          className="min-h-24 w-full resize-y rounded-lg border border-line bg-white p-3 text-sm outline-none focus:border-ink"
                        />
                        <div className="mt-2 flex justify-end gap-2">
                          <button type="button" onClick={cancelEdit} className="rounded-md px-3 py-1 text-sm hover:bg-slate-100">
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => resendEdited(message.id)}
                            className="rounded-md bg-ink px-3 py-1 text-sm text-white"
                          >
                            重新发送
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="whitespace-pre-wrap">{message.text}</div>
                        {isUser ? (
                          <button
                            type="button"
                            onClick={() => beginEdit(message)}
                            className="absolute -bottom-8 right-0 hidden rounded-md border border-line bg-white px-2 py-1 text-xs text-slate-600 shadow-sm group-hover:block"
                          >
                            编辑
                          </button>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="border-t border-line p-4">
          <div className="mx-auto flex max-w-4xl items-end gap-3">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendMessage();
                }
              }}
              className="max-h-32 min-h-12 flex-1 resize-none rounded-xl border border-line bg-white px-4 py-3 text-sm outline-none focus:border-ink"
              placeholder="输入内容。后台任务运行中也可以继续补充要求..."
            />
            <button
              type="button"
              onClick={() => sendMessage()}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-ink text-lg text-white transition hover:bg-slate-800"
              aria-label="发送"
            >
              →
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
