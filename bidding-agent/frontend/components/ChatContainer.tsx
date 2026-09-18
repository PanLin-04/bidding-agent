"use client";

import { useEffect, useRef } from "react";
import { Landmark } from "lucide-react";
import ChatMessage, { type UIMessage } from "@/components/ChatMessage";

type Props = {
  messages: UIMessage[];
  onRegenerate: (messageId: string) => void;
  onPickExample: (question: string) => void;
  examples: string[];
};

export default function ChatContainer({ messages, onRegenerate, onPickExample, examples }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // 新内容到达时贴底。用 scrollTop 直接赋值而不是 scrollIntoView：
  // 后者在某些浏览器会把整个页面（含侧边栏）一起滚动。
  useEffect(() => {
    const container = containerRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages]);

  if (!messages.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-6 px-6 text-center">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 text-white">
          <Landmark size={26} />
        </div>
        <div>
          <h2 className="text-xl font-semibold">招投标采购智能问答</h2>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
            基于知识库混合检索（向量 + BM25），回答均附参考来源
          </p>
        </div>
        <div className="flex w-full max-w-xl flex-col gap-2">
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => onPickExample(example)}
              className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-left text-sm text-slate-600 transition hover:border-blue-400 hover:text-blue-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300 dark:hover:border-blue-500"
            >
              {example}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div ref={containerRef} className="h-full overflow-y-auto px-4 py-6">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        {messages.map((message) => (
          <ChatMessage
            key={message.id}
            message={message}
            onRegenerate={message.role === "assistant" ? onRegenerate : undefined}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
