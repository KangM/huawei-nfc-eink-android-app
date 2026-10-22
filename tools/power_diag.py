#!/usr/bin/env python3
"""68CA 供电诊断：把「链路余量」和「刷新在哪一步掉压」变成可以对比的数字。

两个子命令，都不写驱动、不传图：

  link      重复只读 probe，统计激活成功率与耗时 —— 这是**耦合余量的代理指标**。
            换线圈、挪位置、加压、加垫片前后各跑一次，用数字判断有没有变好，
            而不是靠手感。失败率一旦非零，就别浪费一次刷新。
  timeline  解析一次 refresh 的运行日志，判断 68CA 是「刚启动就欠压」还是
            「刷到一半掉压」，两者对应的修法完全不同。

用法：
  python tools/power_diag.py link --port COM14 --repeat 20 --label 原位置
  python tools/power_diag.py link --port COM14 --repeat 20 --label 加垫片
  python tools/power_diag.py timeline            # 自动取最新一次运行
  python tools/power_diag.py timeline --log logs/20261022_031100_612198_e0jz1gml/run.log
  python tools/power_diag.py selftest            # 离线自检，不需要硬件

为什么用「激活成功率」当电压表：驱动板完全靠射频场取电，场强余量越大，
寻卡就越快越稳。掉到临界点时，表现就是重试、超时、偶发 TAG_NOT_FOUND。
它不能读出伏特数，但足以排序两个摆放位置谁更好。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"

# 上游工具装在隔离 venv 里，脚本自己不一定能 import 到它。
DEFAULT_PYTHON = Path(r"C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc\Scripts\python.exe")

# SW 里属于「电不够」的那一类（见上游 protocol.py 的 STATUS 表）。
LOW_VOLTAGE_SW = {0x68CA: "低电压告警", 0x698A: "低电压告警", 0x6502: "低电压导致 NVM 停止"}
TIMEOUT_SW = {0x68CB: "刷新超时告警"}

LINE_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s+\[(\w+)\]\s*(?:\[([^\]]+)\])?\s*(.*)$")
SW_RE = re.compile(r"SW=([0-9A-Fa-f]{4})")
ELAPSED_RE = re.compile(r"已用\s*([0-9.]+)\s*秒")


# --------------------------------------------------------------------------- timeline

def _clock(line: str) -> float | None:
    match = LINE_RE.match(line)
    if not match:
        return None
    hh, mm, ss = (int(match.group(i)) for i in (1, 2, 3))
    return hh * 3600 + mm * 60 + ss


def parse_run_log(text: str) -> dict:
    """从 run.log 里抠出刷新阶段的时间线。日志只有秒级精度，够用。"""
    info = {
        "d4_seconds": None,      # D4 被接受的时刻
        "error_seconds": None,   # 第一条带 SW 的失败行
        "error_sw": None,
        "busy_seen": False,      # 是否见过 DE=01（波形真的跑起来了）
        "last_elapsed": None,    # 最后一次「已用 N 秒」
        "de_polls": 0,
        "stages": set(),
    }
    for raw in text.splitlines():
        if not raw.strip():
            continue
        stamp = _clock(raw)
        match = LINE_RE.match(raw)
        if not match:
            continue
        level, stage, body = match.group(4), match.group(5) or "", match.group(6)
        if stage:
            info["stages"].add(stage)
        if "刷新指令已接受" in body and info["d4_seconds"] is None:
            info["d4_seconds"] = stamp
        if " D4:" in body and "SW=9000" in body and info["d4_seconds"] is None:
            info["d4_seconds"] = stamp
        if body.startswith("正在刷新"):
            info["busy_seen"] = True
        if stage == "刷新" and "DE" in body:
            info["de_polls"] += 1
        elapsed = ELAPSED_RE.search(body)
        if elapsed:
            info["last_elapsed"] = float(elapsed.group(1))
        if level == "ERROR" and info["error_sw"] is None:
            sw = SW_RE.search(body)
            if sw:
                info["error_sw"] = int(sw.group(1), 16)
                info["error_seconds"] = stamp
    return info


def verdict(info: dict) -> tuple[str, list[str]]:
    """68CA 的两种形态对应两种修法，别混着调。"""
    sw = info["error_sw"]
    notes: list[str] = []
    if sw is None:
        return ("未发现失败行：本次没有可诊断的错误。",
                ["确认这是 refresh 的日志，而不是 probe / send 的。"])

    if info["d4_seconds"] is not None and info["error_seconds"] is not None:
        gap = (info["error_seconds"] - info["d4_seconds"]) % 86400
        notes.append(f"D4 被接到报错：{gap:.0f} 秒（日志只有秒级精度）")
    else:
        gap = None

    if sw in LOW_VOLTAGE_SW:
        if info["busy_seen"] and (gap is None or gap >= 3):
            head = (f"波形中途掉压（SW={sw:04X} {LOW_VOLTAGE_SW[sw]}）："
                    "板子已经在驱动屏幕，但能量供不上后半程。")
            notes += [
                "优先降**峰值电流**：用 --passes two 只刷半幅，峰值减半。",
                "其次才是增强**场强**：大线圈 / 贴近 / 对位 / 线圈后加铁氧体。",
                "加大 --precharge-ms 对这个形态基本无效：电容充满也没用，掉的是负载下的电压。",
            ]
        else:
            head = (f"启动阶段欠压（SW={sw:04X} {LOW_VOLTAGE_SW[sw]}，全程没见到 BUSY=01）："
                    "波形没撑过起飞阶段就被判死。")
            notes += [
                "这是**耦合/场强**问题，不是时序问题：先换大线圈、对齐、压紧。",
                "--precharge-ms / --first-de-delay-ms / --de-hz 全部无效，别再调。",
                "先把 power_diag.py link 的成功率做到 100% 再谈刷新。",
            ]
        return head, notes

    if sw in TIMEOUT_SW:
        return (f"SW={sw:04X} {TIMEOUT_SW[sw]}：波形跑了但超时。",
                ["放宽 --timeout 到 120 秒再试一次，别重发 D4。"])

    return (f"SW={sw:04X} 不是低电压类错误。",
            ["按上游 STATUS 表查含义；供电参数与它无关。"])


def newest_run_log() -> Path | None:
    """取最新一次**进入过刷新阶段**的日志；probe / send 的日志会被跳过。"""
    candidates = []
    for item in LOGS.glob("*/run.log"):
        try:
            candidates.append((item.stat().st_mtime, item))
        except OSError:
            continue  # 个别历史目录权限异常，跳过
    if not candidates:
        return None
    candidates.sort(reverse=True)
    for _mtime, path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "[刷新]" in text:
            return path
    return candidates[0][1]


def render_timeline(info: dict, head: str, notes: list[str]) -> str:
    out = ["──── 刷新时间线 ────"]
    out.append(f"见到 BUSY=01（正在刷新）：{'是' if info['busy_seen'] else '否'}")
    out.append(f"DE 查询次数：{info['de_polls']}")
    if info["last_elapsed"] is not None:
        out.append(f"最后一条耗时：{info['last_elapsed']:.1f} 秒")
    out.append(f"失败状态字：{'—' if info['error_sw'] is None else f'{info["error_sw"]:04X}'}")
    out.append("")
    out.append("──── 判定 ────")
    out.append(head)
    for note in notes:
        out.append(f"· {note}")
    return "\n".join(out)


# --------------------------------------------------------------------------- link

def _failure_note(done: subprocess.CompletedProcess) -> str:
    """上游把失败详情写在 stderr，形如「未完成：没有找到驱动板。（TAG_NOT_FOUND）」。"""
    text = (done.stderr or "") + (done.stdout or "")
    match = re.search(r"[（(]([A-Z][A-Z0-9_]{2,})[）)]", text)
    if match:
        return match.group(1)
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line or "[日志]" in line:
            continue
        if "未完成" in line or "失败" in line:
            return line[:80]
    return f"EXIT_{done.returncode}"


def probe_once(python: Path, port: str, board: str, timeout: float) -> tuple[bool, float, dict | None, str]:
    cmd = [str(python), "-m", "ink_nfc_fmsc", "probe", "--port", port, "--board", board, "--json"]
    started = time.monotonic()
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - started, None, "PROBE_TIMEOUT"
    elapsed = time.monotonic() - started
    if done.returncode != 0:
        return False, elapsed, None, _failure_note(done)
    text = done.stdout.strip()
    start = text.find("{")
    if start < 0:
        return False, elapsed, None, "NO_JSON"
    try:
        return True, elapsed, json.loads(text[start:]), ""
    except json.JSONDecodeError:
        return False, elapsed, None, "BAD_JSON"


def summarize(results: list[tuple[bool, float, dict | None, str]]) -> dict:
    ok = [item for item in results if item[0]]
    times = sorted(item[1] for item in ok)
    wakes: dict[str, int] = {}
    for item in ok:
        key = (item[2] or {}).get("wake", "?")
        wakes[key] = wakes.get(key, 0) + 1
    failures: dict[str, int] = {}
    for item in results:
        if not item[0]:
            failures[item[3]] = failures.get(item[3], 0) + 1

    def pick(fraction: float) -> float:
        if not times:
            return float("nan")
        index = min(len(times) - 1, int(round(fraction * (len(times) - 1))))
        return times[index]

    return {
        "runs": len(results),
        "ok": len(ok),
        "rate": len(ok) / len(results) if results else 0.0,
        "min": times[0] if times else float("nan"),
        "median": statistics.median(times) if times else float("nan"),
        "p95": pick(0.95),
        "max": times[-1] if times else float("nan"),
        "wakes": wakes,
        "failures": failures,
    }


def link_advice(stats: dict) -> list[str]:
    tips = []
    rate = stats["rate"]
    if stats["ok"] == 0:
        return ["一次都没激活成功：先确认驱动板就在线圈上、串口没被别的程序占用。"]
    if rate < 0.7:
        tips.append(f"成功率 {rate:.0%}：余量太小，这个位置不要尝试刷新。")
        tips.append("把线圈与驱动板同轴对齐、间距压到最小，再跑一次对比。")
    elif rate < 1.0:
        tips.append(f"成功率 {rate:.0%}：偶发失败说明已经在临界点附近。")
        tips.append("刷新前把位置固定住（胶带/垫片），别用手扶。")
    else:
        tips.append("成功率 100%：这个位置的链路余量可以，值得尝试一次刷新。")
    if stats["ok"] and stats["p95"] > 2 * stats["median"] + 1:
        tips.append("p95 明显高于中位数：说明有几次很勉强，余量并不像成功率看起来那么足。")
    return tips


def render_link(stats: dict, label: str) -> str:
    out = [f"──── 链路余量 · {label} ────"]
    out.append(f"激活成功：{stats['ok']}/{stats['runs']}（{stats['rate']:.0%}）")
    if stats["ok"]:
        out.append(f"耗时：中位 {stats['median']:.2f}s · p95 {stats['p95']:.2f}s · "
                   f"区间 {stats['min']:.2f}–{stats['max']:.2f}s")
        wakes = "、".join(f"{k}×{v}" for k, v in sorted(stats["wakes"].items()))
        out.append(f"唤醒码：{wakes}")
    if stats["failures"]:
        fails = "、".join(f"{k}×{v}" for k, v in sorted(stats["failures"].items()))
        out.append(f"失败原因：{fails}")
    out.append("")
    out.append("──── 判读 ────")
    out.extend(f"· {tip}" for tip in link_advice(stats))
    return "\n".join(out)


def append_csv(label: str, stats: dict) -> Path:
    path = LOGS / "link-quality.csv"
    LOGS.mkdir(exist_ok=True)
    header = "time,label,runs,ok,rate,median_s,p95_s,max_s,wakes,failures\n"
    fresh = not path.exists()
    line = ",".join([
        datetime.now().isoformat(timespec="seconds"), _csv(label), str(stats["runs"]), str(stats["ok"]),
        f"{stats['rate']:.3f}", f"{stats['median']:.3f}", f"{stats['p95']:.3f}", f"{stats['max']:.3f}",
        _csv(json.dumps(stats["wakes"], ensure_ascii=False)), _csv(json.dumps(stats["failures"], ensure_ascii=False)),
    ]) + "\n"
    with path.open("a", encoding="utf-8") as stream:
        if fresh:
            stream.write(header)
        stream.write(line)
    return path


def _csv(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# --------------------------------------------------------------------------- 自检

SAMPLE_LOG = """\
[03:11:00] [INFO] [准备] 图像格式：JD79665AA-552x768-BWRY-A1
[03:11:06] [INFO]   [连接] APPID 20 1E A0 01; wake=7A / 7-bit; INF<=240
[03:11:07] [WARN]   [权限] D8: SW=6985 (使用条件不满足；D8 通常需要 PIN)
[03:11:12] [INFO] [刷新] 正在启动屏幕刷新，请保持线圈位置不动...
[03:11:14] [SUCCESS]   [刷新] existing-slot D4: SW=9000 (成功 / OK)
[03:11:14] [SUCCESS]   [刷新] 刷新指令已接受（D4=9000），等待屏幕完成...
[03:11:17] [ERROR]   [刷新] existing-slot DE: SW=68CA (低电压告警 / low voltage)
[03:11:17] [ERROR] [结果] 未完成：低电压告警 / low voltage（SW=68CA）
"""

BUSY_LOG = """\
[10:00:00] [INFO] [刷新] 正在启动屏幕刷新，请保持线圈位置不动...
[10:00:02] [SUCCESS]   [刷新] 刷新指令已接受（D4=9000），等待屏幕完成...
[10:00:03] [INFO]   [刷新] 正在刷新，已用 1.2 秒（DE=01，SW=9000）...
[10:00:09] [INFO]   [刷新] 正在刷新，已用 7.4 秒（DE=01，SW=9000）...
[10:00:12] [ERROR]   [刷新] existing-slot DE: SW=698A (低电压告警 / low voltage)
"""


def selftest() -> int:
    checks: list[tuple[str, bool]] = []

    info = parse_run_log(SAMPLE_LOG)
    checks.append(("抓到 D4 时刻", info["d4_seconds"] == 3 * 3600 + 11 * 60 + 14))
    checks.append(("抓到 68CA", info["error_sw"] == 0x68CA))
    checks.append(("未见到 BUSY", info["busy_seen"] is False))
    head, notes = verdict(info)
    checks.append(("判定为启动阶段欠压", "启动阶段欠压" in head))
    checks.append(("给出的建议是耦合优先", any("耦合" in n for n in notes)))
    checks.append(("时间线含秒数间隔", "3 秒" in " ".join(notes)))

    busy = parse_run_log(BUSY_LOG)
    checks.append(("见到 BUSY", busy["busy_seen"] is True))
    checks.append(("累计耗时被抓到", busy["last_elapsed"] == 7.4))
    head2, notes2 = verdict(busy)
    checks.append(("判定为中途掉压", "波形中途掉压" in head2))
    checks.append(("给出的建议是 two-pass", any("two" in n for n in notes2)))

    empty = parse_run_log("[01:00:00] [INFO] [准备] 什么都没发生\n")
    checks.append(("空日志不误判", verdict(empty)[0].startswith("未发现失败行")))

    stats = summarize([(True, 1.0, {"wake": "7A"}, ""), (True, 1.2, {"wake": "7A"}, ""),
                       (False, 8.0, None, "TAG_NOT_FOUND")])
    checks.append(("成功率计算", abs(stats["rate"] - 2 / 3) < 1e-9))
    checks.append(("唤醒码统计", stats["wakes"] == {"7A": 2}))
    checks.append(("失败分类", stats["failures"] == {"TAG_NOT_FOUND": 1}))
    checks.append(("中位数", abs(stats["median"] - 1.1) < 1e-9))
    checks.append(("有失败时不建议刷新", any("不要尝试刷新" in t or "临界点" in t for t in link_advice(stats))))
    perfect = summarize([(True, 1.0, {"wake": "7A"}, "")] * 4)
    checks.append(("全成功时给出可尝试", any("值得尝试" in t for t in link_advice(perfect))))

    with tempfile.TemporaryDirectory() as tmp:
        global LOGS
        saved, LOGS = LOGS, Path(tmp)
        try:
            path = append_csv("测试", perfect)
            text = path.read_text(encoding="utf-8")
            checks.append(("CSV 表头只写一次", text.count("time,label") == 1))
            checks.append(("CSV 含标签", "测试" in text))
            append_csv("第二次", perfect)
            checks.append(("CSV 追加不重复表头",
                           path.read_text(encoding="utf-8").count("time,label") == 1))
        finally:
            LOGS = saved

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        failed += 0 if ok else 1
    print(f"\n{len(checks) - failed}/{len(checks)} 通过")
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- 入口

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="68CA 供电诊断：链路余量 + 刷新时间线")
    sub = parser.add_subparsers(dest="command", required=True)

    link = sub.add_parser("link", help="重复只读 probe，统计激活成功率（耦合余量代理指标）")
    link.add_argument("--port", required=True, help="串口，如 COM14")
    link.add_argument("--repeat", type=int, default=20, help="重复次数，默认 20")
    link.add_argument("--board", choices=("auto", "huawei", "iphone"), default="huawei",
                      help="默认 huawei：直接走 7A/7D，省掉 26/52 的无效轮次")
    link.add_argument("--interval", type=float, default=0.5, help="两次之间的间隔秒数，默认 0.5")
    link.add_argument("--timeout", type=float, default=90.0, help="单次超时秒数，默认 90")
    link.add_argument("--label", default="未命名位置", help="给这个摆放位置起个名，写进 CSV")
    link.add_argument("--python", default=str(DEFAULT_PYTHON), help="装了 ink_nfc_fmsc 的 python")
    link.add_argument("--no-csv", action="store_true", help="不写 logs/link-quality.csv")

    timeline = sub.add_parser("timeline", help="解析 refresh 日志，判断 68CA 的形态")
    timeline.add_argument("--log", help="run.log 路径；省略则取 logs/ 下最新一次")
    timeline.add_argument("--json", action="store_true", help="输出原始解析结果")

    sub.add_parser("selftest", help="离线自检，不需要硬件")

    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()

    if args.command == "timeline":
        path = Path(args.log) if args.log else newest_run_log()
        if path is None or not path.exists():
            print("找不到运行日志：先跑一次 refresh，或显式给 --log。")
            return 2
        info = parse_run_log(path.read_text(encoding="utf-8", errors="replace"))
        if args.json:
            printable = {k: (v if not isinstance(v, set) else sorted(v)) for k, v in info.items()}
            print(json.dumps(printable, ensure_ascii=False, indent=2))
            return 0
        head, notes = verdict(info)
        print(f"日志：{path}")
        print(render_timeline(info, head, notes))
        return 0

    python = Path(args.python)
    if not python.exists():
        print(f"找不到 python：{python}\n用 --python 指向装了 ink_nfc_fmsc 的解释器。")
        return 2
    if args.repeat < 1:
        print("--repeat 至少为 1。")
        return 2

    results = []
    print(f"开始 {args.repeat} 次只读 probe（{args.board} / {args.port}），只寻卡、不写驱动、不刷屏。")
    for index in range(args.repeat):
        ok, elapsed, payload, note = probe_once(python, args.port, args.board, args.timeout)
        results.append((ok, elapsed, payload, note))
        mark = "OK " if ok else "失败"
        wake = f" wake={payload['wake']}" if ok and payload else ""
        detail = "" if ok else f" {note}"
        print(f"  {index + 1:2d}/{args.repeat} {mark} {elapsed:5.2f}s{wake}{detail}")
        if index + 1 < args.repeat:
            time.sleep(max(0.0, args.interval))

    stats = summarize(results)
    print()
    print(render_link(stats, args.label))
    if not args.no_csv:
        print(f"\n已追加到 {append_csv(args.label, stats)}")
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
