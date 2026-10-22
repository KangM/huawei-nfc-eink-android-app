#!/usr/bin/env python3
"""Chameleon Ultra read-only probe for the Huawei 552x768 four-colour NFC e-ink panel.

Diagnoses "is the panel alive / does it answer standard ISO14443-A" without going
through the phone's NXP stack or Q-Poll, so the phone and the panel can be told apart.

Nothing here writes an image or triggers a refresh. The only commands sent are D1
(identity/profile), optionally D8 (access query), plus whatever is passed explicitly
with --apdu.

Usage
  python tools/chameleon_probe.py selftest                 # offline checks, no hardware
  python tools/chameleon_probe.py info    --port COM9      # connect, firmware, force reader mode
  python tools/chameleon_probe.py scan    --port COM9      # standard polling only, field released
  python tools/chameleon_probe.py probe   --port COM9      # field kept on: activate + RATS + D1
  python tools/chameleon_probe.py survey  --port COM9 --repeat 20         # hit rate at one spot
  python tools/chameleon_probe.py survey  --port COM9 --repeat 10 --deep  # also complete RATS + D1
  python tools/chameleon_probe.py probe   --port COM9 --with-d8
  python tools/chameleon_probe.py probe   --port COM9 --apdu F0DE000001

Output lines are "CP|KIND|message", mirroring the App log kinds (TAG / META / ISO_DEP /
APDU / RESULT / ERROR) so both transcripts can be read side by side.

Licence and notices
  Panel command set and factory scan layout were learned from the same two public
  reverse-engineering projects credited in THIRD_PARTY_NOTICES.md:
    - kur0neko, huawei-epd-pn532 (PolyForm Noncommercial 1.0.0)
    - solar2ain, ink-nfc-fmsc-by-pn532 (GPL-3.0-or-later)
  No code from either project is used here. Same noncommercial restriction as the app.
  The Chameleon Ultra wire protocol was reimplemented from its documented frame layout;
  the upstream client itself is GPL-3.0 and is neither vendored nor imported.
"""

import argparse
import binascii
import struct
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

# ---------------------------------------------------------------- device protocol

SOF = 0x11

CMD_GET_APP_VERSION = 1000
CMD_CHANGE_DEVICE_MODE = 1001
CMD_GET_DEVICE_MODE = 1002
CMD_GET_DEVICE_MODEL = 1033
CMD_HF14A_SCAN = 2000
CMD_HF14A_RAW = 2010
CMD_HF14A_SCAN_KEEP = 2016  # recent firmware; select + RATS without dropping the field

ST_HF_TAG_OK = 0x00
ST_HF_TAG_NO = 0x01
ST_HF_ERR_STAT = 0x02
ST_HF_ERR_CRC = 0x03
ST_HF_COLLISION = 0x04
ST_HF_ERR_BCC = 0x05
ST_HF_ERR_ATS = 0x08
ST_PAR_ERR = 0x60
ST_DEVICE_MODE_ERROR = 0x66
ST_INVALID_CMD = 0x67
ST_SUCCESS = 0x68
ST_NOT_IMPLEMENTED = 0x69


def status_text(code):
    return {
        ST_HF_TAG_OK: "标签应答成功",
        ST_HF_TAG_NO: "未找到标签或标签离场",
        ST_HF_ERR_STAT: "标签通信状态异常",
        ST_HF_ERR_CRC: "标签数据 CRC 错误",
        ST_HF_COLLISION: "防碰撞冲突",
        ST_HF_ERR_BCC: "UID BCC 错误",
        ST_HF_ERR_ATS: "ATS 缺失或过长（卡不应答 RATS）",
        ST_PAR_ERR: "参数错误",
        ST_DEVICE_MODE_ERROR: "设备当前模式不允许该操作",
        ST_INVALID_CMD: "固件不支持该命令",
        ST_SUCCESS: "成功",
        ST_NOT_IMPLEMENTED: "固件未实现",
    }.get(code, "未知状态 0x%02X" % code)


def lrc_calc(data):
    total = 0
    for byte in bytearray(data):
        total = (total + byte) & 0xFF
    return (0x100 - total) & 0xFF


