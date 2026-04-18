from __future__ import annotations

import json
import math
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
EDGE_PROFILE_DIRECTORY = "Default"
LOGIN_URL_KEYWORDS = ("login", "passport", "signin")
ONLINE_CHAT_TEXT_CANDIDATES = ("在线沟通", "立即沟通", "马上沟通", "发起沟通")
CHAT_EXCLUDE_KEYWORDS = ("极速", "人才", "特权", "优先", "简历", "下载", "电话", "权益", "购买", "简历包", "推荐", "会员", "开通", "体验")
CHAT_SUCCESS_TEXT_CANDIDATES = ("继续沟通", "立即回复", "去沟通", "已沟通")
CHAT_FAILURE_KEYWORDS = ("开通", "购买", "受限", "上限", "失败", "异常", "不可", "暂停", "稍后", "请先登录")
FORBIDDEN_ATTRS = ("talent", "priority", "speed", "recommend", "gift", "vip", "privilege", "extreme", "fast", "pay", "member", "bait")
ROW_SELECTORS = (
    ".interested-list",
    "[class*='resume'][class*='list'] [class*='item']",
    "[class*='candidate'][class*='list'] [class*='item']",
)
ROW_FALLBACK_SELECTORS = (
    ".interested-list[infoid]",
    ".interested-list[resumeid]",
    "[infoid][class*='list']",
    "[resumeid][class*='list']",
    "[infoid]",
    "[resumeid]",
)
REFRESH_INTERVAL_SECONDS = 600
DEFAULT_RUN_HOURS = 1
AGE_GLYPH_MAP_CACHE: dict[str, dict[str, str]] = {}
SESSION_CLOSED_ERROR_KEYWORDS = (
    "target page, context or browser has been closed",
    "browsercontext.new_page",
    "browser has been closed",
    "connection closed",
    "browser.disconnected",
)


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


def get_edge_user_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
    return base_dir / "edge_profile"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_runtime_state_file() -> Path:
    return get_base_dir() / "runtime_state.json"


def load_runtime_state() -> dict[str, object]:
    state_file = get_runtime_state_file()
    if not state_file.is_file():
        return {}
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_runtime_state(state: dict[str, object]) -> None:
    state_file = get_runtime_state_file()
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_runtime_state() -> None:
    state_file = get_runtime_state_file()
    try:
        state_file.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def is_browser_session_closed_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(keyword in message for keyword in SESSION_CLOSED_ERROR_KEYWORDS)


def is_browser_connected(browser) -> bool:
    if browser is None:
        return False
    try:
        return bool(browser.is_connected())
    except Exception:
        return False


def round_up_to_refresh_interval(seconds: float) -> int:
    if seconds <= 0:
        return 0
    return int(math.ceil(seconds / REFRESH_INTERVAL_SECONDS) * REFRESH_INTERVAL_SECONDS)


def get_shortcut_path() -> Path:
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        return exe_path.parent / f"{exe_path.stem}_独立数据目录.lnk"
    script_path = Path(__file__).resolve()
    return script_path.parent / f"{script_path.stem}_独立数据目录.lnk"


def ensure_local_shortcut() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return

    shortcut_path = get_shortcut_path()
    if shortcut_path.exists():
        return

    edge_path = find_edge_path()
    base_dir = get_base_dir()
    user_data_dir = get_edge_user_data_dir()
    arguments = f'--user-data-dir="{user_data_dir}" --profile-directory={EDGE_PROFILE_DIRECTORY}'
    script = rf"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{str(shortcut_path).replace("'", "''")}')
$Shortcut.TargetPath = '{str(edge_path).replace("'", "''")}'
$Shortcut.Arguments = '{arguments.replace("'", "''")}'
$Shortcut.WorkingDirectory = '{str(base_dir).replace("'", "''")}'
$Shortcut.Description = '58专用 Edge（独立数据目录：{str(user_data_dir).replace("'", "''")}）'
$Shortcut.Save()
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"已创建快捷方式：{shortcut_path}")
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"创建快捷方式失败，已跳过：{exc}")


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


def parse_running_edge_cdp_port(command_lines: list[str], user_data_dir: Path, port_is_ready) -> int | None:
    normalized_user_data_dir = str(user_data_dir).lower().replace("\\", "/").strip('"')
    user_data_pattern = re.compile(r'--user-data-dir=(?:"([^"]+)"|([^\s]+))', re.IGNORECASE)
    profile_pattern = re.compile(r'--profile-directory=(?:"([^"]+)"|([^\s]+))', re.IGNORECASE)
    port_pattern = re.compile(r"--remote-debugging-port=(\d+)", re.IGNORECASE)

    for command_line in command_lines:
        user_data_match = user_data_pattern.search(command_line)
        if not user_data_match:
            continue
        running_user_data_dir = (user_data_match.group(1) or user_data_match.group(2) or "").lower().replace("\\", "/").strip('"')
        if running_user_data_dir != normalized_user_data_dir:
            continue

        profile_match = profile_pattern.search(command_line)
        running_profile = profile_match.group(1) or profile_match.group(2) if profile_match else ""
        if running_profile and running_profile.lower() != EDGE_PROFILE_DIRECTORY.lower():
            continue

        port_match = port_pattern.search(command_line)
        if not port_match:
            continue
        cdp_port = int(port_match.group(1))
        if port_is_ready(cdp_port):
            return cdp_port
    return None


def read_running_edge_cdp_port(user_data_dir: Path) -> int | None:
    if os.name != "nt":
        return None

    script = rf"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()
