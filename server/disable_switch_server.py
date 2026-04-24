from __future__ import annotations

import socketserver


HOST = "0.0.0.0"
PORT = 39000
REQUEST_TEXT = "PING_58"
RESPONSE_BYTES = b"DISABLE_58\n"


class DisableSwitchHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            payload = self.request.recv(64).decode("utf-8", errors="ignore").strip()
            if payload == REQUEST_TEXT:
                self.request.sendall(RESPONSE_BYTES)
                print(f"已响应禁用探测：{self.client_address[0]}:{self.client_address[1]}")
        except OSError as exc:
            print(f"处理连接失败：{exc}")


class ThreadedTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def main() -> None:
    with ThreadedTCPServer((HOST, PORT), DisableSwitchHandler) as server:
        print(f"禁用开关服务已启动：{HOST}:{PORT}")
        print("收到 PING_58 时返回 DISABLE_58。按 Ctrl+C 停止服务。")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n禁用开关服务已停止。")


if __name__ == "__main__":
    main()