def build_frame(cmd, payload=b"", status=0):
    frame = bytearray(struct.pack("!BBHHHB%dsB" % len(payload), SOF, 0, cmd, status, len(payload), 0, payload, 0))
    frame[1] = lrc_calc(frame[:1])
    frame[8] = lrc_calc(frame[:8])
    frame[9 + len(payload)] = lrc_calc(frame[: 9 + len(payload)])
    return bytes(frame)


class FrameReader(object):
    """Incremental parser for a byte stream arriving in arbitrary chunks."""

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, chunk):
        frames = []
        self._buffer.extend(chunk)
        while True:
            if len(self._buffer) < 2:
                break
            if self._buffer[0] != SOF:
                del self._buffer[0]
                continue
            if self._buffer[1] != lrc_calc(self._buffer[:1]):
                del self._buffer[:2]
                continue
            if len(self._buffer) < 9:
                break
            if self._buffer[8] != lrc_calc(self._buffer[:8]):
                del self._buffer[:9]
                continue
            _, _, cmd, status, length = struct.unpack("!BBHHH", self._buffer[:8])
            total = 10 + length
            if len(self._buffer) < total:
                break
            if self._buffer[total - 1] != lrc_calc(self._buffer[: total - 1]):
                del self._buffer[:total]
                continue
            frames.append((cmd, status, bytes(self._buffer[9 : 9 + length])))
            del self._buffer[:total]
        return frames


class Timeout(Exception):
    pass


class Chameleon(object):
    def __init__(self, port, timeout=0.25):
        self.port = port
        self.transport = None
        self.timeout = timeout
        self.reader = FrameReader()

    def open(self):
        self.transport = serial.Serial(port=self.port, baudrate=115200, timeout=self.timeout)
        time.sleep(0.05)
        try:
            self.transport.reset_input_buffer()
        except Exception:
            pass
        return self

    def close(self):
        if self.transport is not None:
            try:
                self.transport.close()
            finally:
                self.transport = None

    def exchange(self, cmd, payload=b"", timeout=None):
        if self.transport is None:
            raise RuntimeError("串口未打开")
        deadline = time.time() + (timeout if timeout is not None else max(2.0, self.timeout * 8))
        self.transport.write(build_frame(cmd, payload))
        while time.time() < deadline:
            waiting = max(1, self.transport.in_waiting)
            for got_cmd, status, data in self.reader.feed(self.transport.read(waiting)):
                if got_cmd == cmd:
                    return status, data
        raise Timeout("命令 %d 等待响应超时" % cmd)


def connect(port):
    """Open the first port that answers GET_APP_VERSION."""
    if serial is None:
        raise RuntimeError("缺少 pyserial，请先执行 pip install pyserial")
    if port:
        return Chameleon(port).open(), port
    errors = []
    for candidate in [entry.device for entry in serial.tools.list_ports.comports()]:
        device = Chameleon(candidate)
        try:
            device.open()
            status, _ = device.exchange(CMD_GET_APP_VERSION, timeout=2.0)
            if status == ST_SUCCESS:
                return device, candidate
            device.close()
        except Exception as error:
            errors.append("%s: %s" % (candidate, error))
            try:
                device.close()
            except Exception:
                pass
    raise RuntimeError("没有找到可用的 Chameleon 串口；尝试结果：%s" % ("；".join(errors) or "无串口"))


# ------------------------------------------------------------- ISO14443-A helpers


def crc_a(data):
    value = 0x6363
    for byte in bytearray(data):
        byte ^= value & 0xFF
        byte ^= (byte << 4) & 0xFF
        value = (value >> 8) ^ (byte << 8) ^ (byte << 3) ^ (byte >> 4)
        value &= 0xFFFF
    return value


def crc_bytes(data):
    value = crc_a(data)
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def strip_trailing_crc(data):
    """Some firmware variants leave the ISO14443-A CRC attached to the response."""
    if len(data) >= 2 and crc_a(data[:-2]) == struct.unpack("<H", data[-2:])[0]:
        return data[:-2]
    return data


# ---------------------------------------------------------------- panel protocol

D1_IDENTITY = bytes.fromhex("00D1000000")
D8_ACCESS = bytes.fromhex("F0D8000005000000000E")

