"use client";

import { Landmark, Moon, Plus, Sun, Trash2 } from "lucide-react";

export type Session = {
  id: string;
  title: string;
  updatedAt: number;
};

type Props = {
  sessions: Session[];
  currentId: string;
  dark: boolean;
  health: { ready?: boolean; points_count?: number; llm_available?: boolean } | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onToggleDark: () => void;
};

export default function Sidebar({
  sessions,
  currentId,
  dark,
  health,
  onSelect,
  onNew,
  onDelete,
  onToggleDark,
}: Props) {
  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-center gap-2 px-4 py-3.5">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white">
          <Landmark size={15} />
        </span>
        <span className="text-sm font-semibold">招投标问答</span>
      </div>

      <div className="px-3">
        <button
          type="button"
          onClick={onNew}
          className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-slate-200 py-2 text-sm text-slate-600 transition hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300"
        >
          <Plus size={15} />
          新建会话
        </button>
      </div>

      <nav className="mt-3 flex-1 overflow-y-auto px-2">
        {sessions.map((session) => (
          <div
            key={session.id}
            className={`group flex items-center gap-1 rounded-lg px-2.5 py-2 text-sm transition ${
              session.id === currentId
                ? "bg-blue-50 text-blue-700 dark:bg-slate-800 dark:text-blue-300"
                : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800/60"
            }`}
          >
            <button
              type="button"
              onClick={() => onSelect(session.id)}
              className="flex-1 truncate text-left"
              title={session.title}
            >
              {session.title || "新会话"}
            </button>
            <button
              type="button"
              onClick={() => onDelete(session.id)}
              title="删除会话"
              className="opacity-0 transition group-hover:opacity-100 hover:text-red-500"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </nav>

      <div className="border-t border-slate-200 px-4 py-3 text-xs text-slate-400 dark:border-slate-800">
        <div className="flex items-center justify-between">
          <span className="inline-flex items-center gap-1.5">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                health?.ready ? "bg-emerald-500" : "bg-slate-300 dark:bg-slate-600"
              }`}
            />
            {health?.ready ? `知识库 ${health.points_count ?? 0} 条` : "知识库未就绪"}
          </span>
          <button
            type="button"
            onClick={onToggleDark}
            title={dark ? "切换到浅色" : "切换到深色"}
            className="hover:text-slate-600 dark:hover:text-slate-200"
          >
            {dark ? <Sun size={14} /> : <Moon size={14} />}
          </button>
        </div>
        {health && health.llm_available === false && (
          <p className="mt-1 text-amber-500">未配置大模型：直接返回知识库原文</p>
        )}
      </div>
    </aside>
  );
}
