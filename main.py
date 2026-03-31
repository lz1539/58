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
from html import unescape
from pathlib import Path

from playwright.sync_api import sync_playwright


CDP_HOST = "127.0.0.1"
TARGET_URL = "https://employer.58.com/main/jobmanage"
PROFILE_DIR_NAME = "edge_profile"
LOGIN_URL_KEYWORDS = ("login", "passport", "signin")
ONLINE_CHAT_TEXT = "在线沟通"


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


def is_login_page(url: str) -> bool:
    normalized = url.lower()
    return any(keyword in normalized for keyword in LOGIN_URL_KEYWORDS)


def wait_for_login(page, timeout_seconds: float = 600) -> None:
    if not is_login_page(page.url):
        return

    print(f"当前位于登录页：{page.url}")
    print("请在 Edge 窗口中完成登录，程序会在登录成功后继续。")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=1000)
        except Exception:
            pass
        if not is_login_page(page.url):
            print(f"检测到已离开登录页：{page.url}")
            return
        time.sleep(1)
    raise TimeoutError("等待用户登录超时，请重新运行程序后再试。")


def wait_for_candidate_list(page, timeout_seconds: float = 20) -> None:
    deadline = time.time() + timeout_seconds
    selectors = [".list-name-icon", ".list-sex-icon img", "span"]
    while time.time() < deadline:
        for frame in page.frames:
            for selector in selectors:
                try:
                    locator = frame.locator(selector)
                    if locator.count() == 0:
                        continue
                    if selector != "span":
                        return
                    texts = locator.all_inner_texts()
                    if any("先生" in text or "女士" in text for text in texts):
                        return
                except Exception:
                    continue
        time.sleep(1)


def close_known_dialogs(page) -> None:
    close_button = page.locator(".coupon-dialog .el-dialog__headerbtn")
    if close_button.count() and close_button.first.is_visible():
        close_button.first.click()
        page.wait_for_timeout(300)
    for selector in [
        ".el-dialog__headerbtn",
        ".el-message-box__close",
        ".el-drawer__close-btn",
        ".chat-dialog .close",
    ]:
        locator = page.locator(selector)
        if locator.count() and locator.first.is_visible():
            try:
                locator.first.click()
                page.wait_for_timeout(300)
            except Exception:
                pass


def get_font_key(page) -> str:
    script = r"""
() => {
  const entries = performance.getEntriesByType('resource').map(x => x.name);
  for (const entry of entries) {
    const match = entry.match(/font\.58\.com\/font\/([0-9a-f]{32})\.css/i);
    if (match) return match[1];
  }
  const links = Array.from(document.querySelectorAll('link[href], style'));
  for (const link of links) {
    const value = link.href || link.textContent || '';
    const match = value.match(/font\.58\.com\/font\/([0-9a-f]{32})\.css/i);
    if (match) return match[1];
  }
  return '';
}
"""
    font_key = page.evaluate(script)
    if not font_key:
        raise RuntimeError("未找到页面字体 font key，无法解码年龄。")
    return str(font_key)


def fetch_candidate_items(page, font_key: str) -> list[dict[str, object]]:
    api_url = (
        "https://zpim.58.com/resumepaychat/paychatlist"
        f"?jslState=0&infoId=0&imSource=-1&page=1&pageSize=10&chatState=0"
        f"&fontkey={font_key}"
        "&from=pc,hx_manage_interestedlist,other"
        "&slotId=pc_hx_manage_interestedlist_list&businessType=0&hxProfession=1&deliveryState=0"
    )
    script = """
    async (url) => {
      const resp = await fetch(url, { credentials: 'include' });
      return await resp.json();
    }
    """
    payload = page.evaluate(script, api_url)
    items = payload.get("data", {}).get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("人才列表接口返回异常，未获取到 items。")
    return items


def build_age_glyph_map(page, age_values: list[str]) -> dict[str, str]:
    glyphs: list[str] = []
    for age_value in age_values:
        decoded = unescape(age_value)
        for ch in decoded:
            if ch.isdigit() or ch == "岁" or ch.isspace():
                continue
            if ch not in glyphs:
                glyphs.append(ch)

    if not glyphs:
        return {}

    script = r"""
(glyphs) => {
  const ageElement = document.querySelector('.list-info-age');
  const fontFamily = ageElement ? getComputedStyle(ageElement).fontFamily : '';
  if (!fontFamily) {
    return {};
  }

  function render(ch) {
    const canvas = document.createElement('canvas');
    canvas.width = 80;
    canvas.height = 80;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#000';
    ctx.font = `48px ${fontFamily}`;
    ctx.textBaseline = 'top';
    ctx.fillText(ch, 10, 10);
    return ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  }

  function similarity(a, b) {
    let score = 0;
    for (let i = 0; i < a.length; i += 4) {
      if (
        a[i] === b[i] &&
        a[i + 1] === b[i + 1] &&
        a[i + 2] === b[i + 2] &&
        a[i + 3] === b[i + 3]
      ) {
        score += 1;
      }
    }
    return score;
  }

  const digitImages = {};
  for (const digit of '0123456789') {
    digitImages[digit] = render(digit);
  }

  const mapping = {};
  for (const glyph of glyphs) {
    const image = render(glyph);
    const candidates = Object.entries(digitImages)
      .map(([digit, digitImage]) => ({ digit, score: similarity(image, digitImage) }))
      .sort((a, b) => b.score - a.score);
    mapping[glyph] = candidates[0].digit;
  }
  return mapping;
}
"""
    mapping = page.evaluate(script, glyphs)
    return {str(k): str(v) for k, v in mapping.items()}