# Standard ISO14443-A uses 7-bit REQA(26)/WUPA(52). Upstream protocol docs for the
# FMSC / Huawei driver boards state that some boards are reached with a non-standard
# 7-bit short frame pair 7A / 7D instead. Trying all four removes a false negative
# that would otherwise read exactly like "the panel coil is dead".
WAKE_FRAMES = ((0x26, "REQA 26"), (0x52, "WUPA 52"), (0x7A, "特殊帧 7A"), (0x7D, "特殊帧 7D"))

GEOMETRY = bytes.fromhex("F0C72006000228")
SCAN_BOTTOM_UP = bytes.fromhex("00120030FFFFFF")
SCAN_TOP_DOWN = bytes.fromhex("01120030FFFFFF")

FIELD_LENGTHS = {0xA0: 7, 0xA1: 7, 0xB1: 1, 0xB2: 1, 0xC0: 4, 0xD1: 7}
FIELD_EXPECTED = {0xA0: GEOMETRY, 0xA1: SCAN_BOTTOM_UP, 0xC0: bytes.fromhex("201EA001"), 0xB1: b"\x02"}
FIELD_NAMES = {
    0xA0: "几何/型号",
    0xA1: "扫描配置",
    0xB1: "B1",
    0xB2: "B2",
    0xC0: "C0",
    0xC1: "标签 UID",
    0xD1: "D1",
}

SW_MEANING = {
    0x6401: "已知未解决分支：D1 返回 6401（见 README v2.10-test1）",
    0x6502: "低电压或供能不足",
    0x68CA: "低电压或供能不足",
    0x698A: "低电压或供能不足",
    0x68CB: "屏幕刷新超时",
    0x6992: "数据解压失败",
    0x6993: "解压后的数据长度错误",
    0x6995: "图像分包序号错误",
    0x6982: "安全状态不满足",
    0x6985: "访问条件不满足：这类驱动板通常需要先验证一次已知默认 PIN，再用 D8 复查",
    0x6451: "配置相关错误：核对屏幕型号与已存驱动配置，不要盲目写入 DA / DB",
    0x6986: "屏幕复位失败或屏幕不存在",
    0x68C6: "屏幕复位失败或屏幕不存在",
}


def sw_text(sw):
    if sw == 0x9000:
        return "成功"
    return "0x%04X %s" % (sw, SW_MEANING.get(sw, "设备返回错误"))


def split_sw(response):
    if len(response) < 2:
        raise ValueError("回包不足两个字节，无法解析状态字")
    return response[:-2], (response[-2] << 8) | response[-1]


def parse_d1(data):
    """Parse the D1 response body into TLV fields; raises on structural errors."""
    fields = {}
    offset = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise ValueError("D1 TLV 头截断")
        tag = data[offset]
        length = data[offset + 1]
        offset += 2
        if tag in fields or offset + length > len(data):
            raise ValueError("D1 TLV 重复或截断")
        fields[tag] = data[offset : offset + length]
        offset += length
    return fields


def check_profile(fields):
    """Same factory-profile gate as PanelProtocol.parseInfo in the app."""
    mismatches = []
    for tag, length in sorted(FIELD_LENGTHS.items()):
        actual = fields.get(tag)
        if actual is None or len(actual) != length:
            shown = "缺失" if actual is None else "%d (%s)" % (len(actual), actual.hex().upper())
            mismatches.append("%02X 长度：期望=%d，实际=%s" % (tag, length, shown))
    for tag, expected in sorted(FIELD_EXPECTED.items()):
        actual = fields.get(tag)
        if actual is None or not actual:
            continue
        if tag == 0xA1 and actual == SCAN_TOP_DOWN:
            continue
        if actual != expected:
            mismatches.append("%02X 内容：期望=%s，实际=%s" % (tag, expected.hex().upper(), actual.hex().upper()))
    first = fields.get(0xD1)
    if first is not None and first[0] != 1:
        mismatches.append("D1 首字节：期望=01，实际=%02X" % first[0])
    return mismatches


def describe_fields(fields):
    return "；".join(
        "%02X(%s)=%s" % (tag, FIELD_NAMES.get(tag, "未知"), value.hex().upper())
        for tag, value in sorted(fields.items())
    )


# -------------------------------------------------------- ISO14443-4 T=CL blocks


def classify_block(pcb):
    if not pcb & 0x80:
        return "I"
    if not pcb & 0x40:
        return "R"
    return "S"


