#!/usr/bin/env python3
"""在 PC 上把「刷新到底成不成」这件事测干净：遇 68CA 不停，按住射频场一直问。

背景（这是本脚本存在的唯一理由）：
    ink_nfc_fmsc 的 `Device.observe()` 里，DE 查询写的是
        state = self.exchange(f"{job.name} DE", DE, timeout=..., quiet=True, on_wtx=waiting)
    `quiet=True` 只压输出，`require_ok` 仍是默认 True ⇒ **第一次非 9000 就抛错**。
    所以之前那次 `DE: SW=68CA` 并不是「观察了 3 秒后失败」，而是「采到一个样本就放弃」。
    用这个样本去推断「启动阶段欠压」是不成立的 —— 那只是上游的异常处理策略。

    同芯片家族的另一个实现把 68C6 / 68CA 归为 **忙**（busy），见到就继续重试。
    本脚本照这个语义重跑一遍：D4 之后按住场，DE 每 ~1 秒问一次，68CA 只记不计错，
    一直问到超时或确实读到「空闲」。

用法（必须用装了 ink_nfc_fmsc 的那个 venv）：
    C:\\Users\\Chen\\.workbuddy\\binaries\\python\\envs\\inknfc\\Scripts\\python.exe ^
        tools\\refresh_hold.py --port COM14 --screen JD79665AA-552x768-BWRY-A1 --seconds 90 --yes

    # 只看状态、不重发 D4（上一次刷新可能还在跑/已跑完）
    ... tools\\refresh_hold.py --port COM14 --screen JD79665AA-552x768-BWRY-A1 --no-d4 --seconds 60 --yes

    # 离线验证判定逻辑，不需要硬件
    ... tools\\refresh_hold.py selftest

判读：
    REFRESH_DONE           见到忙/忙态之后连续三次空闲 ⇒ 驱动板报告刷新结束，请目视核对画面
    HELD_ALL_BUSY          全程都是 68CA/C6/CB，一次 9000 都没有 ⇒ 屏始终没结束刷新
    IDLE_NO_BUSY           一直空闲但从未见到忙 ⇒ 没有在这段时间里启动过刷新
    ABORTED_*              出现其它状态字，按真实错误处理（那才是需要查固件/接线的情况）
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# 同芯片家族的实现（Chanhee03/epd-nfc-samsung 的 FmscEpdProtocol）把这几项当「忙」处理。
BUSY_SW = {0x68CA, 0x68C6, 0x68CB}
SW_TEXT = {
    0x9000: "成功",
    0x6401: "目标无响应",
    0x6451: "未激活",
    0x6502: "不支持的指令",
    0x68CA: "忙 / 供电未就绪（上游当作低电压告警）",
    0x68C6: "屏幕复位失败或屏幕不存在",
    0x68CB: "刷新超时告警",
    0x6982: "权限不足",
    0x6985: "需要认证（PIN）",
}

IDLE_CONFIRM = 3          # 连续三次空闲即认为一轮结束（与 App 端 PanelProtocol 一致）
MIN_OBSERVATION = 20.0    # 从未见到忙时，至少观察这么久才认「空闲完成」


def sw_text(sw: int) -> str:
    return SW_TEXT.get(sw, "未知状态字")


def hold_poll(poll, now, pause, *, duration: float, first_delay: float, de_hz: float,
              min_observation: float = MIN_OBSERVATION, max_link_errors: int = 5, report=print) -> dict:
    """按住场反复查询 DE，把「忙」当成可以继续等，而不是错误。

    poll() -> (sw:int, data:bytes)；now() -> float 秒；pause(seconds)

    波形一旦启动，芯片可能连 DE 都短暂不答（射频场被负载拉塌），所以连续几次超时要容忍，
    超过 max_link_errors 次才认作链路真的断了。
    """
    started = now()
    deadline = started + max(0.0, duration)
    samples: list[tuple[float, int, bytes]] = []
    counts: dict[int, int] = {}
    outcome, detail = "TIMEOUT", ""
    busy_seen = False
    idle = 0
    link_errors = 0
    if first_delay > 0:
        pause(min(first_delay, max(0.0, deadline - now())))
    while now() < deadline:
        elapsed = now() - started
        try:
            sw, data = poll()
        except Exception as exc:
            link_errors += 1
            report(f"{elapsed:6.1f}s  无回包（第 {link_errors}/{max_link_errors} 次）：{exc}")
            if link_errors > max_link_errors:
                outcome, detail = "ABORTED_LINK", f"连续 {link_errors} 次无回包：{exc}"
                break
            pause(max(0.0, 1.0 / de_hz))
            continue
        link_errors = 0
        samples.append((elapsed, sw, data))
        counts[sw] = counts.get(sw, 0) + 1
        if sw in BUSY_SW:
            idle = 0
            report(f"{elapsed:6.1f}s  DE={sw:04X} {sw_text(sw)} —— 按「忙」继续等（第 {counts[sw]} 次）")
        elif sw == 0x9000:
            if data == b"\x01":
                busy_seen = True
                idle = 0
                report(f"{elapsed:6.1f}s  DE=9000 数据=01 屏幕报告忙，继续供能")
            elif data == b"\x00":
                idle += 1
                report(f"{elapsed:6.1f}s  DE=9000 数据=00 屏幕报告空闲（连续 {idle} 次）")
                if idle >= IDLE_CONFIRM and (busy_seen or now() - started >= min_observation):
                    outcome = "REFRESH_DONE" if busy_seen else "IDLE_NO_BUSY"
                    detail = ("见到忙→连续空闲，驱动板报告刷新结束" if busy_seen
                              else "全程未见到忙，不能据此确认刷新过")
                    break
            else:
                outcome, detail = "ABORTED_PAYLOAD", f"DE 数据字节异常：{data.hex().upper()}"
                report(f"{elapsed:6.1f}s  {detail}")
                break
        else:
            outcome, detail = "ABORTED_SW", f"DE 返回 {sw:04X} {sw_text(sw)}"
            report(f"{elapsed:6.1f}s  {detail}")
            break
        pause(max(0.0, 1.0 / de_hz - (now() - started - elapsed)))
    else:
        if samples and all(sw in BUSY_SW for _, sw, _ in samples):
            outcome, detail = "HELD_ALL_BUSY", "全程忙态，屏在一次也没有报告结束"
        else:
            outcome, detail = "TIMEOUT", "超过观察窗口仍未读到确定的结束状态"
    if not samples:
        outcome, detail = "NO_SAMPLES", detail or "一个 DE 样本都没拿到"
    return {"outcome": outcome, "detail": detail, "samples": samples,
            "counts": counts, "busy_seen": busy_seen,
            "elapsed": now() - started}


# --------------------------------------------------------------------------- 真机
DE = bytes.fromhex("F0DE000001")


def write_csv(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["run_at", "outcome", "detail", "elapsed_s", "sample_index", "t_s", "sw", "data"])
        run_at = time.strftime("%Y-%m-%d %H:%M:%S")
        for i, (t, sw, data) in enumerate(result["samples"]):
            writer.writerow([run_at, result["outcome"], result["detail"], f"{result['elapsed']:.2f}",
                             i, f"{t:.2f}", f"{sw:04X}", data.hex().upper()])


def real(args) -> int:
    try:
        import ink_nfc_fmsc.cli as cli  # noqa: F401  仅用于取上游 CLI 与 Device
        from ink_nfc_fmsc import cli as upcli
    except ImportError:
        print("找不到 ink_nfc_fmsc。请用装了它的 venv 运行：\n"
              "  C:\\Users\\Chen\\.workbuddy\\binaries\\python\\envs\\inknfc\\Scripts\\python.exe tools\\refresh_hold.py ...",
              file=sys.stderr)
        return 2
    global DE
    from ink_nfc_fmsc.protocol import DE as UP_DE
    DE = UP_DE

    argv = ["refresh"]
    # 上游把 ms / 超时类的参数声明成 int，这里统一取整，避免 "1000.0" 被 argparse 拒掉。
    for flag, value in (("--port", args.port), ("--screen", args.screen), ("--board", args.board),
                        ("--de-hz", args.de_hz), ("--de-timeout", args.de_timeout),
                        ("--d4-timeout", args.d4_timeout), ("--first-de-delay-ms", args.first_de_delay_ms),
                        ("--precharge-ms", args.precharge_ms), ("--pre-refresh-ms", args.pre_refresh_ms),
                        ("--rf-timeout-ms", args.rf_timeout_ms),
                        ("--timeout", args.seconds)):
        if value is None:
            continue
        text = f"{value:g}" if isinstance(value, float) else str(value)
        if flag.endswith("-ms") or flag == "--timeout":
            text = str(int(round(float(value))))
        argv += [flag, text]
    if args.yes:
        argv.append("--yes")
    if args.verbose:
        argv.append("--verbose")

    original = upcli.Device
    # 用类工厂包一层，把 observe() 换成 hold_poll；argparse 之外的东西一律不动。
    upcli.Device = lambda settings, logger, **kw: _hold_factory(original, settings, logger, args, **kw)
    try:
        return upcli.main(argv)
    finally:
        upcli.Device = original


def _hold_factory(base, settings, logger, args, **kw):
    device = base(settings, logger, **kw)
    device.args_no_d4 = args.no_d4

    def observe(job, *, timeout=None):
        duration = float(timeout if timeout is not None else job.timeout)
        device.begin_stage("刷新", "启动刷新并按住射频场，遇到忙态不放弃...")
        lost_reply = False
        if not args.no_d4:
            try:
                result = device.exchange(f"{job.name} D4", job.d4,
                                         timeout=min(settings.d4_timeout, duration), require_ok=False)
            except Exception as exc:
                # D4 是全场最耗电的一步：波形一起，回包常在电压塌陷时丢掉。
                # 「没有回包」不等于「没收到命令」—— 去问 DE 才知道板子到底动没动。
                lost_reply = True
                device.report(f"D4 没有回包（{exc}）；不能断定命令没被接受，继续观察 DE。", level="warning")
            else:
                device.report(f"D4 返回 SW={result.sw:04X} {sw_text(result.sw)}",
                              level="success" if result.sw == 0x9000 else "warning")
                if result.sw != 0x9000 and result.sw not in BUSY_SW:
                    result.require_ok("D4")
                if result.sw in BUSY_SW:
                    device.report("D4 也报忙：不重发（重发可能打断已开始的波形），直接进 DE 观察。", level="warning")
        device.report(f"观察窗口 {duration:g} 秒，DE 查询 {settings.de_hz:g} Hz。", detail=False)

        def poll():
            reply = device.exchange(f"{job.name} DE", DE, timeout=min(settings.de_timeout, 5.0),
                                    quiet=True, require_ok=False)
            return reply.sw, reply.data

        outcome = hold_poll(poll, device.clock, device.sleep, duration=duration,
                            first_delay=settings.first_de_delay_ms / 1000.0, de_hz=settings.de_hz,
                            report=lambda line: device.report(line, detail=False))
        outcome["d4_reply_lost"] = lost_reply
        if lost_reply and outcome["outcome"] in ("HELD_ALL_BUSY", "REFRESH_DONE"):
            outcome["detail"] += "；D4 回包虽丢，但板子随后报告过忙 ⇒ 刷新命令其实已被接受"
        device.log.event("refresh_hold", {k: v for k, v in outcome.items() if k != "samples"})
        device.report(f"{outcome['outcome']} · {outcome['detail']}",
                      level="success" if outcome["outcome"] == "REFRESH_DONE" else "warning", detail=False)
        write_csv(Path("logs") / "refresh-hold.csv", outcome)
        if outcome["outcome"] != "REFRESH_DONE":
            device.report("没有读到明确的「刷新结束」；请目视核对画面，并保留这次 DE 序列。", level="warning", detail=False)

    device.observe = observe
    return device


# --------------------------------------------------------------------------- 离线自检
def selftest() -> int:
    checks = []

    def scripted(seq, clock):
        it = iter(seq)

        def poll():
            step = next(it)
            clock[0] += 1.0
            if isinstance(step, Exception):
                raise step
            return step
        return poll

    # 1) 忙一段后转空闲 ⇒ REFRESH_DONE
    clock = [0.0]
    seq = [(0x68CA, b"")] * 4 + [(0x9000, b"\x01")] * 2 + [(0x9000, b"\x00")] * 3
    res = hold_poll(scripted(seq, clock), lambda: clock[0], lambda s: None,
                    duration=60, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("68CA 之后转忙再转空闲 ⇒ REFRESH_DONE", res["outcome"] == "REFRESH_DONE"))
    checks.append(("记录到 busy", res["busy_seen"] is True))

    # 2) 全程 68CA ⇒ HELD_ALL_BUSY
    clock = [0.0]
    res = hold_poll(scripted([(0x68CA, b"")] * 100, clock), lambda: clock[0], lambda s: None,
                    duration=6, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("全程 68CA ⇒ HELD_ALL_BUSY", res["outcome"] == "HELD_ALL_BUSY"))
    checks.append(("全程 68CA 不抛异常", len(res["samples"]) >= 4))

    # 3) 状态字 68C6 也当忙
    clock = [0.0]
    res = hold_poll(scripted([(0x68C6, b"")] * 100, clock), lambda: clock[0], lambda s: None,
                    duration=4, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("68C6 归为忙", res["outcome"] == "HELD_ALL_BUSY"))

    # 4) 真错误（6502）要中止
    clock = [0.0]
    res = hold_poll(scripted([(0x6502, b"")] * 5, clock), lambda: clock[0], lambda s: None,
                    duration=10, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("6502 立即 ABORTED_SW", res["outcome"] == "ABORTED_SW" and len(res["samples"]) == 1))

    # 5) 一直空闲但没忙 ⇒ IDLE_NO_BUSY（且要过最小观察窗口）
    clock = [0.0]
    res = hold_poll(scripted([(0x9000, b"\x00")] * 100, clock), lambda: clock[0], lambda s: None,
                    duration=40, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("空闲无忙 ⇒ IDLE_NO_BUSY", res["outcome"] == "IDLE_NO_BUSY"))
    checks.append(("IDLE 判定不早于最小观察窗口", res["elapsed"] >= MIN_OBSERVATION))

    # 6) 链路异常要连续超过阈值才中止（单次超时可能只是波形拉塌了场）
    clock = [0.0]
    seq_tol = ([(0x9000, b"\x01"), RuntimeError("no tag")] * 2) + [(0x9000, b"\x01")] * 50
    res = hold_poll(scripted(seq_tol, clock),
                    lambda: clock[0], lambda s: None, duration=6, first_delay=0, de_hz=1000,
                    report=lambda *_: None)
    checks.append(("单次无回包不中止", res["outcome"] != "ABORTED_LINK"))
    clock = [0.0]
    seq6 = []
    for _ in range(8):
        seq6 += [(0x9000, b"\x01"), RuntimeError("no tag")]
    res = hold_poll(scripted(seq6, clock), lambda: clock[0], lambda s: None,
                    duration=60, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("连续无回包超过阈值 ⇒ ABORTED_LINK", res["outcome"] == "ABORTED_LINK"))

    # 7) 数据字节异常
    clock = [0.0]
    res = hold_poll(scripted([(0x9000, b"\x07")] * 3, clock), lambda: clock[0], lambda s: None,
                    duration=30, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("DE 数据字节异常要中止", res["outcome"] == "ABORTED_PAYLOAD"))

    # 8) 一个样本都没有
    clock = [0.0]
    res = hold_poll(scripted([], clock), lambda: clock[0], lambda s: None,
                    duration=0, first_delay=0, de_hz=1000, report=lambda *_: None)
    checks.append(("窗口为 0 ⇒ NO_SAMPLES", res["outcome"] == "NO_SAMPLES"))

    # 9) 首次延时会被裁剪到观察窗口内
    clock = [0.0]
    res = hold_poll(scripted([(0x68CA, b"")] * 50, clock), lambda: clock[0], lambda s: None,
                    duration=2, first_delay=99, de_hz=1000, report=lambda *_: None)
    checks.append(("first_delay 不会越过 deadline", res["elapsed"] <= 2.5))

    ok = sum(1 for _, passed in checks if passed)
    for name, passed in checks:
        print(("  PASS  " if passed else "  FAIL  ") + name)
    print(f"selftest: {ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="按住射频场反复查询 DE，把 68CA 当忙态处理（PC 侧验证刷新）")
    p.add_argument("command", nargs="?", default="run", choices=["run", "selftest"])
    p.add_argument("--port")
    p.add_argument("--screen", default="JD79665AA-552x768-BWRY-A1")
    p.add_argument("--board", default="huawei")
    p.add_argument("--seconds", type=float, default=90.0, help="DE 观察窗口，默认 90 秒")
    p.add_argument("--de-hz", type=float, default=1.0)
    p.add_argument("--de-timeout", type=float, default=10.0)
    p.add_argument("--d4-timeout", type=float, default=15.0)
    p.add_argument("--first-de-delay-ms", type=float, default=1000.0)
    p.add_argument("--rf-timeout-ms", type=float, default=3000.0,
                   help="等待驱动板回包的最短时限；波形刚启动时回包容易被丢掉，默认放宽到 3 秒")
    p.add_argument("--precharge-ms", type=float, default=1000.0)
    p.add_argument("--pre-refresh-ms", type=float, default=1000.0)
    p.add_argument("--no-d4", action="store_true", help="只观察，不重发 D4")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "selftest":
        return selftest()
    if not args.port:
        print("需要 --port（PN532 通常 COM14）；不确定就先跑 ink_nfc_fmsc ports。", file=sys.stderr)
        return 2
    return real(args)


if __name__ == "__main__":
    raise SystemExit(main())
