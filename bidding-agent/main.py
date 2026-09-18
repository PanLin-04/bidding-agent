"""命令行入口：`ingest` 导入知识库 / `api` 启动服务 / `dev` 起后端并提示前端。

Windows 控制台默认 GBK，中文日志与路径容易乱码或直接抛 UnicodeEncodeError；
这里统一把 stdout/stderr 切到 UTF-8（见 docs/开发文档.md §10 同类处理）。
"""

from __future__ import annotations

import argparse
import sys
import threading


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def _check_config() -> None:
    """缺失必需配置时直接退出并打印清单——与其半死不活，不如启动即说清楚。"""
    from src.config import settings

    missing = settings.missing_required
    if missing:
        print("缺少必需配置: " + ", ".join(missing))
        print("请在项目根目录的 .env 中补全后重试（可参考 .env.example）。")
        sys.exit(1)
    if not settings.llm_available:
        print("提示: 未配置 LLM 凭据，将使用「直接返回知识库原文」的降级模式。")


def cmd_ingest(args: argparse.Namespace) -> int:
    from src.logging_config import setup_logging
    from src.rag.ingest import ingest_data

    setup_logging()
    _check_config()
    summary = ingest_data(force=True, path=args.source)
    print(
        f"导入完成: {summary['imported']} 条 → 集合 {summary['collection']}\n"
        f"数据源: {summary['source']}\n"
        f"词表: {summary['vocab_file']}"
    )
    return 0


def _uvicorn_config(reload: bool):
    import uvicorn

    from src.config import settings

    return uvicorn.Config(
        "api.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
        log_level="info",
    )


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    _check_config()
    uvicorn.run(_uvicorn_config(reload=True), server_header=False)
    return 0


def cmd_dev(args: argparse.Namespace) -> int:
    """后台线程起后端，前台打印前端启动提示，省去两个终端来回切。"""
    import uvicorn

    _check_config()
    from src.config import settings

    server = uvicorn.Server(_uvicorn_config(reload=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    print(f"后端已启动: http://localhost:{settings.api_port}（接口文档 /docs）")
    print("前端请另开终端执行: cd frontend && npm install && npm run dev")
    print("按 Ctrl+C 退出。")
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="招投标采购智能问答")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="将 Excel 问答数据导入 Qdrant（全量重建）")
    p_ingest.add_argument("--source", default=None, help="指定数据文件；默认自动查找 data/ 下的 xlsx")
    p_ingest.set_defaults(func=cmd_ingest)

    sub.add_parser("api", help="启动后端服务（开发模式，带热重载）").set_defaults(func=cmd_api)
    sub.add_parser("dev", help="后台启动后端并打印前端启动提示").set_defaults(func=cmd_dev)
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
