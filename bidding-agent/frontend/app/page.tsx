"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatContainer from "@/components/ChatContainer";
import ChatInput from "@/components/ChatInput";
import Sidebar from "@/components/Sidebar";
import type { UIMessage } from "@/components/ChatMessage";
import { fetchHealth, parsePersistedMessage, streamChat, type PersistedMessage } from "@/lib/api";

const SESSIONS_KEY = "chat_sessions";
const CURRENT_KEY = "chat_session_id";
const DARK_KEY = "chat_dark_mode";
// 送给后端的历史轮数上限，与后端 MAX_HISTORY_ROUNDS 对齐
const MAX_HISTORY_ROUNDS = 5;

const EXAMPLES = [
  "单一来源采购方式公示期间有异议应当如何处理？",
  "供应商针对单一来源异议被驳回，可以再次异议吗？",
  "哪些情形下可以采用单一来源方式采购？",
];

type StoredMessage = UIMessage;
type StoredSession = {
  id: string;
  title: string;
  updatedAt: number;
  messages: StoredMessage[];
};

const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

/** 落库形状（开发文档 §7.3）：思考/计时/占位等瞬时状态一律不写入 localStorage。 */
function toPersisted(message: StoredMessage) {
  return {
    id: message.id,
    role: message.role,
    content: message.content,
    sources: message.sources,
    toolCalled: message.toolCalled,
    toolName: message.toolName,
  };
}

function loadSessions(): StoredSession[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // 逐条校验：脏记录丢弃即可，不能让一条坏数据把整个页面打崩
    return parsed.flatMap((item): StoredSession[] => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (typeof record.id !== "string") return [];
      const messages = Array.isArray(record.messages)
        ? record.messages
            .map(parsePersistedMessage)
            .filter((m): m is PersistedMessage => m !== null)
            .map((m) => ({ ...m, id: uid() }))
        : [];
      return [
        {
          id: record.id,
          title: typeof record.title === "string" ? record.title : "",
          updatedAt: typeof record.updatedAt === "number" ? record.updatedAt : Date.now(),
          messages,
        },
      ];
    });
  } catch {
    return [];
  }
}

