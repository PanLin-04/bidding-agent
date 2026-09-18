"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, FileText } from "lucide-react";
import type { Source } from "@/lib/api";

/**
 * 参考来源卡片。
 *
 * 默认折叠答案正文——来源列表常常一次出现好几条，全展开会把助手的回答顶到
 * 屏幕外；用户关心的是"依据哪几问"，需要细节时再展开。
 */
export default function SourceCard({ source, index }: { source: Source; index: number }) {
  const [open, setOpen] = useState(false);
  const isWeb = Boolean(source.url);

  return (
    <div className="rounded-lg border border-slate-200 bg-white text-sm dark:border-slate-700 dark:bg-slate-900">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-start gap-2 p-2.5 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60"
      >
        <span className="mt-0.5 text-slate-400">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        <span className="mt-0.5 shrink-0 text-slate-400">
          {isWeb ? <ExternalLink size={14} /> : <FileText size={14} />}
        </span>
        <span className="flex-1 font-medium text-slate-700 dark:text-slate-200">
          <span className="mr-1 text-slate-400">[{index}]</span>
          {source.question || source.answer.slice(0, 40)}
        </span>
        {typeof source.score === "number" && source.score > 0 && (
          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
            {source.score.toFixed(2)}
          </span>
        )}
      </button>

      {open && (
        <div className="animate-fade-in border-t border-slate-100 p-2.5 text-slate-600 dark:border-slate-800 dark:text-slate-300">
          <p className="whitespace-pre-wrap">{source.answer}</p>
          {source.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-block text-blue-600 hover:underline dark:text-blue-400"
            >
              查看原文
            </a>
          )}
        </div>
      )}
    </div>
  );
}
