# Agent Instructions

1.  **Language**: Respond to the user in **Chinese**.
2.  **Runtime**: Always use `uv` for executing scripts.
3.  **Sync**: Update `API_DOCUMENTATION.md` if any API endpoint or field changes.
4.  **Minimalism**: Follow the principle of **minimal changes**. Do not perform unrelated refactoring, add extra features, or include unnecessary code/comments unless explicitly requested.
5.  **Code Style**: Keep Python comments in **Chinese** as per user preference.

## Project Memory

- 项目主入口是 `main.py`，打包产物是 `dist\\edge_58_launcher.exe`。
- 运行环境是 Windows，浏览器固定使用系统 `msedge.exe`，并使用程序同目录下的独立 `edge_profile`。
- 用户常说的“读取记忆”就是先读取本文件 `GEMINI.md`，必要时再结合 `README.md` 一起看。
- 登录后如果跳到新的标签页，程序现在会在整个浏览器会话里寻找真正离开登录页的标签页，再继续进入 `https://employer.58.com/main/jobmanage`。
- 页面启动时不再主动打开额外的 `about:blank` 空白页。
- 人才列表的页面候选行识别已经做过兼容，不同账号下要优先相信页面真实按钮和页面真实列表行，不要再用过宽的容器选择器覆盖精确结果。
- 年龄判断现在是“页面直读/动态字体解码优先，接口辅助，失败短重试”，并带 `font key` 缓存；不同账号切换后年龄仍可能因页面渲染时机偶发失败，所以看日志时要同时关注：
  - `年龄解码映射`
  - `年龄无法解码，已跳过`
  - `未命中原因` 里每个人后面的年龄
- 日志里的 `未命中原因` 现在会显示每个人的解码后年龄，例如 `苗先生(43)`。
- 是否允许点击，当前以页面按钮状态为准；接口 `chatState` 不再作为硬拦截条件，因为已验证存在“接口显示不可点，但手动实际可点”的情况。
- 如果 `build.ps1` 因网络超时无法拉取 `pyinstaller`，说明是 `uv run --with pyinstaller` 联网失败，不一定是代码问题。
- 如果离线重打包，必须确保把 `.venv\\Lib\\site-packages` 带进 PyInstaller，否则 `exe` 可能启动时报 `ModuleNotFoundError: No module named 'playwright'`。