export default function Page() {
  const [sessions, setSessions] = useState<StoredSession[]>([]);
  const [currentId, setCurrentId] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [dark, setDark] = useState(false);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [hydrated, setHydrated] = useState(false);

  // 流式回调发生在 React 状态闭包之外，用 ref 持有"当前值"，
  // 同时让 handleSend / handleRegenerate 保持引用稳定（配合 ChatMessage 的 memo）
  const sessionsRef = useRef<StoredSession[]>([]);
  const currentIdRef = useRef("");
  const streamingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const requestSeqRef = useRef(0);
  const sessionIdRef = useRef("");

  // ---- 初始化：读取本地会话与主题 ----
  useEffect(() => {
    const stored = loadSessions();
    const storedCurrent = localStorage.getItem(CURRENT_KEY);
    const initial = stored.length
      ? stored
      : [{ id: uid(), title: "", updatedAt: Date.now(), messages: [] }];
    const activeId = storedCurrent && initial.some((s) => s.id === storedCurrent) ? storedCurrent : initial[0].id;

    sessionsRef.current = initial;
    currentIdRef.current = activeId;
    setSessions(initial);
    setCurrentId(activeId);
    setDark(document.documentElement.classList.contains("dark"));
    setHydrated(true);

    const timer = setInterval(async () => setHealth(await fetchHealth()), 60_000);
    fetchHealth().then(setHealth);
    return () => clearInterval(timer);
  }, []);

  const commit = useCallback((next: StoredSession[], persist: boolean) => {
    sessionsRef.current = next;
    setSessions(next);
    if (!persist) return;
    try {
      localStorage.setItem(SESSIONS_KEY, JSON.stringify(next.map((s) => ({ ...s, messages: s.messages.map(toPersisted) }))));
    } catch {
      // 隐私模式/配额写满：持久化失败不能影响当前会话继续用
    }
  }, []);

  const toggleDark = useCallback(() => {
    setDark((prev) => {
      const next = !prev;
      document.documentElement.classList.toggle("dark", next);
      try {
        localStorage.setItem(DARK_KEY, next ? "1" : "0");
      } catch {
        /* 忽略写入失败 */
      }
      return next;
    });
  }, []);

  const handleNew = useCallback(() => {
    abortRef.current?.abort();
    const session: StoredSession = { id: uid(), title: "", updatedAt: Date.now(), messages: [] };
    commit([session, ...sessionsRef.current], true);
    currentIdRef.current = session.id;
    setCurrentId(session.id);
    try {
      localStorage.setItem(CURRENT_KEY, session.id);
    } catch {
      /* 忽略 */
    }
  }, [commit]);

  const handleSelect = useCallback((id: string) => {
    abortRef.current?.abort();
    currentIdRef.current = id;
    setCurrentId(id);
    try {
      localStorage.setItem(CURRENT_KEY, id);
    } catch {
      /* 忽略 */
    }
  }, []);

  const handleDelete = useCallback(
    (id: string) => {
      const rest = sessionsRef.current.filter((s) => s.id !== id);
      if (id === currentIdRef.current) {
        const fallback: StoredSession = { id: uid(), title: "", updatedAt: Date.now(), messages: [] };
        commit([fallback, ...rest], true);
        currentIdRef.current = fallback.id;
        setCurrentId(fallback.id);
      } else {
        commit(rest, true);
      }
    },
    [commit]
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setStreaming(false);
    streamingRef.current = false;
  }, []);

  // ---- 发送与流式接收 ----
  const handleSend = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || streamingRef.current) return;

      // ① AbortController：新请求发起即取消旧流，避免两路 token 交错写入同一条消息
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // ② 请求序号：流收尾前校验自己仍是最新请求，否则结果作废
      const seq = ++requestSeqRef.current;
      // ③ 会话归属：记录本次请求属于哪个会话，切走之后不得再写回去
      const sessionId = currentIdRef.current || sessionsRef.current[0]?.id;
      if (!sessionId) return;
      sessionIdRef.current = sessionId;

      const session = sessionsRef.current.find((s) => s.id === sessionId);
      const history = (session?.messages ?? [])
        .slice(-MAX_HISTORY_ROUNDS * 2)
        .map((m) => ({ role: m.role, content: m.content }));

      const assistantId = uid();
      const base = sessionsRef.current.map((s) =>
        s.id !== sessionId
          ? s
          : {
              ...s,
              title: s.title || question.slice(0, 24),
              updatedAt: Date.now(),
              messages: [
                ...s.messages,
                { id: uid(), role: "user" as const, content: question },
                { id: assistantId, role: "assistant" as const, content: "", streaming: true, status: "正在检索知识库..." },
              ],
            }
      );
      commit(base, true);
      setStreaming(true);
      streamingRef.current = true;

      const patchAssistant = (patch: (m: StoredMessage) => StoredMessage, persist = false) => {
        const next = sessionsRef.current.map((s) =>
          s.id !== sessionId
            ? s
            : { ...s, updatedAt: Date.now(), messages: s.messages.map((m) => (m.id === assistantId ? patch(m) : m)) }
        );
        commit(next, persist);
      };

      let sawEnd = false;
      try {
        await streamChat(
          question,
          history,
          (event) => {
            // 三重防护的落地位置：任何一条不满足都直接丢弃事件
            if (seq !== requestSeqRef.current) return;
            if (sessionIdRef.current !== sessionId) return;

            switch (event.type) {
              case "status":
                patchAssistant((m) => ({ ...m, status: event.content }));
                break;
              case "token":
                patchAssistant((m) => ({ ...m, content: m.content + event.content, status: undefined }));
                break;
              case "reset":
                // 后端检出工具调用文本泄漏或大模型失败：清空已流内容重来
                patchAssistant((m) => ({ ...m, content: "" }));
                break;
              case "done":
                sawEnd = true;
                patchAssistant(
                  (m) => ({
                    ...m,
                    streaming: false,
                    status: undefined,
                    sources: event.sources,
                    toolCalled: event.tool_called,
                    toolName: event.tool_name,
                    elapsedMs: event.elapsed_ms,
                  }),
                  true
                );
                break;
              case "error":
                sawEnd = true;
                patchAssistant(
                  (m) => ({ ...m, streaming: false, status: undefined, error: true, content: m.content || event.content }),
                  true
                );
                break;
            }
          },
          controller.signal
        );

        if (seq === requestSeqRef.current && !sawEnd) {
          // 流结束但没收到 done/error：后端异常截断。已有正文保留并加提示，
          // 无正文则给出明确错误，避免界面停在"正在思考"
          patchAssistant(
            (m) => ({
              ...m,
              streaming: false,
              status: undefined,
              error: true,
              content: m.content || "连接中断，请重试。",
            }),
            true
          );
        }
      } catch (error) {
        if ((error as Error)?.name === "AbortError") {
          // 用户主动停止或切换会话：保留已生成内容，只结束流式态
          patchAssistant((m) => ({ ...m, streaming: false, status: undefined }), true);
        } else if (seq === requestSeqRef.current) {
          patchAssistant(
            (m) => ({
              ...m,
              streaming: false,
              status: undefined,
              error: true,
              content: m.content || "请求失败，请稍后重试。",
            }),
            true
          );
        }
      } finally {
        if (seq === requestSeqRef.current) {
          setStreaming(false);
          streamingRef.current = false;
        }
      }
    },
    [commit]
  );

  const handleRegenerate = useCallback(
    async (messageId: string) => {
      const sessionId = currentIdRef.current;
      const session = sessionsRef.current.find((s) => s.id === sessionId);
      if (!session) return;

      const index = session.messages.findIndex((m) => m.id === messageId);
      if (index < 0) return;
      let userIndex = index - 1;
      while (userIndex >= 0 && session.messages[userIndex].role !== "user") userIndex--;
      if (userIndex < 0) return;

      // 从该提问处截断，连同它的回答一起重来
      const question = session.messages[userIndex].content;
      commit(
        sessionsRef.current.map((s) =>
          s.id !== sessionId ? s : { ...s, messages: s.messages.slice(0, userIndex), updatedAt: Date.now() }
        ),
        true
      );
      await handleSend(question);
    },
    [commit, handleSend]
  );

  const messages = sessions.find((s) => s.id === currentId)?.messages ?? [];

  if (!hydrated) {
    // 首帧不渲染内容：localStorage 只在浏览器可读，先渲染会造成水合不一致
    return <div className="flex h-screen items-center justify-center text-slate-400">加载中...</div>;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        sessions={sessions.map((s) => ({ id: s.id, title: s.title, updatedAt: s.updatedAt }))}
        currentId={currentId}
        dark={dark}
        health={health}
        onSelect={handleSelect}
        onNew={handleNew}
        onDelete={handleDelete}
        onToggleDark={toggleDark}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <ChatContainer
          messages={messages}
          onRegenerate={handleRegenerate}
          onPickExample={handleSend}
          examples={EXAMPLES}
        />
        <ChatInput onSend={handleSend} onStop={handleStop} streaming={streaming} disabled={health?.ready === false} />
      </main>
    </div>
  );
}
