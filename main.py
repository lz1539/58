from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
TARGET_URL = "https://www.58.com/"
VERSION_ENDPOINT = f"http://{CDP_HOST}:{CDP_PORT}/json/version"
PROFILE_DIR_NAME = "edge_profile"


def find_edge_path() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到系统 Edge，请确认 Microsoft Edge 已安装。")


def get_app_profile_dir() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
    return base_dir / PROFILE_DIR_NAME


def read_cdp_version() -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(VERSION_ENDPOINT, timeout=1.5) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, json.JSONDecodeError):
        return None


def wait_for_cdp_ready(timeout_seconds: float = 15) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        version_data = read_cdp_version()
        if version_data and version_data.get("webSocketDebuggerUrl"):
            return
        time.sleep(0.5)
    raise TimeoutError(
        "未等到 Edge 的 CDP 端口就绪。请先关闭所有 Edge 窗口后重试。"
    )


def launch_edge(edge_path: Path, user_data_dir: Path) -> subprocess.Popen[str] | None:
    if read_cdp_version():
        return None

    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(edge_path),
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    return subprocess.Popen(args)


def open_58_with_cdp() -> None:
    edge_path = find_edge_path()
    user_data_dir = get_app_profile_dir()

    edge_process = launch_edge(edge_path, user_data_dir)
    wait_for_cdp_ready()

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://{CDP_HOST}:{CDP_PORT}")
        except Exception as exc:
            raise RuntimeError("CDP 连接失败，请先关闭所有 Edge 窗口后重试。") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        page.bring_to_front()
        print(f"已通过 CDP 接管 Edge，并打开：{TARGET_URL}")
        print(f"Edge 路径：{edge_path}")
        print(f"资料目录：{user_data_dir}")
        input("按回车退出程序，浏览器会保持打开...")
        browser.close()

    if edge_process and edge_process.poll() is None:
        print("程序退出后不会主动关闭 Edge。")


def main() -> int:
    try:
        open_58_with_cdp()
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        input("按回车退出...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
