from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


CDP_HOST = "127.0.0.1"
TARGET_URL = "https://www.58.com/"
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


def build_version_endpoint(cdp_port: int) -> str:
    return f"http://{CDP_HOST}:{cdp_port}/json/version"


def get_devtools_active_port_file(user_data_dir: Path) -> Path:
    return user_data_dir / "DevToolsActivePort"


def choose_cdp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((CDP_HOST, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def read_cdp_version(cdp_port: int) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(build_version_endpoint(cdp_port), timeout=1.5) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, json.JSONDecodeError):
        return None


def read_existing_cdp_port(user_data_dir: Path) -> int | None:
    active_port_file = get_devtools_active_port_file(user_data_dir)
    if not active_port_file.is_file():
        return None
    try:
        lines = active_port_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    try:
        cdp_port = int(lines[0].strip())
    except ValueError:
        return None
    version_data = read_cdp_version(cdp_port)
    if version_data and version_data.get("webSocketDebuggerUrl"):
        return cdp_port
    return None


def read_running_edge_cdp_port(user_data_dir: Path) -> int | None:
    if os.name != "nt":
        return None

    profile_name = user_data_dir.name.lower()
    script = rf"""
Get-CimInstance Win32_Process |
  Where-Object {{ $_.Name -eq 'msedge.exe' -and $_.CommandLine }} |
  Select-Object -ExpandProperty CommandLine
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    for command_line in result.stdout.splitlines():
        normalized = command_line.lower()
        if profile_name not in normalized:
            continue
        match = re.search(r"--remote-debugging-port=(\d+)", command_line)
        if not match:
            continue
        cdp_port = int(match.group(1))
        version_data = read_cdp_version(cdp_port)
        if version_data and version_data.get("webSocketDebuggerUrl"):
            return cdp_port
    return None


def wait_for_enter(prompt: str) -> None:
    if sys.stdin is None or not sys.stdin.isatty():
        return
    try:
        input(prompt)
    except EOFError:
        pass


def wait_for_cdp_ready(cdp_port: int, edge_process: subprocess.Popen[str], timeout_seconds: float = 15) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        version_data = read_cdp_version(cdp_port)
        if version_data and version_data.get("webSocketDebuggerUrl"):
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"未等待到 Edge 的 CDP 端口就绪（{CDP_HOST}:{cdp_port}）。"
        f" 启动器进程退出码：{edge_process.poll()}。"
        " 请检查是否有安全软件阻止调试端口，或 Edge 未成功拉起浏览器进程。"
    )


def launch_edge(edge_path: Path, user_data_dir: Path, cdp_port: int) -> subprocess.Popen[str]:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(edge_path),
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank",
    ]
    return subprocess.Popen(args)


def open_58_with_cdp() -> None:
    edge_path = find_edge_path()
    user_data_dir = get_app_profile_dir()
    cdp_port = read_existing_cdp_port(user_data_dir) or read_running_edge_cdp_port(user_data_dir)

    if cdp_port is None:
        cdp_port = choose_cdp_port()
        edge_process = launch_edge(edge_path, user_data_dir, cdp_port)
        wait_for_cdp_ready(cdp_port, edge_process)
    else:
        edge_process = None

    version_data = read_cdp_version(cdp_port)
    websocket_url = str(version_data.get("webSocketDebuggerUrl", "")) if version_data else ""
    if not websocket_url:
        raise RuntimeError(f"未获取到 Edge 的 webSocketDebuggerUrl（{CDP_HOST}:{cdp_port}）。")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(websocket_url)
        except Exception as exc:
            raise RuntimeError(f"CDP 连接失败（{CDP_HOST}:{cdp_port}）：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        page.bring_to_front()
        print(f"已通过 CDP 接管 Edge，并打开：{TARGET_URL}")
        print(f"Edge 路径：{edge_path}")
        print(f"资料目录：{user_data_dir}")
        print(f"调试地址：http://{CDP_HOST}:{cdp_port}")
        wait_for_enter("按回车退出程序，浏览器会保持打开...")
        browser.close()

    if edge_process and edge_process.poll() is None:
        print("程序退出后不会主动关闭 Edge。")


def main() -> int:
    try:
        open_58_with_cdp()
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        wait_for_enter("按回车退出...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