def decode_age(age_value: str, glyph_map: dict[str, str]) -> int | None:
    decoded = unescape(age_value).strip()
    if not decoded or decoded == "未知":
        return None

    age_digits: list[str] = []
    for ch in decoded:
        if ch.isdigit():
            age_digits.append(ch)
            continue
        if ch == "岁":
            break
        digit = glyph_map.get(ch)
        if digit is None:
            return None
        age_digits.append(digit)

    if not age_digits:
        return None
    return int("".join(age_digits))


def find_target_row(page, target_name: str, target_age: int, glyph_map: dict[str, str]):
    rows = page.locator(".interested-list")
    total = rows.count()
    for index in range(total):
        row = rows.nth(index)
        try:
            name = row.locator(".list-name-box span").first.inner_text().strip()
        except Exception:
            continue
        if name != target_name:
            continue

        try:
            age_text = row.locator(".list-info-age").first.inner_text().strip()
        except Exception:
            age_text = ""
        age = decode_age(age_text, glyph_map)
        if age != target_age:
            continue

        button = row.locator("button.list-chat-btn").first
        if button.count() == 0:
            continue
        return row, button
    return None, None


def click_matching_online_chat(page) -> None:
    close_known_dialogs(page)
    font_key = get_font_key(page)
    items = fetch_candidate_items(page, font_key)
    glyph_map = build_age_glyph_map(page, [str(item.get("age", "")) for item in items])

    matches: list[dict[str, object]] = []
    skipped_unknown_age: list[str] = []
    for item in items:
        sex = str(item.get("sex", "")).strip()
        age = decode_age(str(item.get("age", "")), glyph_map)
        if age is None:
            skipped_unknown_age.append(str(item.get("name", "")))
            continue
        if sex != "男":
            continue
        if not (18 <= age <= 55):
            continue
        if int(item.get("chatState", -1)) != 0:
            continue
        matches.append(
            {
                "name": str(item.get("name", "")),
                "age": age,
                "infoid": str(item.get("infoId", "")),
            }
        )

    clicked: list[str] = []
    skipped_not_online: list[str] = []
    for match in matches:
        close_known_dialogs(page)
        row, button = find_target_row(page, str(match["name"]), int(match["age"]), glyph_map)
        if row is None or button is None:
            skipped_not_online.append(f"{match['name']}({match['age']}) - 页面未找到")
            continue
        success = False
        last_button_text = ""
        for _ in range(3):
            button.scroll_into_view_if_needed()
            last_button_text = button.inner_text().strip()
            if last_button_text != ONLINE_CHAT_TEXT:
                break
            try:
                button.click(timeout=5000)
            except Exception:
                try:
                    button.click(force=True, timeout=5000)
                except Exception:
                    page.wait_for_timeout(500)
                    continue
            page.wait_for_timeout(1200)
            close_known_dialogs(page)
            try:
                updated_text = button.inner_text().strip()
            except Exception:
                updated_text = ""
            if updated_text != ONLINE_CHAT_TEXT:
                last_button_text = updated_text
                success = True
                break
        if success:
            clicked.append(f"{match['name']}({match['age']})")
        else:
            skipped_not_online.append(
                f"{match['name']}({match['age']}) - 最终按钮是{last_button_text or '未识别'}"
            )

    print("筛选结果：")
    print(f"字体 key：{font_key}")
    print(f"年龄解码映射：{glyph_map}")
    print(f"命中人数：{len(matches)}")
    if clicked:
        print("已点击在线沟通：")
        for item in clicked:
            print(f"- {item}")
    if skipped_not_online:
        print("未点击：")
        for item in skipped_not_online:
            print(f"- {item}")
    if skipped_unknown_age:
        print("年龄无法解码，已跳过：")
        for item in skipped_unknown_age:
            print(f"- {item}")


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
        wait_for_login(page)
        page.wait_for_load_state("domcontentloaded")
        wait_for_candidate_list(page)
        page.bring_to_front()
        click_matching_online_chat(page)
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
