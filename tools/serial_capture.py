"""抓一段串口日志（用于看固件的分阶段输出）。

只做一件事：开串口 -> 用 RTS 复位一次（DTR 保持不拉低，避免进下载模式）->
原样打印收到的字节，直到超时。

用法：
    python tools/serial_capture.py --port COM11 --seconds 45
    python tools/serial_capture.py --port COM11 --seconds 45 --no-reset
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    print("缺少 pyserial", file=sys.stderr)
    raise SystemExit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--seconds", type=float, default=45.0)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--no-reset", action="store_true", help="不复位，直接贴着看当前输出")
    ap.add_argument("--send", default=None, help="在 --send-after 秒后发送这串字符（会补一个回车）")
    ap.add_argument("--send-after", type=float, default=1.0)
    ap.add_argument("--raw", action="store_true", help="发送时不追加回车")
    args = ap.parse_args(argv)

    try:
        link = serial.Serial(args.port, args.baud, timeout=0.2)
    except Exception as exc:
        print(f"打不开 {args.port}：{exc}", file=sys.stderr)
        return 2

    try:
        link.dtr = False
        link.rts = False
        if not args.no_reset:
            # RTS 接 EN：拉低再放开等于按一下复位；DTR 保持高电平原样，IO0 不会被拉低
            link.rts = True
            time.sleep(0.15)
            link.rts = False
        link.reset_input_buffer()

        started = time.monotonic()
        buf = bytearray()
        pending = b""
        sent = False
        while time.monotonic() - started < args.seconds:
            if args.send and not sent and time.monotonic() - started >= args.send_after:
                payload = args.send.encode()
                if not args.raw:
                    payload += b"\r\n"
                link.write(payload)
                link.flush()
                print(f"[{time.monotonic() - started:5.1f}s] >>> 已发送 {args.send!r}")
                sent = True
            chunk = link.read(256)
            if not chunk:
                continue
            buf += chunk
            pending += chunk
            # 串口按块到达，必须自己攒整行再解码，否则中文字符会被切在中间变成乱码
            *lines, pending = pending.split(b"\n")
            stamp = time.monotonic() - started
            for raw in lines:
                line = raw.decode("utf-8", "replace").rstrip("\r")
                if line.strip():
                    print(f"[{stamp:5.1f}s] {line}")
        if pending.strip():
            print(f"[{time.monotonic() - started:5.1f}s] "
                  f"{pending.decode('utf-8', 'replace').rstrip()}")
    finally:
        try:
            link.close()
        except Exception:
            pass

    print(f"\n--- 共 {len(buf)} 字节 ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
