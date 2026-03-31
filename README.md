# Edge 58 Launcher

这个程序会：

1. 查找当前 Windows 上安装的 `msedge.exe`
2. 使用 `exe` 同目录下的独立 Edge 资料目录
3. 以自动分配的 `--remote-debugging-port` 启动 Edge
4. 通过 CDP 接管浏览器并打开 `https://www.58.com/`

## 运行

```powershell
uv run main.py
```

## 打包 exe

```powershell
.\build.ps1
```

打包结果默认在 `dist\edge_58_launcher.exe`。

## 使用说明

- 目标机器需要已经安装 Microsoft Edge。
- 程序会在 `exe` 同目录自动创建 `edge_profile` 资料目录。
- 这个资料目录会保留登录态、Cookie 等独立浏览器数据。
- 每次运行会自动选择一个空闲 CDP 端口，避免与本机已有 Edge 调试实例冲突。

## 原因说明

从 Chromium / Edge 新版本开始，默认资料目录不再适合直接配合 `--remote-debugging-port` 使用。因此本程序固定使用独立资料目录：

- `exe` 可以拷到别的设备运行。
- 资料目录和 `exe` 放在一起，便于迁移和备份。
- 首次运行会自动创建资料目录。
