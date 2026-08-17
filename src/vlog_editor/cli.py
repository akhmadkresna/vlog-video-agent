from __future__ import annotations

import argparse
import sys

from vlog_editor import __version__
from vlog_editor.analysis import analyze_episode
from vlog_editor.benchmark import run_benchmark
from vlog_editor.dashboard import approve_plan, generate_dashboard
from vlog_editor.doctor import print_report
from vlog_editor.planner import create_balanced_plan, create_plan
from vlog_editor.project import DEFAULT_CONFIG, create_episode, resolve_episode
from vlog_editor.render import render_episode
from vlog_editor.youtube import generate_listing, upload_episode
from vlog_editor.youtube import login as youtube_login


def _episode_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("episode", nargs="?", default=".", help="Episode folder (default: .)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ve", description="Local Vlog Editor Agent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local runtime and model")
    doctor.add_argument("episode", nargs="?", help="Optional episode for its configuration")

    new = subparsers.add_parser("new", help="Create an episode folder")
    new.add_argument("path")
    new.add_argument("--force", action="store_true")

    analyze = subparsers.add_parser("analyze", help="Analyze footage locally")
    _episode_argument(analyze)
    analyze.add_argument("--force", action="store_true", help="Ignore analysis caches")

    plan = subparsers.add_parser("plan", help="Generate and validate a local edit plan")
    _episode_argument(plan)
    plan.add_argument(
        "--balanced",
        action="store_true",
        help="Use deterministic balanced selection without another LLM planning request",
    )

    preview = subparsers.add_parser("preview", help="Generate the local review dashboard")
    _episode_argument(preview)
    preview.add_argument("--no-open", action="store_true")

    approve = subparsers.add_parser("approve", help="Approve the exact current edit plan")
    _episode_argument(approve)

    render = subparsers.add_parser("render", help="Render an approved edit plan")
    _episode_argument(render)

    benchmark = subparsers.add_parser("benchmark", help="Benchmark three local vision frames")
    _episode_argument(benchmark)

    run = subparsers.add_parser("run", help="Analyze, plan and stop for human review")
    _episode_argument(run)
    run.add_argument("--force", action="store_true", help="Ignore analysis caches")
    run.add_argument("--no-open", action="store_true")

    youtube_login_cmd = subparsers.add_parser(
        "youtube-login",
        help="Authorize this machine to upload to YouTube (browser once)",
    )
    youtube_login_cmd.set_defaults(command="youtube-login")

    meta = subparsers.add_parser(
        "youtube-meta",
        help="Write work/youtube_listing.json (title, description, tags, made-for-kids)",
    )
    _episode_argument(meta)
    meta.add_argument("--privacy", choices=["private", "unlisted", "public"])
    kids = meta.add_mutually_exclusive_group()
    kids.add_argument("--made-for-kids", dest="made_for_kids", action="store_true")
    kids.add_argument("--not-made-for-kids", dest="made_for_kids", action="store_false")
    meta.set_defaults(made_for_kids=None)

    upload = subparsers.add_parser(
        "upload",
        help="Upload rendered final.mp4 plus captions.srt to YouTube",
    )
    _episode_argument(upload)
    upload.add_argument("--dry-run", action="store_true", help="Write listing/request JSON only")
    upload.add_argument("--privacy", choices=["private", "unlisted", "public"])
    upload_kids = upload.add_mutually_exclusive_group()
    upload_kids.add_argument("--made-for-kids", dest="made_for_kids", action="store_true")
    upload_kids.add_argument("--not-made-for-kids", dest="made_for_kids", action="store_false")
    upload.set_defaults(made_for_kids=None)
    upload.add_argument("--skip-captions", action="store_true")
    upload.add_argument(
        "--force",
        action="store_true",
        help="Upload a new YouTube copy even if output/youtube_upload.json exists",
    )

    return parser


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        config = resolve_episode(args.episode).config if args.episode else DEFAULT_CONFIG
        return print_report(config)
    if args.command == "youtube-login":
        path = youtube_login()
        print(f"YouTube login saved to {path}")
        return 0
    if args.command == "new":
        episode = create_episode(args.path, force=args.force)
        print(f"Created {episode.root}")
        print(f"Add videos to {episode.footage}, then run: ve run {episode.root}")
        return 0

    episode = resolve_episode(args.episode)
    if args.command == "analyze":
        analyze_episode(episode, force=args.force)
    elif args.command == "plan":
        if args.balanced:
            create_balanced_plan(episode)
        else:
            create_plan(episode)
    elif args.command == "preview":
        generate_dashboard(episode, open_browser=not args.no_open)
    elif args.command == "approve":
        approval = approve_plan(episode)
        print(f"Approved plan {approval['plan_sha256'][:12]}")
    elif args.command == "render":
        render_episode(episode)
    elif args.command == "benchmark":
        result = run_benchmark(episode)
        print(
            f"{result['model']}: {result['warm_wall_sec']:.2f}s warm latency, "
            f"{result['tokens_per_sec'] or 'n/a'} tok/s"
        )
    elif args.command == "run":
        analyze_episode(episode, force=args.force)
        create_plan(episode)
        generate_dashboard(episode, open_browser=not args.no_open)
        print("\nStopped for review. When satisfied:")
        print(f"  ve approve {episode.root}")
        print(f"  ve render {episode.root}")
    elif args.command == "youtube-meta":
        listing = generate_listing(
            episode,
            privacy=args.privacy,
            made_for_kids=args.made_for_kids,
        )
        print(f"Wrote {episode.work / 'youtube_listing.json'}")
        print(f"Title: {listing['title']}")
        print(f"Privacy: {listing['privacy']}")
        print(f"Made for kids: {listing['made_for_kids']}")
        print(f"Tags: {', '.join(listing['tags'][:12])}")
    elif args.command == "upload":
        result = upload_episode(
            episode,
            privacy=args.privacy,
            made_for_kids=args.made_for_kids,
            skip_captions=args.skip_captions,
            force=args.force,
            dry_run=args.dry_run,
        )
        if result.get("dry_run"):
            print(f"Dry run: {episode.work / 'youtube_dry_run.json'}")
            print(f"Title: {result['listing']['title']}")
            print(f"Privacy: {result['listing']['privacy']}")
            print(f"Made for kids: {result['listing']['made_for_kids']}")
        else:
            print(f"Uploaded {result['url']}")
            print(f"Studio: {result['studio_url']}")
            print(f"Made for kids: {result['made_for_kids']}  Privacy: {result['privacy']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary must render actionable errors
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