Get-CimInstance Win32_Process |
  Where-Object {{ $_.Name -eq 'msedge.exe' -and $_.CommandLine }} |
  Select-Object -ExpandProperty CommandLine
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    def port_is_ready(cdp_port: int) -> bool:
        version_data = read_cdp_version(cdp_port)
        return bool(version_data and version_data.get("webSocketDebuggerUrl"))

    return parse_running_edge_cdp_port(result.stdout.splitlines(), user_data_dir, port_is_ready)


def ensure_compatible_edge_state(user_data_dir: Path) -> None:
    if os.name != "nt":
        return

    normalized_user_data_dir = str(user_data_dir).lower().replace("\\", "/")
    script = """
$processes = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'msedge.exe' } |
  Select-Object ProcessId, CommandLine

if ($processes) {
  $processes | ForEach-Object {
    $line = $_.CommandLine
    if (-not $line) { $line = '' }
    Write-Output (\"{0}`t{1}\" -f $_.ProcessId, $line)
  }
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return

    running_edges = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not running_edges:
        return

    incompatible_edges: list[tuple[str, str]] = []
    for line in running_edges:
        parts = line.split("\t", 1)
        process_id = parts[0] if parts else ""
        command_line = parts[1] if len(parts) > 1 else ""
        normalized = command_line.lower().replace("\\", "/")
        if f"--user-data-dir={normalized_user_data_dir}" not in normalized:
            continue
        if f"--profile-directory={EDGE_PROFILE_DIRECTORY.lower()}" in normalized and "--remote-debugging-port=" in normalized:
            continue
        incompatible_edges.append((process_id, command_line))

    if not incompatible_edges:
        return

    if sys.stdin is None or not sys.stdin.isatty():
        raise RuntimeError("检测到普通 Edge 或其他占用实例正在运行，请先彻底关闭所有 Edge 窗口后再启动程序。")

    print(f"检测到 {len(incompatible_edges)} 个 Edge 占用实例。")
    try:
        answer = input("是否由程序自动关闭这些 Edge 进程并继续？(y/N)：").strip().lower()
    except EOFError:
        raise RuntimeError("检测到普通 Edge 或其他占用实例正在运行，请先彻底关闭所有 Edge 窗口后再启动程序。") from None
    if answer not in {"y", "yes"}:
        raise RuntimeError("检测到普通 Edge 或其他占用实例正在运行，请先彻底关闭所有 Edge 窗口后再启动程序。")

    for process_id, _ in incompatible_edges:
        if not process_id:
            continue
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {process_id} -Force"],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"自动关闭 Edge 进程失败：{process_id}，{exc}") from exc

    time.sleep(1)


def wait_for_enter(prompt: str) -> None:
    if sys.stdin is None or not sys.stdin.isatty():
        return
    try:
        input(prompt)
    except EOFError:
        pass


def prompt_run_duration_seconds() -> float | None:
    if sys.stdin is None or not sys.stdin.isatty():
        print(f"当前为非交互环境，默认运行 {DEFAULT_RUN_HOURS} 小时。")
        return DEFAULT_RUN_HOURS * 3600

    options: list[tuple[str, str, float | None]] = [
        ("1", "1 小时", 1 * 3600),
        ("2", "2 小时", 2 * 3600),
        ("3", "3 小时", 3 * 3600),
        ("4", "4 小时", 4 * 3600),
        ("5", "5 小时", 5 * 3600),
        ("6", "6 小时", 6 * 3600),
        ("7", "7 小时", 7 * 3600),
        ("8", "8 小时", 8 * 3600),
        ("9", "永久执行", None),
    ]
    print("请选择本次运行时长：")
    for key, label, _ in options:
        default_mark = "（默认）" if key == "1" else ""
        print(f"{key}. {label}{default_mark}")

    while True:
        try:
            raw = input("请输入选项编号：").strip()
        except EOFError:
            print(f"\n未读取到输入，默认运行 {DEFAULT_RUN_HOURS} 小时。")
            return DEFAULT_RUN_HOURS * 3600
        if not raw:
            raw = "1"
        for key, _, seconds in options:
            if raw == key:
                return seconds
        print("输入无效，请输入 1-9。")


def wait_for_cdp_ready(
    cdp_port: int,
    edge_process: subprocess.Popen[str],
    user_data_dir: Path,
    timeout_seconds: float = 20,
) -> int:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        version_data = read_cdp_version(cdp_port)
        if version_data and version_data.get("webSocketDebuggerUrl"):
            return cdp_port
        active_cdp_port = read_existing_cdp_port(user_data_dir) or read_running_edge_cdp_port(user_data_dir)
        if active_cdp_port is not None:
            version_data = read_cdp_version(active_cdp_port)
            if version_data and version_data.get("webSocketDebuggerUrl"):
                return active_cdp_port
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
        f"--profile-directory={EDGE_PROFILE_DIRECTORY}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    return subprocess.Popen(args)


def is_login_page(url: str) -> bool:
    normalized = url.lower()
    return any(keyword in normalized for keyword in LOGIN_URL_KEYWORDS)


def is_target_page(page) -> bool:
    return TARGET_URL in page.url


def wait_for_login(context, page, timeout_seconds: float | None = 600):
    if not is_login_page(page.url):
        return page

    print(f"当前位于登录页：{page.url}")
    print("请在 Edge 窗口中完成登录，程序会在登录成功后继续。")
    deadline = None if timeout_seconds is None else time.time() + timeout_seconds
    while deadline is None or time.time() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=1000)
        except Exception:
            pass
        if page.is_closed():
            pages = [item for item in context.pages if not item.is_closed()]
            if pages:
                page = pages[0]
        if not page.is_closed() and not is_login_page(page.url):
            print(f"检测到已离开登录页：{page.url}")
            return page
        time.sleep(1)
    raise TimeoutError("等待用户登录超时，请重新运行程序后再试。")


def ensure_target_page(context, page, login_timeout_seconds: float | None = None):
    if is_target_page(page):
        page.reload(wait_until="domcontentloaded")
    else:
        page.goto(TARGET_URL, wait_until="domcontentloaded")

    page = wait_for_login(context, page, timeout_seconds=login_timeout_seconds)
    if not is_target_page(page):
        print("登录完成，正在进入人才管理页。")
        page.goto(TARGET_URL, wait_until="domcontentloaded")
    return page


def normalize_text(value: object) -> str:
    text = unescape(str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_int(value: object) -> int | None:
    text = normalize_text(value)
    if not text:
        return None
    match = re.search(r"-?\d+", text)
    if not match:
        return None
    return int(match.group())


def normalize_sex(value: object) -> str | None:
    text = normalize_text(value).lower()
    if not text:
        return None
    if text in {"男", "先生", "male", "m", "1"} or "男" in text or "先生" in text:
        return "男"
    if text in {"女", "女士", "female", "f", "0"} or "女" in text or "女士" in text:
        return "女"
    return None


def is_actionable_chat_text(text: object) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if any(keyword in normalized for keyword in CHAT_EXCLUDE_KEYWORDS):
        return False
    return any(keyword in normalized for keyword in ONLINE_CHAT_TEXT_CANDIDATES)


def is_success_chat_text(text: object) -> bool:
    normalized = normalize_text(text)
    return bool(normalized) and any(keyword in normalized for keyword in CHAT_SUCCESS_TEXT_CANDIDATES)


def classify_feedback_texts(texts: list[str]) -> str:
    merged = " ".join(normalize_text(text) for text in texts if normalize_text(text))
    if not merged:
        return "unknown"
    if any(keyword in merged for keyword in CHAT_FAILURE_KEYWORDS):
        return "failure"
    if any(keyword in merged for keyword in CHAT_SUCCESS_TEXT_CANDIDATES):
        return "success"
    return "unknown"


def wait_for_candidate_list(page, timeout_seconds: float = 20) -> None:
    deadline = time.time() + timeout_seconds
    ready_keywords = ("先生", "女士", "在线沟通", "继续沟通", "人才管理")
    while time.time() < deadline:
        for frame in page.frames:
            for selector in ROW_SELECTORS:
                try:
                    locator = frame.locator(selector)
                    if locator.count() > 0:
                        return
                except Exception:
                    continue
            try:
                spans = frame.locator("span, button, a")
                if spans.count() == 0:
                    continue
                texts = [normalize_text(text) for text in spans.all_inner_texts()]
                if any(any(keyword in text for keyword in ready_keywords) for text in texts):
                    return
            except Exception:
                continue
        time.sleep(1)
    raise TimeoutError("等待人才列表超时，未检测到可操作的列表内容。")


def close_known_dialogs(page) -> bool:
    closed_any = False
    # 仅保留具有明确“关闭”标识的选择器
    selectors = [
        ".el-dialog__headerbtn",
        ".el-message-box__close",
        ".el-drawer__close-btn",
        ".el-notification__closeBtn",
        ".el-message__closeBtn",
        ".el-popover__close",
        "i[class*='close']",
        "button[aria-label*='关闭']",
        "button[title*='关闭']",
    ]
    for selector in selectors:
        locator = page.locator(selector)
        for i in range(min(locator.count(), 3)):
            try:
                btn = locator.nth(i)
                if btn.is_visible():
                    btn.click(timeout=500)
                    closed_any = True
            except Exception: continue
    
    # 严格的 JS 过滤：必须包含“关闭”文字或类名
    script = r"""
