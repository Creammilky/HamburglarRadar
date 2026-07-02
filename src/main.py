"""顶层入口。

用法：
    python -m src.main digest --dry-run
    python -m src.main digest --send
    python -m src.main serve [--host 0.0.0.0 --port 9000]   # 飞书群聊交互服务（Milestone 3）
    python -m src.main ask "总结 https://arxiv.org/abs/xxxx.xxxxx"  # 本地模拟一条群命令
"""

from __future__ import annotations

import argparse
import sys

from src.scheduler.daily_digest_job import main as digest_main


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    parser = argparse.ArgumentParser(prog="arxiv-feishu-research-agent")
    sub = parser.add_subparsers(dest="command")

    p_digest = sub.add_parser("digest", help="运行 arXiv 晨报作业")
    p_digest.add_argument("--dry-run", action="store_true")
    p_digest.add_argument("--send", action="store_true")
    p_digest.add_argument("--profile", default=None)

    p_serve = sub.add_parser("serve", help="启动飞书群聊交互服务（Milestone 3）")
    p_serve.add_argument(
        "--mode", choices=["ws", "http"], default="ws",
        help="ws=自建应用长连接（本机可用，推荐）；http=事件回调（需公网 URL）",
    )
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=9000)

    sub.add_parser("ask", help="本地模拟一条群 @ 命令")

    args, rest = parser.parse_known_args(argv)

    if args.command == "digest":
        forwarded = rest[:]
        if args.dry_run:
            forwarded.append("--dry-run")
        if args.send:
            forwarded.append("--send")
        if args.profile:
            forwarded += ["--profile", args.profile]
        return digest_main(forwarded)

    if args.command == "serve":
        if args.mode == "ws":
            from src.agent.feishu_ws import run_ws

            run_ws()
        else:
            from src.agent.serve import run_server

            run_server(host=args.host, port=args.port)
        return 0

    if args.command == "ask":
        from src.agent.cli import main as ask_main

        return ask_main(rest)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
