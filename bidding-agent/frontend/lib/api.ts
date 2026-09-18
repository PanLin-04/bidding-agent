/**
 * API 封装：REST + SSE 流式读取 + 运行时校验。
 *
 * 两处刻意的设计：
 * 1. **流式请求绕过 Next.js rewrites 代理直连后端**——Next 的代理会缓冲整个
 *    响应再下发，SSE 会退化成"整段一次显示"（见开发文档 §3.2）；
 * 2. **localStorage 读出的历史记录做运行时校验**，脏记录丢弃而不是让页面崩溃。
 */

export type Source = {
  question: string;
  answer: string;
  score: number;
  url?: string;
};

/** 落库形状（开发文档 §7.3）：思考过程与计时**不落库**，仅当前会话展示。 */
export type PersistedMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  toolCalled?: boolean;
  toolName?: string;
};

export type PhaseTime = [string, number];

export type StreamEvent =
  | { type: "status"; content: string }
  | { type: "token"; content: string }
  | { type: "reset" }
  | {
      type: "done";
      sources: Source[];
      web_sources: Source[];
      tool_called: boolean;
      tool_name: string;
      elapsed_ms: number;
      phase_times: PhaseTime[];
    }
  | { type: "error"; content: string };

/** 流式直连地址；未显式配置时回退本机后端。 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8001";

export async function streamChat(
  question: string,
  history: { role: string; content: string }[],
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
    signal,
  });

  if (!response.ok) {
    // 后端错误体是 {detail}；解析失败时退回状态码，避免把 HTML 错误页当消息显示
    let detail = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* 忽略解析失败 */
    }
    onEvent({ type: "error", content: detail });
    return;
  }
  if (!response.body) {
    onEvent({ type: "error", content: "浏览器不支持流式响应。" });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 以空行分隔帧；最后一段可能不完整，留在 buffer 里等下一次读取
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data: ")) continue; // 跳过 `:` 开头的首帧填充注释
        const raw = line.slice(6).trim();
        if (!raw || raw === "[DONE]") continue;
        try {
          onEvent(JSON.parse(raw) as StreamEvent);
        } catch {
          // 单行 JSON 解析失败不应中断整个流
        }
      }
    }
  }
}

export async function fetchHealth(): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(`${API_BASE}/api/health`);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

/** 运行时校验：只放行形状正确的记录，脏数据丢弃而非让渲染崩溃。 */
export function parsePersistedMessage(value: unknown): PersistedMessage | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  if (record.role !== "user" && record.role !== "assistant") return null;
  if (typeof record.content !== "string") return null;

  const message: PersistedMessage = { role: record.role, content: record.content };
  if (Array.isArray(record.sources)) {
    message.sources = record.sources
      .filter((s): s is Record<string, unknown> => !!s && typeof s === "object")
      .filter((s) => typeof s.question === "string" || typeof s.answer === "string")
      .map((s) => ({
        question: typeof s.question === "string" ? s.question : "",
        answer: typeof s.answer === "string" ? s.answer : "",
        score: typeof s.score === "number" ? s.score : 0,
        url: typeof s.url === "string" ? s.url : undefined,
      }));
  }
  if (typeof record.toolCalled === "boolean") message.toolCalled = record.toolCalled;
  if (typeof record.toolName === "string") message.toolName = record.toolName;
  return message;
}