() => {
  const isVisible = (el) => {
    const style = window.getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && el.getBoundingClientRect().width > 0;
  };
  const candidates = Array.from(document.querySelectorAll('button, a, i, span'))
    .filter(isVisible)
    .filter(el => {
      const text = (el.innerText || el.textContent || '').trim();
      const cls = typeof el.className === 'string' ? el.className.toLowerCase() : '';
      const title = (el.getAttribute('title') || '').toLowerCase();
      // 必须明确包含关闭关键词
      return /关闭|取消|退出|确认|知道了|^×$|^x$/i.test(text) || /close|cancel|confirm/i.test(cls) || /close|cancel/i.test(title);
    });
  
  if (candidates.length > 0) {
    // 限制在弹窗或浮层内，防止误触列表
    const popup = document.querySelector('.el-dialog__wrapper, .el-message-box__wrapper, .el-drawer__wrapper, [role="dialog"], [role="alert"]');
    if (popup) {
      const btn = candidates.find(c => popup.contains(c));
      if (btn) { btn.click(); return true; }
    }
  }
  return false;
}
"""
    try:
        if page.evaluate(script): closed_any = True
    except Exception: pass
    return closed_any


def read_visible_feedback_texts(page) -> list[str]:
    texts: list[str] = []
    popup_selectors = [
        ".el-notification",
        ".el-message",
        ".el-message-box__wrapper",
        ".el-dialog__wrapper",
        ".el-drawer",
        ".chat-dialog",
        "[class*='dialog']",
        "[class*='popup']",
        "[role='dialog']",
    ]
    for selector in popup_selectors:
        locator = page.locator(selector)
        count = min(locator.count(), 3)
        for index in range(count):
            try:
                element = locator.nth(index)
                if not element.is_visible():
                    continue
                text = normalize_text(element.inner_text())
                if text and text not in texts:
                    texts.append(text)
            except Exception:
                continue
    return texts


def get_font_key(page) -> str | None:
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
    normalized = normalize_text(font_key)
    return normalized or None


def get_age_font_family(page) -> str | None:
    script = r"""