def is_wtx(pcb):
    return classify_block(pcb) == "S" and bool(pcb & 0x30)


def iblock(apdu, block_number):
    return bytes([0x02 | (block_number & 0x01)]) + apdu


# ------------------------------------------------------------------- probe logic


def emit(kind, message):
    print("CP|%s|%s" % (kind, str(message).replace("\n", " ")))
    sys.stdout.flush()


def raw_options(activate, wait, append, keep, check):
    value = 0
    if activate:
        value |= 1 << 7
    if wait:
        value |= 1 << 6
    if append:
        value |= 1 << 5
    if keep:
        value |= 1 << 3
    if check:
        value |= 1 << 2
    return bytes([value])


def send_raw(device, payload, bitlen=None, wait=True, append_crc=False, check_crc=True,
             activate=True, keep=True, timeout_ms=1000):
    if bitlen is None:
        bitlen = len(payload) * 8
    data = raw_options(activate, wait, append_crc, keep, check_crc)
    data += struct.pack("!HH%ds" % len(payload), timeout_ms, bitlen, payload)
    status, response = device.exchange(CMD_HF14A_RAW, data, timeout=(timeout_ms / 1000.0) + 2.0)
    if status != ST_HF_TAG_OK:
        emit("ERROR", "raw 返回 status=0x%02X（%s）" % (status, status_text(status)))
        return None
    return strip_trailing_crc(bytes(response))


def teardown(device):
    try:
        options = raw_options(False, False, False, False, False)
        device.exchange(CMD_HF14A_RAW, options + struct.pack("!HH0s", 100, 0), timeout=2.0)
        emit("FIELD_OFF", "已关闭射频场")
    except Exception as error:
        emit("CLEANUP_ERROR", "关闭射频场失败：" + str(error))


def unpack_scan(data):
    """Parse the firmware's uidlen|uid|atqa|sak|atslen|ats list into one dict."""
    offset = 0
    uidlen = data[offset]
    offset += 1
    uid, atqa, sak, atslen = struct.unpack_from("!%ds2s1sB" % uidlen, data, offset)
    offset += struct.calcsize("!%ds2s1sB" % uidlen)
    return {"uid": uid, "atqa": atqa, "sak": sak, "ats": data[offset : offset + atslen]}


def try_scan_keep(device, timeout_ms):
    """Preferred path: select + RATS with the field left on, done by the firmware."""
    try:
        status, data = device.exchange(CMD_HF14A_SCAN_KEEP, timeout=(timeout_ms / 1000.0) + 2.0)
    except Timeout as error:
        return None, "SCAN_KEEP 超时（%s）" % error
    if status in (ST_INVALID_CMD, ST_NOT_IMPLEMENTED):
        return None, "固件不支持 HF14A_SCAN_KEEP"
    if status != ST_HF_TAG_OK:
        return None, "SCAN_KEEP status=0x%02X（%s）" % (status, status_text(status))
    if not data:
        return None, "SCAN_KEEP 返回空列表"
    return unpack_scan(data), None


