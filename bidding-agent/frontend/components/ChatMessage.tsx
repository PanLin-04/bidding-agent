"use client";

import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, Check, Copy, Database, RefreshCw } from "lucide-react";
import SourceCard from "@/components/SourceCard";
import type { Source } from "@/lib/api";

export type UIMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  toolCalled?: boolean;
  toolName?: string;
  /** 流式过程中的阶段提示（status 帧），用于占位显示 */
  status?: string;
  streaming?: boolean;
  error?: boolean;
  elapsedMs?: number;
};

type Props = {
  message: UIMessage;
  onRegenerate?: (messageId: string) => void;
};

/**
 * 单条消息。
 *
 * `React.memo` 是必需的：流式输出时父组件每个 token 都会重渲染，若不拦截，
 * 历史消息（可能带长 Markdown 表格）会跟着一起重排，长对话下肉眼可见地卡。
 * 与之配套，父组件传来的回调必须引用稳定（见 page.tsx 的 useCallback）。
 */
function ChatMessage({ message, onRegenerate }: Props) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 剪贴板不可用（非 https / 无权限）时静默失败 */
    }
  };

  if (isUser) {
    return (
      <div className="flex animate-fade-in justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2.5 text-[15px] leading-7 text-white">
          {message.content}
        </div>
      </div>
    );
  }

  // 尚无正文时显示阶段提示（status 帧）或加载动画，避免出现空气泡
  const showPlaceholder = message.streaming && !message.content;

  return (
    <div className="flex animate-fade-in flex-col gap-2">
      <div className="max-w-[92%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
        {showPlaceholder ? (
          <div className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-blue-500" />
            {message.status || "正在思考..."}
          </div>
        ) : (
          <div className="markdown-body">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // 外链一律新窗口打开，并断开 referrer / opener 关系
                a: ({ node, ...props }) => (
                  <a {...props} target="_blank" rel="noopener noreferrer" />
                ),
              }}
            >
              {message.content}
            </ReactMarkdown>
            {/* 流式光标：让"还在输出"这件事在视觉上连续 */}
            {message.streaming && (
              <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-blue-500 align-text-bottom" />
            )}
          </div>
        )}
      </div>

      {message.error && (
        <div className="flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400">
          <AlertTriangle size={14} />
          回答可能不完整，请重试
        </div>
      )}

      {!!message.sources?.length && (
        <div className="flex flex-col gap-1.5">
          <div className="text-xs text-slate-500 dark:text-slate-400">
            参考来源（{message.sources.length}）
          </div>
          {message.sources.map((source, index) => (
            <SourceCard key={`${message.id}-src-${index}`} source={source} index={index + 1} />
          ))}
        </div>
      )}

      {!message.streaming && message.content && (
        <div className="flex items-center gap-3 text-xs text-slate-400">
          {message.toolCalled && (
            <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 dark:bg-slate-800">
              <Database size={12} />
              已检索知识库
            </span>
          )}
          {typeof message.elapsedMs === "number" && <span>{(message.elapsedMs / 1000).toFixed(1)}s</span>}
          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center gap-1 hover:text-slate-600 dark:hover:text-slate-300"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "已复制" : "复制"}
          </button>
          {onRegenerate && (
            <button
              type="button"
              onClick={() => onRegenerate(message.id)}
              className="inline-flex items-center gap-1 hover:text-slate-600 dark:hover:text-slate-300"
            >
              <RefreshCw size={12} />
              重新生成
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default memo(ChatMessage);