() => {
  const selectors = ['.list-info-age', '[class*="age"]', '.interested-list [class*="info-item"]'];
  for (const selector of selectors) {
    const elements = Array.from(document.querySelectorAll(selector));
    for (const element of elements) {
      const text = (element.innerText || element.textContent || '').trim();
      if (!text || !text.includes('岁')) continue;
      const fontFamily = getComputedStyle(element).fontFamily || '';
      if (fontFamily.trim()) return fontFamily;
    }
  }
  return '';
}
"""
    font_family = normalize_text(page.evaluate(script))
    return font_family or None


def wait_for_age_render_ready(page, timeout_ms: int = 2500) -> None:
    script = r"""
() => {
  const selectors = ['.list-info-age', '[class*="age"]'];
  for (const selector of selectors) {
    const elements = Array.from(document.querySelectorAll(selector));
    for (const element of elements) {
      const text = (element.innerText || element.textContent || '').trim();
      if (!text || !text.includes('岁')) continue;
      const style = getComputedStyle(element);
      if ((style.fontFamily || '').trim()) return true;
    }
  }
  return false;
}
"""
    try:
        page.wait_for_function(script, timeout=timeout_ms)
    except Exception:
        pass


def build_paychat_api_url(font_key: str | None) -> str:
    params = [
        "jslState=0",
        "infoId=0",
        "imSource=-1",
        "page=1",
        "pageSize=10",
        "chatState=0",
        "from=pc,hx_manage_interestedlist,other",
        "slotId=pc_hx_manage_interestedlist_list",
        "businessType=0",
        "hxProfession=1",
        "deliveryState=0",
    ]
    if font_key:
        params.append(f"fontkey={font_key}")
    return "https://zpim.58.com/resumepaychat/paychatlist?" + "&".join(params)


def fetch_candidate_items(page, font_key: str | None) -> list[dict[str, object]]:
    api_url = build_paychat_api_url(font_key)
    script = """
    async (url) => {
      const resp = await fetch(url, { credentials: 'include' });
      if (!resp.ok) {
        return { __fetch_error__: `${resp.status} ${resp.statusText}` };
      }
      return await resp.json();
    }
    """
    payload = page.evaluate(script, api_url)
    if not isinstance(payload, dict):
        raise RuntimeError("人才列表接口返回异常，返回结果不是对象。")
    if payload.get("__fetch_error__"):
        raise RuntimeError(f"人才列表接口请求失败：{payload['__fetch_error__']}")
    data = payload.get("data")
    if data is None:
        return []
    if not isinstance(data, dict):
        raise RuntimeError(f"人才列表接口返回异常，data 不是对象：{payload}")
    items = data.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError(f"人才列表接口返回异常，未获取到 items：{payload}")
    return items


def _build_age_glyph_map_once(page, glyphs: list[str], font_key: str | None = None) -> dict[str, str]:
    if not glyphs:
        return {}

    cache_key = font_key or "__default__"
    cached_map = AGE_GLYPH_MAP_CACHE.get(cache_key, {})
    missing_glyphs = [glyph for glyph in glyphs if glyph not in cached_map]
    if not missing_glyphs:
        return {glyph: cached_map[glyph] for glyph in glyphs if glyph in cached_map}

    font_family = get_age_font_family(page)
    if not font_family:
        return {glyph: cached_map[glyph] for glyph in glyphs if glyph in cached_map}

    script = r"""