def manual_activate(device, timeout_ms=1000):
    """Full standard ISO14443-A activation with the carrier never dropped.

    A normal polling loop cuts the field between technologies; an energy-harvesting
    panel may never get through that. This sequence keeps it on from REQA to RATS.
    """
    atqa = None
    for value, name in WAKE_FRAMES:
        atqa = send_raw(device, bytes([value]), bitlen=7, activate=True, keep=True,
                        append_crc=False, check_crc=False, timeout_ms=timeout_ms)
        if atqa:
            emit("ACTIVATE", "%s 有应答（7 位短帧）" % name)
            break
    if not atqa:
        emit("ACTIVATE", "26 / 52 / 7A / 7D 均无应答")
        return None
    emit("META", "ATQA=" + atqa.hex().upper())

    uid = b""
    sak = b"\x00"
    for index, sel in enumerate([0x93, 0x95, 0x97]):
        cascade = send_raw(device, bytes([sel, 0x20]), keep=True, append_crc=False,
                           check_crc=False, timeout_ms=timeout_ms)
        if not cascade or len(cascade) < 5:
            emit("ACTIVATE", "防碰撞 CL%d 无有效应答（%s）" % (index + 1, cascade.hex().upper() if cascade else "无"))
            return None
        bcc = 0
        for byte in cascade[:4]:
            bcc ^= byte
        if bcc != cascade[4]:
            emit("ACTIVATE", "防碰撞 CL%d BCC 不匹配：%s" % (index + 1, cascade.hex().upper()))
            return None
        uid += cascade[:4]
        selected = send_raw(device, bytes([sel, 0x70]) + cascade[:5], keep=True, append_crc=True,
                            check_crc=True, timeout_ms=timeout_ms)
        if not selected:
            emit("ACTIVATE", "SELECT CL%d 无应答" % (index + 1))
            return None
        sak = selected
        if not sak[0] & 0x04:
            break

    emit("META", "SAK=" + sak.hex().upper())
    if not sak[0] & 0x20:
        emit("ACTIVATE", "SAK 未声明 ISO14443-4 支持；后续 RATS / D1 可能不成功")
    for fsdi in (2, 8, 5):
        ats = send_raw(device, bytes([0xE0, fsdi << 4]), keep=True, append_crc=True,
                       check_crc=True, timeout_ms=timeout_ms)
        if ats:
            emit("ISO_DEP", "ATS=" + ats.hex().upper() + "；RATS FSDI=" + str(fsdi) + "；场保持开启")
            return {"uid": uid, "atqa": atqa, "sak": sak, "ats": ats}
    emit("ACTIVATE", "RATS 无应答：标签不支持或未进入 ISO14443-4")
    return None


def exchange_apdu(device, apdu, block_number, timeout_ms=1000, max_wtx=32):
    """Send one APDU over T=CL and return the response body, or None."""
    payload = iblock(apdu, block_number)
    for _ in range(max_wtx + 1):
        response = send_raw(device, payload, keep=True, append_crc=True,
                            check_crc=True, timeout_ms=timeout_ms)
        if response is None:
            return None
        if not response:
            emit("ERROR", "收到空帧")
            return None
        pcb = response[0]
        kind = classify_block(pcb)
        if kind == "I":
            return response[1:]
        if kind == "R":
            if pcb & 0x10:
                emit("ERROR", "收到 R(NAK)，已停止")
                return None
            payload = bytes([0xA2 | (block_number & 0x01)])
            continue
        if is_wtx(pcb):
            wtxm = response[1] if len(response) > 1 else 1
            emit("WTX", "屏请求等待扩展 WTXM=%d；已回应并保持供能" % wtxm)
            payload = bytes([pcb, wtxm])
            continue
        emit("ERROR", "收到未预期的 S 块 PCB=%02X" % pcb)
        return None
    emit("ERROR", "WTX 次数超过上限，可能供能或状态异常")
    return None


def run_scan(device, timeout_ms):
    status, data = device.exchange(CMD_HF14A_SCAN, timeout=(timeout_ms / 1000.0) + 2.0)
    if status != ST_HF_TAG_OK:
        emit("RESULT", "hf14a scan 失败：status=0x%02X（%s）" % (status, status_text(status)))
        return False
    if not data:
        emit("RESULT", "扫到空列表")
        return False
    tag = unpack_scan(data)
    emit("TAG", "收到标签 UID=" + tag["uid"].hex().upper())
    emit("META", "ATQA=" + tag["atqa"].hex().upper() + "；SAK=" + tag["sak"].hex().upper())
    emit("ISO_DEP", "ATS=" + tag["ats"].hex().upper())
    return True


