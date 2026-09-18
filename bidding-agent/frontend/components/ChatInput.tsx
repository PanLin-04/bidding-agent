"use client";

import { useEffect, useRef, useState } from "react";
import { CornerDownLeft, SendHorizontal, Square } from "lucide-react";

type Props = {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
};

export default function ChatInput({ onSend, onStop, streaming, disabled }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // 高度自适应：输入多行时向上生长，超过上限后内部滚动
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [value]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || disabled) return;
    onSend(text);
    setValue("");
  };

  return (
    <div className="border-t border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-2 focus-within:border-blue-400 dark:border-slate-700 dark:bg-slate-950">
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter 发送、Shift+Enter 换行；中文输入法组合期间的 Enter 不能当发送，
            // 否则选词会误触发
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? "知识库未就绪..." : "输入招投标相关问题，Enter 发送 / Shift+Enter 换行"}
          className="max-h-[180px] flex-1 resize-none bg-transparent py-1.5 text-[15px] leading-6 outline-none placeholder:text-slate-400 disabled:cursor-not-allowed"
        />

        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            title="停止生成"
            className="mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-slate-200 text-slate-600 transition hover:bg-slate-300 dark:bg-slate-700 dark:text-slate-200"
          >
            <Square size={15} />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!value.trim() || disabled}
            title="发送"
            className="mb-0.5 flex h-9 w-9 items-center justify-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <SendHorizontal size={16} />
          </button>
        )}
      </div>
      <p className="mx-auto mt-1.5 flex max-w-3xl items-center gap-1 text-xs text-slate-400">
        <CornerDownLeft size={11} />
        回答基于知识库检索结果生成，仅供参考；重要事项请以官方文件为准
      </p>
    </div>
  );
}