(input) => {
  const { glyphs, fontFamily } = input;
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
    mapping = page.evaluate(script, {"glyphs": missing_glyphs, "fontFamily": font_family})
    normalized_mapping = {str(k): str(v) for k, v in mapping.items()}
    merged_mapping = dict(cached_map)
    merged_mapping.update(normalized_mapping)
    AGE_GLYPH_MAP_CACHE[cache_key] = merged_mapping
    return {glyph: merged_mapping[glyph] for glyph in glyphs if glyph in merged_mapping}


def build_age_glyph_map(page, age_values: list[str], font_key: str | None = None) -> dict[str, str]:
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

    wait_for_age_render_ready(page)
    for attempt in range(3):
        mapping = _build_age_glyph_map_once(page, glyphs, font_key=font_key)
        if mapping:
            return mapping
        if attempt < 2:
            page.wait_for_timeout(800)
    return {}


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


def infer_age_from_text(text: str, glyph_map: dict[str, str]) -> int | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    match = re.search(r"(\d{1,2})\s*岁", normalized)
    if match:
        return int(match.group(1))
    match = re.search(r"([^\s]{1,3})\s*岁", normalized)
    if match:
        return decode_age(match.group(1) + "岁", glyph_map)
    return None


def pick_item_value(item: dict[str, object], *keys: str) -> object:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def normalize_candidate_from_api(item: dict[str, object], glyph_map: dict[str, str]) -> dict[str, object]:
    name = normalize_text(pick_item_value(item, "name", "realName", "resumeName", "userName"))
    sex = normalize_sex(pick_item_value(item, "sex", "gender", "sexDesc", "genderDesc"))
    age_source = pick_item_value(item, "age", "ageDesc", "ageText")
    age = decode_age(str(age_source), glyph_map)
    if age is None:
        age = parse_int(age_source)
    chat_state = parse_int(pick_item_value(item, "chatState", "imState", "status"))
    chat_text = normalize_text(pick_item_value(item, "chatStateDesc", "statusDesc", "buttonText"))
    return {
        "name": name,
        "sex": sex,
        "age": age,
        "infoid": normalize_text(pick_item_value(item, "infoId", "id")),
        "resumeid": normalize_text(pick_item_value(item, "resumeId")),
        "chat_state": chat_state,
        "chat_text": chat_text,
        "raw": item,
    }


def get_candidate_rows(page):
    for selector in ROW_SELECTORS:
        rows = page.locator(selector)
        if rows.count() > 0:
            return rows
    return page.locator(".interested-list")


def iter_candidate_rows(page):
    selectors = list(dict.fromkeys([*ROW_SELECTORS, *ROW_FALLBACK_SELECTORS]))
    for frame in page.frames:
        for selector in selectors:
            try:
                rows = frame.locator(selector)
                total = rows.count()
            except Exception:
                continue
            if total <= 0:
                continue
            for index in range(total):
                yield rows.nth(index)
            break


def extract_row_snapshot(row, glyph_map: dict[str, str]) -> dict[str, object]:
    script = r"""
(node) => {
  const isVisible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    if (!style) return false;
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') {
      return false;
    }
    if (Number(style.opacity || '1') === 0) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const textOf = (selectorList) => {
    for (const selector of selectorList) {
      const element = node.querySelector(selector);
      if (element && element.innerText && element.innerText.trim()) {
        return element.innerText.trim();
      }
    }
    return '';
  };
  const clickable = Array.from(node.querySelectorAll('button, a, [role="button"]'))
    .filter((element) => isVisible(element))
    .map((element) => (element.innerText || element.textContent || '').trim())
    .filter(Boolean);
  const html = node.outerHTML || '';
  const attrs = [
    node.getAttribute('infoid'),
    node.getAttribute('resumeid'),
    node.getAttribute('data-infoid'),
    node.getAttribute('data-id'),
    node.id,
  ]
    .filter(Boolean);
  const hrefs = Array.from(node.querySelectorAll('a[href]')).map((item) => item.getAttribute('href') || '');
  return {
    name: textOf(['.list-name-box span', '[class*="name"] span', '[class*="name"]']),
    ageText: textOf(['.list-info-age', '[class*="age"]']),
    sexText: textOf(['.list-sex-icon', '[class*="sex"]', '[class*="gender"]']),
    text: (node.innerText || '').trim(),
    buttonTexts: clickable,
    html,
    hints: attrs.concat(hrefs),
    infoId: node.getAttribute('infoid') || node.getAttribute('data-infoid') || node.getAttribute('data-id') || '',
    resumeId: node.getAttribute('resumeid') || '',
  };
}
"""
    snapshot = row.evaluate(script)
    full_text = normalize_text(snapshot.get("text", ""))
    age_text = normalize_text(snapshot.get("ageText", ""))
    sex_text = normalize_text(snapshot.get("sexText", ""))
    button_texts = [normalize_text(text) for text in snapshot.get("buttonTexts", []) if normalize_text(text)]
    selected_button_text = ""
    # 优先寻找精准匹配
    for text in button_texts:
        if text == "在线沟通":
            selected_button_text = text
            break
    # 其次寻找模糊匹配但排除干扰
    if not selected_button_text:
        for text in button_texts:
            if is_actionable_chat_text(text):
                selected_button_text = text
                break
    # 最后兜底
    if not selected_button_text:
        selected_button_text = next((text for text in button_texts if "沟通" in text and not any(kw in text for kw in CHAT_EXCLUDE_KEYWORDS)), button_texts[0] if button_texts else "")

    return {
        "name": normalize_text(snapshot.get("name", "")),
        "age_text": age_text,
        "sex": normalize_sex(sex_text or full_text),
        "age": infer_age_from_text(age_text or full_text, glyph_map),
        "button_texts": button_texts,
        "button_text": selected_button_text,
        "text": full_text,
        "infoid": normalize_text(snapshot.get("infoId", "")),
        "resumeid": normalize_text(snapshot.get("resumeId", "")),
    }


def build_page_candidates(page, glyph_map: dict[str, str]) -> list[dict[str, object]]:
    selectors = list(dict.fromkeys([*ROW_SELECTORS, *ROW_FALLBACK_SELECTORS]))

    for _ in range(3):
        for selector in selectors:
            best_candidates: list[dict[str, object]] = []
            for frame in page.frames:
                try:
                    rows = frame.locator(selector)
                    total = rows.count()
                except Exception:
                    continue

                if total <= 0:
                    continue

                current_candidates: list[dict[str, object]] = []
                for index in range(total):
                    row = rows.nth(index)
                    try:
                        snapshot = extract_row_snapshot(row, glyph_map)
                    except Exception:
                        continue

                    has_identity = bool(snapshot.get("infoid") or snapshot.get("resumeid") or snapshot.get("name"))
                    has_button = bool(snapshot.get("button_text"))
                    if not has_identity and not has_button:
                        continue

                    snapshot["row"] = row
                    snapshot["index"] = index
                    current_candidates.append(snapshot)

                if len(current_candidates) > len(best_candidates):
                    best_candidates = current_candidates

            if best_candidates:
                return best_candidates
        page.wait_for_timeout(1000)
    return []


def merge_candidate(page_candidate: dict[str, object], api_candidate: dict[str, object] | None) -> dict[str, object]:
    merged = dict(page_candidate)
    if not api_candidate:
        return merged
    for key in ("name", "sex", "age", "infoid", "resumeid"):
        if merged.get(key) in (None, "", 0):
            merged[key] = api_candidate.get(key)
    merged["api_chat_state"] = api_candidate.get("chat_state")
    merged["api_chat_text"] = api_candidate.get("chat_text")
    return merged


def is_target_candidate(candidate: dict[str, object]) -> tuple[bool, str]:
    button_text = normalize_text(candidate.get("button_text", ""))
    if not is_actionable_chat_text(button_text):
        return False, f"按钮不是可发起状态：{button_text or '空'}"

    sex = normalize_sex(candidate.get("sex", ""))
    if sex != "男":
        return False, f"性别不匹配：{sex or '未知'}"

    age = candidate.get("age")
    if not isinstance(age, int):
        return False, "年龄无法识别"
    if not (18 <= age <= 55):
        return False, f"年龄不在范围内：{age}"
    return True, ""


def find_real_chat_button(row):
    # 干扰项类名/属性黑名单
    FORBIDDEN_ATTRS = ("talent", "priority", "speed", "recommend", "gift", "vip", "privilege", "extreme", "fast", "pay")
    
    # 候选选择器
    selectors = [
        "button.list-chat-btn, a.list-chat-btn",
        "button[class*='chat-btn'], a[class*='chat-btn']",
        "button.el-button--primary",
        "button, a",
    ]
    
    def is_valid_button(btn):
        try:
            if not btn.is_visible(): return False
            # 获取完整 HTML 进行深层检查
            html = btn.evaluate("el => el.outerHTML").lower()
            if any(kw in html for kw in CHAT_EXCLUDE_KEYWORDS): return False
            if any(kw in html for kw in FORBIDDEN_ATTRS): return False
            
            text = normalize_text(btn.inner_text())
            if any(kw in text for kw in CHAT_EXCLUDE_KEYWORDS): return False
            
            aria_disabled = normalize_text(btn.get_attribute("aria-disabled") or "")
            if aria_disabled in {"true", "1"} or btn.get_attribute("disabled") is not None: return False
            return True
        except Exception: return False

    # 优先级 1: 精准文本匹配且合法
    for selector in selectors:
        buttons = row.locator(selector)
        for i in range(buttons.count()):
            btn = buttons.nth(i)
            if is_valid_button(btn) and normalize_text(btn.inner_text()) == "在线沟通":
                return btn

    # 优先级 2: 候选名单匹配且合法
    for selector in selectors:
        buttons = row.locator(selector)
        actionable_button = None
        success_button = None
        for i in range(buttons.count()):
            btn = buttons.nth(i)
            if not is_valid_button(btn): continue
            
            text = normalize_text(btn.inner_text())
            if text in ONLINE_CHAT_TEXT_CANDIDATES:
                actionable_button = btn
                break
            if success_button is None and text in CHAT_SUCCESS_TEXT_CANDIDATES:
                success_button = btn
        
        if actionable_button is not None: return actionable_button
        if success_button is not None: return success_button
        
    return None


def is_button_click_target_clear(page, button) -> bool:
    try:
        box = button.bounding_box()
    except Exception:
        return False
    if not box:
        return False
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    script = r"""
([element, x, y]) => {
  const target = document.elementFromPoint(x, y);
  if (!target) return false;
  return target === element || element.contains(target) || target.contains(element);
}
"""
    try:
        return bool(page.evaluate(script, [button.element_handle(), x, y]))
    except Exception:
        return False


def find_target_row(page, target: dict[str, object], glyph_map: dict[str, str]):
    for row in iter_candidate_rows(page):
        try:
            snapshot = extract_row_snapshot(row, glyph_map)
        except Exception:
            continue
        target_resumeid = normalize_text(target.get("resumeid", ""))
        if target_resumeid and snapshot.get("resumeid") == target_resumeid:
            button = find_real_chat_button(row)
            if button is not None:
                return row, button
        target_name = normalize_text(target.get("name", ""))
        target_age = target.get("age")
        if snapshot.get("name") != target_name:
            continue
        if isinstance(target_age, int) and snapshot.get("age") not in (None, target_age):
            continue
        button = find_real_chat_button(row)
        if button is None:
            continue
        return row, button
    return None, None


def build_candidate_key(candidate: dict[str, object]) -> str:
    resumeid = normalize_text(candidate.get("resumeid", ""))
    if resumeid:
        return f"resumeid:{resumeid}"
    name = normalize_text(candidate.get("name", ""))
    age = candidate.get("age")
    age_text = str(age) if isinstance(age, int) else normalize_text(candidate.get("age_text", ""))
    return f"name:{name}|age:{age_text}"


def format_candidate_log_label(candidate: dict[str, object], target_key: str) -> str:
    name = normalize_text(candidate.get("name", "")) or "未知候选人"
    age = candidate.get("age")
    age_text = str(age) if isinstance(age, int) else "年龄未知"
    return f"{name}({age_text}) | key={target_key}"


def prepare_cycle_runtime_state(cycle: int) -> dict[str, object]:
    state = load_runtime_state()
    if state.get("cycle") != cycle:
        state = {"cycle": cycle, "completed_targets": [], "current_target": ""}
        save_runtime_state(state)
        return state
    completed_targets = state.get("completed_targets")
    if not isinstance(completed_targets, list):
        state["completed_targets"] = []
    current_target = state.get("current_target")
    if not isinstance(current_target, str):
        state["current_target"] = ""
    save_runtime_state(state)
    return state


def set_current_runtime_target(cycle: int, target_key: str) -> dict[str, object]:
    state = prepare_cycle_runtime_state(cycle)
    state["current_target"] = target_key
    save_runtime_state(state)
    return state


def mark_runtime_target_completed(cycle: int, target_key: str) -> dict[str, object]:
    state = prepare_cycle_runtime_state(cycle)
    completed_targets = list(state.get("completed_targets", []))
    if target_key not in completed_targets:
        completed_targets.append(target_key)
    state["completed_targets"] = completed_targets
    state["current_target"] = ""
    save_runtime_state(state)
    return state


def click_matching_online_chat(page, cycle: int) -> None:
    close_known_dialogs(page)
    font_key = get_font_key(page)
    api_error = ""
    items: list[dict[str, object]] = []
    try:
        items = fetch_candidate_items(page, font_key)
    except Exception as exc:
        api_error = str(exc)
    glyph_map = build_age_glyph_map(page, [str(item.get("age", "")) for item in items], font_key=font_key) if items else {}

    api_candidates = [normalize_candidate_from_api(item, glyph_map) for item in items]
    api_by_resumeid = {str(item["resumeid"]): item for item in api_candidates if item.get("resumeid")}
    api_by_name = {str(item["name"]): item for item in api_candidates if item.get("name")}

    page_candidates = build_page_candidates(page, glyph_map)
    matches: list[dict[str, object]] = []
    skipped_reasons: list[str] = []
    skipped_unknown_age: list[str] = []
    for page_candidate in page_candidates:
        api_candidate = None
        if page_candidate.get("resumeid"):
            api_candidate = api_by_resumeid.get(str(page_candidate["resumeid"]))
        if api_candidate is None and page_candidate.get("name"):
            api_candidate = api_by_name.get(str(page_candidate["name"]))
        merged = merge_candidate(page_candidate, api_candidate)
        ok, reason = is_target_candidate(merged)
        if ok:
            matches.append(merged)
        else:
            label = normalize_text(merged.get("name", "")) or f"第{int(merged.get('index', 0)) + 1}行"
            age = merged.get("age")
            age_text = str(age) if isinstance(age, int) else "年龄未知"
            label = f"{label}({age_text})"
            if reason == "年龄无法识别":
                skipped_unknown_age.append(label)
            else:
                skipped_reasons.append(f"{label} - {reason}")

    state = prepare_cycle_runtime_state(cycle)
    completed_targets = {str(item) for item in state.get("completed_targets", [])}
    current_target = str(state.get("current_target", "") or "")
    match_keys = [build_candidate_key(match) for match in matches]
    if matches:
        print("命中候选诊断：")
        for index, match in enumerate(matches, start=1):
            target_key = build_candidate_key(match)
            print(f"- 第 {index} 个：{format_candidate_log_label(match, target_key)}")
    if current_target and current_target not in match_keys:
        print(f"断点目标已不在当前列表中，改为从下一位继续：{current_target}")
        state["current_target"] = ""
        save_runtime_state(state)
        current_target = ""

    clicked: list[str] = []
    skipped_not_online: list[str] = []
    resume_waiting = bool(current_target)
    for match in matches:
        target_key = build_candidate_key(match)
        log_label = format_candidate_log_label(match, target_key)
        print(f"处理命中候选：{log_label}")
        if target_key in completed_targets:
            print(f"跳过命中候选：{log_label}，原因：本轮已处理过相同 key。")
            continue
        if resume_waiting and target_key != current_target:
            print(f"跳过命中候选：{log_label}，原因：等待恢复断点 {current_target}。")
            continue
        resume_waiting = False
        set_current_runtime_target(cycle, target_key)

        close_known_dialogs(page)
        row = match.get("row")
        button = None
        if row is not None:
            try:
                button = find_real_chat_button(row)
            except Exception:
                button = None
        if row is None or button is None:
            row, button = find_target_row(page, match, glyph_map)
        if row is None or button is None:
            print(f"跳过命中候选：{log_label}，原因：页面未找到对应行或按钮。")
            skipped_not_online.append(f"{match['name']}({match['age']}) - 页面未找到")
            completed_targets.add(target_key)
            mark_runtime_target_completed(cycle, target_key)
            continue
        success = False
        last_button_text = ""
        failure_reason = ""
        for _ in range(3):
            button.scroll_into_view_if_needed()
            if not is_button_click_target_clear(page, button):
                close_known_dialogs(page)
                page.wait_for_timeout(300)
                try:
                    button = find_real_chat_button(row)
                except Exception:
                    button = None
                if button is None:
                    failure_reason = "目标按钮被遮挡"
                    print(f"点击诊断：{log_label}，目标按钮被遮挡，重新查找按钮失败。")
                    break
                button.scroll_into_view_if_needed()
                if not is_button_click_target_clear(page, button):
                    failure_reason = "目标按钮被遮挡"
                    print(f"点击诊断：{log_label}，目标按钮被遮挡。")
                    break
            last_button_text = normalize_text(button.inner_text())
            button_html = button.evaluate("el => el.outerHTML")
            print(f"诊断：准备点击按钮 - 候选人: {match['name']} | 文本: {last_button_text} | HTML: {button_html}")

            if is_success_chat_text(last_button_text):
                success = True
                break
            if not is_actionable_chat_text(last_button_text):
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
            feedback_texts = read_visible_feedback_texts(page)
            feedback_result = classify_feedback_texts(feedback_texts)
            try:
                updated_text = normalize_text(button.inner_text())
            except Exception:
                updated_text = ""
            if updated_text and updated_text != last_button_text and not is_actionable_chat_text(updated_text):
                last_button_text = updated_text
                success = True
                break
            if is_success_chat_text(updated_text):
                last_button_text = updated_text
                success = True
                break
            if feedback_result == "success":
                last_button_text = "弹框提示成功"
                success = True
                close_known_dialogs(page)
                break
            if feedback_result == "failure":
                failure_reason = " / ".join(feedback_texts)
                close_known_dialogs(page)
                break
            close_known_dialogs(page)
        if success:
            clicked.append(f"{match['name']}({match['age']})")
        else:
            final_reason = failure_reason or f"最终按钮是{last_button_text or '未识别'}"
            skipped_not_online.append(f"{match['name']}({match['age']}) - {final_reason}")
        completed_targets.add(target_key)
        mark_runtime_target_completed(cycle, target_key)

    clear_runtime_state()
    print("筛选结果：")
    print(f"字体 key：{font_key or '未获取'}")
    print(f"年龄解码映射：{glyph_map}")
    print(f"页面候选行数：{len(page_candidates)}")
    print(f"接口候选数：{len(api_candidates)}")
    if api_error:
        print(f"接口诊断：{api_error}")
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
    if skipped_reasons:
        print("未命中原因：")
        for item in skipped_reasons:
            print(f"- {item}")


def run_once(context, page, cycle: int, login_timeout_seconds: float | None = None):
    page = ensure_target_page(context, page, login_timeout_seconds)
    page.wait_for_load_state("domcontentloaded")
    wait_for_candidate_list(page)
    page.bring_to_front()
    click_matching_online_chat(page, cycle)
    return page


def connect_to_managed_edge(playwright, edge_path: Path, user_data_dir: Path):
    cdp_port = read_existing_cdp_port(user_data_dir) or read_running_edge_cdp_port(user_data_dir)
    if cdp_port is None:
        cdp_port = choose_cdp_port()
        edge_process = launch_edge(edge_path, user_data_dir, cdp_port)
        cdp_port = wait_for_cdp_ready(cdp_port, edge_process, user_data_dir)
    else:
        edge_process = None

    version_data = read_cdp_version(cdp_port)
    websocket_url = str(version_data.get("webSocketDebuggerUrl", "")) if version_data else ""
    if not websocket_url:
        raise RuntimeError(f"未获取到 Edge 的 webSocketDebuggerUrl（{CDP_HOST}:{cdp_port}）。")

    try:
        browser = playwright.chromium.connect_over_cdp(websocket_url)
    except Exception as exc:
        raise RuntimeError(f"CDP 连接失败（{CDP_HOST}:{cdp_port}）：{exc}") from exc
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.pages[0] if context.pages else context.new_page()
    return browser, context, page, edge_process, cdp_port


def run_periodically(playwright, edge_path: Path, user_data_dir: Path, browser, context, page, run_duration_seconds: float | None) -> None:
    cycle = 1
    deadline = None if run_duration_seconds is None else time.time() + run_duration_seconds
    allow_cycle_after_deadline = False
    while True:
        cycle_after_deadline = False
        if deadline is not None and time.time() >= deadline:
            if not allow_cycle_after_deadline:
                break
            allow_cycle_after_deadline = False
            cycle_after_deadline = True
        print(f"开始第 {cycle} 轮执行：{time.strftime('%Y-%m-%d %H:%M:%S')}")
        cycle_succeeded = False
        while True:
            if deadline is None:
                remaining_seconds = None
            elif cycle_after_deadline:
                remaining_seconds = REFRESH_INTERVAL_SECONDS
            else:
                remaining_seconds = round_up_to_refresh_interval(deadline - time.time())
            try:
                if not is_browser_connected(browser):
                    raise RuntimeError("browser has been closed")
                if page.is_closed():
                    page = context.new_page()
                page = run_once(context, page, cycle, login_timeout_seconds=remaining_seconds)
                cycle_succeeded = True
                break
            except Exception as exc:
                if is_browser_session_closed_error(exc) or not is_browser_connected(browser):
                    print(f"检测到浏览器连接已断开，正在重连并继续第 {cycle} 轮：{exc}")
                    browser, context, page, _, cdp_port = connect_to_managed_edge(playwright, edge_path, user_data_dir)
                    print(f"重连成功，调试地址：http://{CDP_HOST}:{cdp_port}")
                    continue
                clear_runtime_state()
                print(f"第 {cycle} 轮执行失败：{exc}")
                break
        if deadline is None:
            wait_seconds = REFRESH_INTERVAL_SECONDS
            if cycle_succeeded:
                print(f"第 {cycle} 轮执行完成，{int(wait_seconds // 60)} 分钟后刷新重试，当前为永久执行。")
            else:
                print(f"第 {cycle} 轮执行失败，{int(wait_seconds // 60)} 分钟后重试，当前为永久执行。")
        else:
            remaining_seconds = deadline - time.time()
            rounded_remaining_seconds = round_up_to_refresh_interval(remaining_seconds)
            if rounded_remaining_seconds <= 0:
                break
            wait_seconds = REFRESH_INTERVAL_SECONDS
            allow_cycle_after_deadline = wait_seconds > remaining_seconds
            if cycle_succeeded:
                print(
                    f"第 {cycle} 轮执行完成，"
                    f"{int(wait_seconds // 60)} 分钟后刷新重试，剩余运行 {int(rounded_remaining_seconds // 60)} 分钟。"
                )
            else:
                print(
                    f"第 {cycle} 轮执行失败，"
                    f"{int(wait_seconds // 60)} 分钟后重试，剩余运行 {int(rounded_remaining_seconds // 60)} 分钟。"
                )
        cycle += 1
        time.sleep(wait_seconds)
    if deadline is not None:
        print("已到达设定运行时间，程序即将退出。")


def open_58_with_cdp() -> None:
    edge_path = find_edge_path()
    user_data_dir = get_edge_user_data_dir()
    ensure_compatible_edge_state(user_data_dir)
    run_duration_seconds = prompt_run_duration_seconds()

    edge_process = None
    with sync_playwright() as playwright:
        browser, context, page, edge_process, cdp_port = connect_to_managed_edge(playwright, edge_path, user_data_dir)
        if run_duration_seconds is None:
            print("本次计划永久执行。")
        else:
            print(f"本次计划运行 {run_duration_seconds / 3600:g} 小时。")
        print(f"已通过 CDP 接管 Edge，并打开：{TARGET_URL}")
        print(f"Edge 路径：{edge_path}")
        print(f"资料目录：{user_data_dir}")
        print(f"Profile 目录：{EDGE_PROFILE_DIRECTORY}")
        print(f"调试地址：http://{CDP_HOST}:{cdp_port}")
        run_periodically(playwright, edge_path, user_data_dir, browser, context, page, run_duration_seconds)

    clear_runtime_state()
    if edge_process and edge_process.poll() is None:
        print("程序退出后不会主动关闭 Edge。")


def main() -> int:
    try:
        ensure_local_shortcut()
        open_58_with_cdp()
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        wait_for_enter("按回车退出...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