def run_probe(device, args):
    tag, reason = try_scan_keep(device, args.timeout_ms)
    if reason:
        emit("INFO", reason + "；改用手动激活序列")
    if tag is None:
        tag = manual_activate(device, args.timeout_ms)
    if tag is None:
        emit("RESULT", "未能建立 ISO14443-4 会话")
        emit("HINT", "不能据此判定屏坏：先用一张已知正常的卡重复本命令校验读卡器，再更换贴合位置重试")
        teardown(device)
        return 1
    if reason is None:
        emit("TAG", "收到标签 UID=" + tag["uid"].hex().upper())
        emit("META", "ATQA=" + tag["atqa"].hex().upper() + "；SAK=" + tag["sak"].hex().upper())
        emit("ISO_DEP", "ATS=" + tag["ats"].hex().upper() + "；场保持开启")

    emit("STAGE", "checking|已建立 T=CL 会话，发送 D1 读取屏幕规格")
    body = exchange_apdu(device, D1_IDENTITY, 0, timeout_ms=args.timeout_ms)
    if body is None:
        emit("RESULT", "D1 无有效回包；保持贴合并重试，或增大 --timeout-ms")
        teardown(device)
        return 1
    _, sw = split_sw(body)
    emit("APDU", "D1：SW=" + sw_text(sw))
    if sw != 0x9000:
        emit("RESULT", "屏幕返回非成功状态字，已停止；未写入任何数据")
        teardown(device)
        return 1

    fields = parse_d1(body)
    emit("IDENTITY", describe_fields(fields))
    mismatches = check_profile(fields)
    if mismatches:
        for line in mismatches:
            emit("MISMATCH", line)
        emit("RESULT", "目标配置与已验证的原厂配置不匹配；未写入任何数据")
    else:
        emit("RESULT", "D1 规格与原厂配置校验通过；线圈与 ISO-DEP 通信正常，问题在手机侧轮询")

    if args.with_d8:
        access = exchange_apdu(device, D8_ACCESS, 1, timeout_ms=args.timeout_ms)
        if access is not None:
            _, sw = split_sw(access)
            emit("APDU", "D8 权限查询：SW=" + sw_text(sw))
    if args.apdu:
        try:
            chosen = bytes.fromhex(args.apdu)
        except binascii.Error:
            emit("ERROR", "--apdu 不是合法十六进制")
            chosen = None
        if chosen:
            response = exchange_apdu(device, chosen, 2, timeout_ms=args.timeout_ms)
            if response is not None:
                _, sw = split_sw(response)
                emit("APDU", "%s：SW=%s；数据=%s" % (args.apdu.upper(), sw_text(sw), response.hex().upper()))

    teardown(device)
    return 0


def field_off(device):
    """Turn the carrier off without logging, so a survey round starts from cold."""
    try:
        options = raw_options(False, False, False, False, False)
        device.exchange(CMD_HF14A_RAW, options + struct.pack("!HH0s", 100, 0), timeout=2.0)
    except Exception:
        pass


def run_survey(device, args):
    """Repeat the cheapest activation round so coupling can be compared as a number.

    Moving the reader across the panel, or reshaping the panel coil, changes coupling
    and nothing else. Counting answers per spot turns "feels better" into a hit rate,
    which is the only way to judge whether a coil change helped or hurt.
    """
    rounds = max(1, args.repeat)
    answers = 0
    sessions = 0
    started = time.time()
    for index in range(rounds):
        field_off(device)
        atqa = None
        for value, name in WAKE_FRAMES:
            atqa = send_raw(device, bytes([value]), bitlen=7, activate=True, keep=True,
                            append_crc=False, check_crc=False, timeout_ms=args.timeout_ms)
            if atqa:
                emit("SURVEY", "第 %d/%d 轮：%s 有应答 ATQA=%s" % (index + 1, rounds, name, atqa.hex().upper()))
                break
        if not atqa:
            emit("SURVEY", "第 %d/%d 轮：26 / 52 / 7A / 7D 均无应答" % (index + 1, rounds))
            continue
        answers += 1
        field_off(device)
        tag = manual_activate(device, args.timeout_ms)
        if tag is None:
            emit("SURVEY", "第 %d/%d 轮：有 ATQA，但未进入 ISO14443-4" % (index + 1, rounds))
            teardown(device)
            continue
        body = exchange_apdu(device, D1_IDENTITY, 0, timeout_ms=args.timeout_ms)
        if body is None:
            emit("SURVEY", "第 %d/%d 轮：有 ATS，但 D1 无回包" % (index + 1, rounds))
        else:
            _, sw = split_sw(body)
            if sw == 0x9000:
                sessions += 1
            emit("SURVEY", "第 %d/%d 轮：D1 SW=%s" % (index + 1, rounds, sw_text(sw)))
        teardown(device)

    elapsed = time.time() - started
    percent = answers * 100.0 / rounds
    emit("SURVEY_RESULT", "轮询应答 %d/%d（%.0f%%）；用时 %.1f s" % (answers, rounds, percent, elapsed))
    if args.deep:
        emit("SURVEY_RESULT", "D1 成功 %d/%d（%.0f%%）" % (sessions, rounds, sessions * 100.0 / rounds))
    if answers == 0:
        emit("HINT", "零应答不能判定屏坏：先用一张已知正常的卡在同一位置跑同一条命令，读卡器正常才算有效对照")
    emit("HINT", "同一位置重跑可看稳定性；换位置或改线圈形状后重跑，直接比较百分比")
    teardown(device)
    return 0 if answers else 1


