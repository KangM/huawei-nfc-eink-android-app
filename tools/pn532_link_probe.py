"""PN532 链路死因排查：只发 GetFirmwareVersion，看串口到底有没有任何字节回来。

区分三种情况：
  · 一个字节都没有  ⇒ 模块没电 / 芯片没跑 / USB 线只供电不通数据
  · 有字节但是乱码  ⇒ 波特率不对（换速率能对上就不是坏）
  · 正常 00 00 FF 00 FF 00 ... 32 1.6 ... ⇒ 模块是活的，问题在上游初始化参数

用法： python tools/pn532_link_probe.py --port COM14
"""
from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
except ImportError:  # pragma: no cover
    print("缺少 pyserial；用装了 ink_nfc_fmsc 的 venv 运行本脚本。", file=sys.stderr)
    raise SystemExit(2)

PREAMBLE = bytes([0x55] * 16)
# GetFirmwareVersion: 00 00 FF 02 FE D4 02 2A 00
GET_FW = bytes.fromhex("00000002FED4022A00")
BAUDS = (115200, 9600, 38400, 57600, 230400)


def attempt(port: str, baud: int, *, reset: bool) -> tuple[bytes, str]:
    try:
        link = serial.Serial(port, baud, timeout=0.15, write_timeout=1.0)
    except Exception as exc:
        return b"", f"打不开：{exc}"
    try:
        if reset:
            # 部分 CH340 板把 DTR 接到 PN532 的 RSTPDN 上，拉低再放开等于硬复位
            link.dtr = True
            time.sleep(0.05)
            link.dtr = False
        link.dtr = False
        link.rts = False
        time.sleep(0.15)
        link.reset_input_buffer()
        link.write(PREAMBLE + GET_FW)
        link.flush()
        time.sleep(0.05)
        link.write(GET_FW)
        link.flush()
        raw = b""
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            chunk = link.read(64)
            if chunk:
                raw += chunk
                deadline = time.monotonic() + 0.3
        return raw, ""
    finally:
        try:
            link.close()
        except Exception:
            pass


def describe(raw: bytes) -> str:
    if not raw:
        return "零字节回包"
    if b"\x00\x00\xff" in raw:
        return "见到 PN532 帧头 00 00 FF —— 模块活着"
    printable = sum(1 for b in raw if 32 <= b < 127)
    if printable / len(raw) > 0.8:
        return "回来的全是可打印 ASCII —— 很可能是别的串口设备"
    return "有数据但不是 PN532 帧 —— 波特率/线路可疑"


def selftest() -> int:
    checks = []
    checks.append(("零字节判「零回包」", describe(b"") == "零字节回包"))
    checks.append(("帧头判活", "活着" in describe(b"\x00\x00\xff\x00\xff\x00\x02\xfe\xd5\x03")))
    checks.append(("ASCII 判别的设备", "别的串口设备" in describe(b"hello world, this is not a pn532\r\n")))
    checks.append(("乱码判速率可疑", "波特率" in describe(b"\xde\xad\xbe\xef")))
    ok = sum(1 for _, p in checks if p)
    for name, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + name)
    print(f"selftest: {ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", default="COM14")
    p.add_argument("--baud", type=int, action="append", help="只测指定速率，可重复")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        return selftest()

    bauds = args.baud or list(BAUDS)
    print(f"端口 {args.port}，依次试 {len(bauds)} 个速率，每个速率最多等 0.8 秒\n")
    alive = False
    for baud in bauds:
        for reset in (False, True):
            raw, err = attempt(args.port, baud, reset=reset)
            tag = "DTR复位后" if reset else "直接问  "
            if err:
                print(f"  {baud:>7}  {tag}  {err}")
                break
            print(f"  {baud:>7}  {tag}  {len(raw):3d} 字节  {describe(raw)}"
                  + (f"  | {raw[:24].hex(' ').upper()}" if raw else ""))
            if b"\x00\x00\xff" in raw:
                alive = True
    print()
    if alive:
        print("结论：PN532 芯片有应答。问题在读卡器初始化参数或线圈耦合，不在供电/USB。")
        return 0
    print("结论：本次没有任何 PN532 帧。按顺序排查：\n"
          "  1) 拔掉外接 3.3 V 再测一次 —— 若这时通了，说明外部负载把模块电源拖垮了（别从 PN532 取电）\n"
          "  2) 拔插 USB：部分 PN532 在帧发到一半被打断后会卡住，必须硬复位\n"
          "  3) 换一根**能传数据**的 USB 线（很多线只有电源线）\n"
          "  4) 检查模块上的 HSU/I2C/SPI 拨码或跳线是否被碰过（HSU 才能走串口）")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