# --------------------------------------------------------------------- selftest


def selftest():
    failures = []

    def check(name, condition, extra=""):
        if condition:
            print("ok   %s" % name)
        else:
            print("FAIL %s %s" % (name, extra))
            failures.append(name)

    check("LRC 补码", lrc_calc(bytes([SOF])) == 0xEF, hex(lrc_calc(bytes([SOF]))))

    frame = build_frame(CMD_HF14A_RAW, bytes([0x02, 0x00]))
    parsed = FrameReader().feed(frame)
    check("帧往返", len(parsed) == 1 and parsed[0][0] == CMD_HF14A_RAW and parsed[0][2] == bytes([0x02, 0x00]))
    check("帧长度", len(frame) == 12, str(len(frame)))
    noisy = FrameReader().feed(b"\x00\x00" + frame + b"\xff")
    check("帧前后噪点容错", len(noisy) == 1 and noisy[0][0] == CMD_HF14A_RAW)
    stream = FrameReader()
    check("分片输入不误判", stream.feed(frame[:6]) == [])
    check("分包拼接", stream.feed(frame[6:]) == [(CMD_HF14A_RAW, 0, bytes([0x02, 0x00]))])

    sample = bytes.fromhex("0012")
    with_crc = sample + crc_bytes(sample)
    check("CRC_A 自校验", crc_a(with_crc) == 0)
    check("CRC_A 非平凡", crc_a(sample) != 0 and len(crc_bytes(sample)) == 2)
    check("尾部 CRC 剥离", strip_trailing_crc(with_crc) == sample)
    check("无 CRC 时保持原样", strip_trailing_crc(sample) == sample)

    check("I 块构造", iblock(D1_IDENTITY, 0) == b"\x02" + D1_IDENTITY)
    check("I 块翻转", iblock(D1_IDENTITY, 1) == b"\x03" + D1_IDENTITY)
    check("块类型识别", [classify_block(x) for x in (0x02, 0x03, 0x0A, 0xA2, 0xB2, 0xC2, 0xF2)]
          == ["I", "I", "I", "R", "R", "S", "S"])
    check("WTX 判定", is_wtx(0xF2) and is_wtx(0xF3) and not is_wtx(0xC2) and not is_wtx(0x02))
    check("选项位编码", raw_options(True, False, True, False, True) == bytes([0xA4]))
    check("选项位：保持场", raw_options(True, True, True, True, True) == bytes([0xEC]))

    response = bytes.fromhex("A007") + GEOMETRY
    response += bytes.fromhex("A107") + SCAN_BOTTOM_UP
    response += bytes.fromhex("B101") + b"\x02"
    response += bytes.fromhex("B201") + b"\x00"
    response += bytes.fromhex("C004") + bytes.fromhex("201EA001")
    response += bytes.fromhex("C104") + bytes.fromhex("01020304")
    response += bytes.fromhex("D107") + bytes.fromhex("01000000000000")
    response += bytes.fromhex("9000")
    body, sw = split_sw(response)
    check("SW 切分", sw == 0x9000 and not body.endswith(bytes.fromhex("9000")))
    fields = parse_d1(body)
    check("D1 TLV 数量", len(fields) == 7, str(len(fields)))
    check("D1 长度与内容校验", check_profile(fields) == [], str(check_profile(fields)))
    check("A1 从上到下方向可接受",
          check_profile(parse_d1(body.replace(bytes.fromhex("A107") + SCAN_BOTTOM_UP,
                                              bytes.fromhex("A107") + SCAN_TOP_DOWN))) == [])
    check("几何不匹配会被检出",
          len(check_profile(parse_d1(body.replace(GEOMETRY, bytes([0xFF] * 7))))) == 1)
    check("缺失字段会被检出", check_profile({}) != [])
    check("D1 截断会抛错", _raises(lambda: parse_d1(bytes.fromhex("A0070011"))))
    check("状态字说明", "供能" in sw_text(0x6502) and "已知" in sw_text(0x6401) and sw_text(0x9000) == "成功")
    check("状态字：6985 提示 PIN", "PIN" in sw_text(0x6985))
    check("状态字：6451 属配置错误", "配置" in sw_text(0x6451))
    check("唤醒帧含 26/52/7A/7D", [value for value, _ in WAKE_FRAMES] == [0x26, 0x52, 0x7A, 0x7D])

    print("\n%d 项失败" % len(failures) if failures else "\n全部通过")
    return 1 if failures else 0


def _raises(func):
    try:
        func()
    except ValueError:
        return True
    return False


# -------------------------------------------------------------------------- main


def ensure_reader_mode(device):
    status, data = device.exchange(CMD_GET_DEVICE_MODE, timeout=2.0)
    if status == ST_SUCCESS and len(data) == 1 and data[0] == 1:
        return
    status, _ = device.exchange(CMD_CHANGE_DEVICE_MODE, bytes([1]), timeout=3.0)
    if status != ST_SUCCESS:
        raise RuntimeError("切换到读卡器模式失败：status=0x%02X（%s）" % (status, status_text(status)))
    emit("MODE", "已切换到读卡器模式")


def command_info(device):
    status, data = device.exchange(CMD_GET_APP_VERSION, timeout=2.0)
    if status == ST_SUCCESS and len(data) >= 2:
        emit("VERSION", "固件 %d.%d" % (data[0], data[1]))
    status, data = device.exchange(CMD_GET_DEVICE_MODEL, timeout=2.0)
    if status == ST_SUCCESS and data:
        model = {0: "Ultra", 1: "Lite"}.get(data[0], "未知(%d)" % data[0])
        emit("MODEL", "设备型号=" + model)
        if model == "Lite":
            emit("ERROR", "Chameleon Lite 没有 HF 读卡芯片，无法执行本脚本的测试")
            return 1
    ensure_reader_mode(device)
    status, _ = device.exchange(CMD_HF14A_SCAN_KEEP, timeout=2.0)
    supported = status not in (ST_INVALID_CMD, ST_NOT_IMPLEMENTED)
    emit("CAPABILITY", "HF14A_SCAN_KEEP：" + ("固件支持" if supported else "固件不支持，将手动激活"))
    if status == ST_DEVICE_MODE_ERROR:
        emit("INFO", "该状态说明设备当前处于模拟模式；probe 会自动切到读卡器模式")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chameleon Ultra read-only probe for Huawei NFC e-ink panels")
    parser.add_argument("command", choices=["selftest", "info", "scan", "probe", "survey"])
    parser.add_argument("--port", help="串口，例如 COM9 或 /dev/ttyACM0；省略时自动探测")
    parser.add_argument("--timeout-ms", type=int, default=1500, help="每帧等待超时，默认 1500 ms")
    parser.add_argument("--repeat", type=int, default=20, help="survey 的重复轮数，默认 20")
    parser.add_argument("--deep", action="store_true", help="survey 每轮都完成 RATS + D1，更慢但更贴近真实可用性")
    parser.add_argument("--with-d8", action="store_true", help="D1 之后再查询一次 D8 访问条件")
    parser.add_argument("--apdu", help="额外发送的十六进制 APDU（高级用法，自行承担风险）")
    args = parser.parse_args(argv)

    if args.command == "selftest":
        return selftest()

    device, port = connect(args.port)
    emit("STATE", "已连接串口=" + str(port))
    code = 0
    try:
        if args.command == "info":
            code = command_info(device)
        elif args.command == "scan":
            ensure_reader_mode(device)
            code = 0 if run_scan(device, args.timeout_ms) else 1
        elif args.command == "survey":
            ensure_reader_mode(device)
            code = run_survey(device, args)
        else:
            ensure_reader_mode(device)
            code = run_probe(device, args)
    except Timeout as error:
        emit("ERROR", "设备无响应：" + str(error))
        code = 1
    except Exception as error:
        emit("ERROR", "%s: %s" % (type(error).__name__, error))
        code = 1
    finally:
        device.close()
    emit("END", "本轮结束")
    return code


if __name__ == "__main__":
    sys.exit(main())
