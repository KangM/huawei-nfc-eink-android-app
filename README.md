# Huawei NFC E-Ink Android App

Android 墨水屏刷新工具，当前测试版本 **v2.10-test1**。通过 Shizuku 或 Root 调用兼容系统的 NXP Q-Poll，再通过 Android ISO-DEP 向 **552×768 四色华为 A0 / A1 墨水屏**传图刷新，使用板内已存驱动。

## 功能

- 设备与测试：授权连接、NFC 芯片配置识别、Q-Poll 能力测试、普通 NFC-A 对照。
- 图像与发送：选图、四色预览、旋转、裁切、黑白二值化、阈值、色块简化和抖动；支持全屏缩放与 1:1 像素查看。
- 预览和发送按钮位于页面上方，图片编辑选项位于下方；预览高度根据可用空间调整。
- 高级参数：上电等待时间、忙状态查询间隔；参数保存在本地。
- 保留屏幕规格和原厂扫描配置检查，不限制屏幕 UID；发送前确认，运行中可停止。

## 使用

1. 开启 NFC，保持手机解锁。启动 Shizuku 后，在“设备与测试”页授权连接；也可选择 Root 后端。
2. 点击“检查芯片 / NFC”，按需测试 Q-Poll。型号来自系统固件配置，能力以实际控制器响应和通信结果为准。
3. 在“图像与发送”页手动选择实物屏幕类型 A0 或 A1，再选择图片并调整预览，可先执行“只读检查屏幕”。选择会保存，更换屏幕时需重新核对；D1 无法自动判断 A0 / A1。
4. 点击“发送当前预览到墨水屏”，核对图片和参数后确认。持续贴合并保持应用在前台，直到操作结束。

仅适用于兼容 Q-Poll 的手机系统及相同原厂配置的目标屏；不能仅凭接口存在或型号名称判定兼容。刷新结果取决于实际供能与屏幕状态。中断可能留下不完整画面，程序不会自动重发刷新指令。

## 构建与验证

需要 Python 3、JDK 17、Android SDK platform android-34 和 build-tools 34.0.0。设置 `JAVA_HOME`、`ANDROID_HOME` 指向本机安装目录，然后在仓库根目录运行：

```sh
python3 tests/run_java.py
python3 build.py
```

脚本使用 Android SDK 的命令行工具构建，无 Gradle、无构建期下载。Shizuku API / provider / aidl / shared 13.1.5 依赖已固定在 `libs/`。构建脚本当前使用 Unix 工具路径；Windows 可在 WSL 中运行。

如安装了 `lzokay==2.1.0`，可独立检查解压与扫描打包：

```sh
python3 tests/check_fixtures.py build/<protocol-tests-directory>
```

APK 和 SHA-256 输出在 `dist/`。首次构建会在 `build/` 生成本地调试签名密钥；密钥、构建缓存和 APK 不纳入 Git。自行构建时需要保留自己的密钥，以支持后续覆盖安装。

```sh
adb install -r dist/QPollLab-2.10-test1.apk
```

## 隐私

本仓库仅包含 App 源码、测试、构建脚本、必要依赖与许可证。测试使用合成 UID，不包含原始手机日志、NFC 抓包或个人图片。

应用在运行时读取当前设备与标签信息，并在应用私有目录保存运行日志。日志可能包含设备型号、标签 UID 和本轮操作参数；分享日志前请自行检查。应用未申请网络权限。

## 参考逆向项目

本 App 的协议学习与实现参考了以下两个逆向仓库，感谢原作者公开研究成果：

- [kur0neko/huawei-epd-pn532](https://gitee.com/kur0neko/huawei-epd-pn532)：参考原厂图像扫描布局及 D3 传图、D4 刷新流程；对应代码遵循 PolyForm Noncommercial 1.0.0。
- [solar2ain/ink-nfc-fmsc-by-pn532](https://github.com/solar2ain/ink-nfc-fmsc-by-pn532)：参考公开的 D8/APDU 定义及 LZO1X literal-stream 格式；该项目采用 GPL-3.0-or-later。

具体参考提交、版权声明和许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 协议与许可

保留原厂驱动与扫描配置，无 DB/DA 配置写入。图像为 105984 字节，分块发送 D3，单次 D4 启动刷新，通过 DE 查询状态。

参考项目、第三方版权和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。协议与扫描布局所参考代码使用 PolyForm Noncommercial 1.0.0，完整许可证及 Required Notice 保留在 `third-party/` 和 APK assets 中；请遵守其非商业条款。Shizuku 依赖遵循 MIT License。

## v2.9.1 配置诊断

配置校验失败时，日志逐项显示不匹配字段、期望值和实际十六进制值；缺失或长度异常也显示对应字段。D1 只检查首字节时明确标注。仍执行原有兼容性检查，校验失败后不会认证、传图或刷新，不改写 DB/DA。UID 不参与兼容性校验。

## v2.9.2-test1 连接测试版（Issue #1）

针对寻到标签后 ISO-DEP 连接失败，增加最多三次连接尝试：仅 `IsoDep.connect()` 抛出 IOException、尚未发送任何 APDU 时重新启动 ReaderMode，获取同一 UID 的新 Tag；重新寻卡超时、取消或遇到不同 UID 即停止。协议通信开始后不自动重试，保留原有屏幕规格校验。

日志增加 CONNECT_ATTEMPT、ISO_DEP、CONNECTED / CONNECT_FAILED、APDU_BEGIN / APDU_FAILED 和异常堆栈；不记录 PIN 或图像内容。升级后重新连接 Shizuku，先执行“只读检查屏幕”，反馈从本轮开始到结束的完整日志及手机、系统、屏幕型号。此版本是连接恢复尝试与诊断增强，尚未在 Issue 中的目标屏实测，不代表已支持该型号。

## Chameleon Ultra 只读交叉测试

当手机端的 NCI 日志不可见、无法判断是轮询不兼容还是屏幕线圈问题时，可用 `tools/chameleon_probe.py` 绕开 Q-Poll 直接验证屏幕本身。该脚本只发 D1（可选 D8），不写图像、不触发刷新。

需要 Chameleon **Ultra**（内含独立的 14a 读卡芯片；Lite 不支持）与 Python 3.9+：

```sh
pip install pyserial
python tools/chameleon_probe.py selftest                 # 离线自检，不需要硬件
python tools/chameleon_probe.py info    --port COM9      # 连接、固件版本、切读卡器模式
python tools/chameleon_probe.py scan    --port COM9      # 仅标准轮询，结束后关闭场
python tools/chameleon_probe.py probe   --port COM9      # 保持供能：激活 + RATS + D1
python tools/chameleon_probe.py probe   --port COM9 --with-d8
python tools/chameleon_probe.py survey  --port COM9 --repeat 20         # 单点应答率，用于比较贴合位置
python tools/chameleon_probe.py survey  --port COM9 --repeat 10 --deep  # 每轮都跑完 RATS + D1
```

`survey` 重复同一套最简激活并记录命中率。移动读头或改变线圈形状只会改变耦合系数、不会改变协议，所以比较位置优劣应当比百分比而不是比手感：先在某个位置跑一次得到基准，改一处再跑一次，看 `SURVEY_RESULT` 的数字变化。零应答时先换一张已知正常的卡在同一位置复核，确认读卡器本身有效。

脚本依次尝试标准 7 位短帧 `26`（REQA）/`52`（WUPA）与非标准 `7A`/`7D`：上游 solar2ain 的协议文档说明部分 FMSC / 华为驱动板是用 `7A`/`7D` 唤醒的。只试 `26`/`52` 时，这类板会表现为“完全无应答”，极易被误判成线圈损坏。

### 已有 PN532 / PCR532 时的最短路径

上游 solar2ain/ink-nfc-fmsc-by-pn532 直接支持 PN532 / PCR532 的 UART 串口模式，判定这块板能否被寻到不必自己写：

本机实测可用的安装方式（隔离虚拟环境，不要装到系统 Python）：

```powershell
C:\Users\Chen\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc
C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc\Scripts\python.exe -m pip install "git+https://github.com/solar2ain/ink-nfc-fmsc-by-pn532.git"
```

之后用**该虚拟环境**里的 Python 运行（版本 0.1.21 已验证可用，依赖含 Pillow / mini-racer / lzokay / pyserial）：

```powershell
C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc\Scripts\python.exe -m ink_nfc_fmsc ports
C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc\Scripts\python.exe -m ink_nfc_fmsc probe --port COM14 --verbose
```

`ports` 列出读写器串口，典型 PN532 模块显示为 `USB-SERIAL CH340`；`probe` 只做连接检查，不改配置、不刷屏。加上 `--verbose` 会打印协议细节，`--json` 便于存档。`probe` 另有 `--board {auto,huawei,iphone}`、`--reader {auto,pn532,pcr532}`、`--uid`、`--rf-timeout-ms` 等参数：软件扫不到时可显式指定 `--board huawei`、`--reader pn532` 再试。

**它是 GPL-3.0 工具，只作为外部诊断手段使用，不得作为本仓库的依赖或随 APK 分发。**

- **不要省略 `--use-stored-driver`**：指定 `--screen` 而不带该参数会写入 DA / DB，**覆盖驱动板原厂配置且无法还原**。本仓库 App 从不发送 DB / DA，始终使用板内已存驱动。
- **期望要放对**：上游实测 PN532 配小线圈时，华为 A0 / A1 在刷新启动后多次失联；唯一成功完成 A1 552×768 四色刷新的组合是 **PCR532 + Nova 15 Pro/Ultra 原配手机壳里藏在摄像头边框的原配大线圈 + `--use-stored-driver`**。所以 PN532 适合回答“这块板答不答应”，不要指望用它完成 552×768 的刷新。
- **协议要点**：华为板在 D8 返回 `6985` 时需要验证一次已知默认 PIN 再复查 D8（本 App 已内置该步骤）；D4 = `F0 D4 85 80 00`；DE = `F0 DE 00 00 01`；为兼容 PN532 串口帧，ISO-DEP 的 INF 建议限制在 240 bytes。
- **对照价值**：同一个线圈位置上，PN532 找得到而 Chameleon 找不到（或相反），差别就在轮询与唤醒帧，而不是线圈。这一组差异正是手机端“超时未收到标签”的候选解释。

- 优先使用固件的 `HF14A_SCAN_KEEP`；不支持时回退到手动 REQA / 防碰撞 / SELECT / RATS 序列，全程 `-k` 保持射频场不断开，避免轮询间隙导致假阴性。
- ISO14443-4（T=CL） blocks 与 WTX 处理在脚本内实现，固件不提供 APDU 层。
- 输出为 `CP|类型|说明`，类型沿用 App 日志的 TAG / META / ISO_DEP / APDU / RESULT / ERROR，便于两份日志对照。
- D1 回包按与原厂配置相同的字段逐项校验；不匹配只报告，不写入任何数据。
- 未发出任何应答不等于屏幕损坏：请先用一张已知正常的卡执行同一命令校验读卡器，再更换贴合位置重试。供电能力有限，不建议用它尝试传图或刷新。
- **线圈尺寸与折线圈**：耦合系数在两个环路尺寸接近时最大。屏幕线圈比 Chameleon 的天线大很多时，多围出来的面积基本是反向回磁通，正负相抵。把线圈对半平折会让折过去那一半的面积矢量反向、与剩下的一半互相抵消（净磁通趋近于零），同时电感骤降使谐振偏离 13.56 MHz，两个效应叠加通常**比不折更差**。优先尝试把读头整块压在线圈外框走线上（靠近导体的磁场最密，且不动线圈就不会失谐），并用 `survey` 逐点比较命中率。在两者之间加一个调谐到 13.56 MHz 的无源中继环，也能改善尺寸失配且不必改动任何一侧。
- 该脚本仅重新实现了公开的串口帧结构；上游 ChameleonUltra 客户端为 GPL-3.0，未被包含或导入。协议部分同样遵循本仓库的非商业限制。

## PN532 本机实测结论（2026-10-22）

COM14（PN532 / USB-SERIAL CH340），工具版本 0.1.21。目标为**华为 Nova 墨水屏手机壳 NFC 驱动板**，UID `2E 6C 15 1E`，APPID `20 1E A0 01`。

### 一、决定性结论：这块板不响应标准 REQA / WUPA

`probe` 的 `--board` 决定尝试哪些 7 位唤醒短帧（`auto` = `26,52,7A,7D`；`iphone` = 仅 `26,52`；`huawei` = 仅 `7A,7D`），三种模式在同一位置重复验证：

| 模式 | 尝试的帧 | 结果 |
|---|---|---|
| `auto` | `26`→`52`→`7A`→`7D` | 成功，`wake=7A / 7-bit`，D1=9000 |
| `iphone` | `26`、`52` | 失败，`TAG_NOT_FOUND` |
| `huawei` | `7A`、`7D` | 成功，`wake=7A / 7-bit`，D1=9000 |

`auto` 是**先试标准帧**的，最终落在 `7A`，说明 `26`/`52` 确实没有应答。工具在激活前会先关场约 1.5 秒再开，并分别等待 5 ms 与 200 ms 各试一轮，两种等待都不影响结果。

**由此得到 Android 侧的判定**：这块板只有收到非标准的 7 位短帧 `7A`（或 `7D`）才应答，而 Android 公开 API 无法发送 7 位短帧 —— `NfcA.transceive()` 官方文档明确写了"Applications must only send commands that are complete bytes, for example a SENS_REQ is not possible"，且 CRC 由栈自动附加。也就是说：

- 手机"`本轮超时，未收到标签`"**不是轮询间隙掉电、也不是 Q-Poll 是否生效**的问题，而是**标准轮询压根发不出这块板要的那个帧**；
- 调 polling 参数、换 discovery 类型（包括 Q-Poll）都不改变这一点，除非能走到厂商私有的"发送原始比特级帧"通道；
- 一旦被 `7A` 唤醒之后，后续 SELECT / RATS / ISO-DEP / D1 全是标准流程 —— 所以 Chameleon 脚本（-k 保持场 + bitlen=7）和 PN532 都能通。缺的只是**唤醒那一下**。

### 二、传图成功、刷新失败

`send-test` 实测（已写入本次所选屏幕的 DA / DB 配置）：

- PIN 认证成功：`D8=6985` → 验证已知 PIN → `D8=9000`；本仓库 App 已内置该步骤。
- 配置写入确认成功，`D1=9000`。
- **传图 53/53 全部 `SW=9000`** —— PN532 + 小线圈完全能完成 105984 字节的传图。
- `D4=9000` 被接受，随后 DE 查询在约 2～3 秒时返回 **`68CA`（低电压告警）**。
- 用 `refresh` 子命令（**不写 DA/DB、不重传图像，只重发 D4 + 轮询 DE**）把预充电从 1 s 加到 5 s、`--pre-refresh-ms 5000`、`--first-de-delay-ms 3000`、`--de-hz 3` 后重试，**仍在 `68CA` 上失败**。说明这不是充电时序问题，而是刷新波形期间的持续供能不足。

### 三、下一步该怎么排

1. **耦合第一**：把 PN532 天线整块压在线圈走线上（不是几何中心）、加 13.56 MHz 调谐中继环、给 PN532 模块独立 5 V 供电（不要只取 CH340 的 3.3 V），每改一处重跑一次 `refresh` 对照。
2. **半屏刷新降低峰值电流**：`--passes two` 对本屏有效（two-pass.json 里 `huawei_a1_jd79665`，标记 `experimental`，每半溢出 90/120 秒观测窗口）。它会写 DA / DB 把驱动改成"半幅窗口"，**属于不可还原的写操作**，执行前想清楚。
3. 重试刷新优先用 `refresh` 子命令，它对已存图像只做 D4 + DE，不碰配置：

```powershell
C:\Users\Chen\.workbuddy\binaries\python\envs\inknfc\Scripts\python.exe -m ink_nfc_fmsc refresh --port COM14 --screen JD79665AA-552x768-BWRY-A1 --yes --verbose --timeout 120
```

> 写入后读到的板内配置（供对照，非原厂值）：
> `A0 07 F0 C7 20 06 00 02 28` `A1 07 01 12 00 30 FF FF FF` `B1 01 02` `B2 01 14` `B3 01 00` `C0 04 20 1E A0 01` `C1 04 2E 6C 15 1E` `D1 07 01 20 00 00 00 00 00`

## v2.10-test1 手动选择 A0 / A1

- “图像与发送”页新增屏幕类型选项，首次使用必须明确选择；不根据 D1、UID 或旧版设置猜测型号。切换后重新生成发送快照，运行中不能更改；发送确认和日志显示所选类型。
- A1 保留原有线性控制器行布局，A0 使用参考仓库的奇偶行交错布局。预览按所选布局反解，显示逻辑画面，而非交错后的传输排列。
- 继续使用板内已存驱动，不发送 DB / DA。不负责将 A1 驱动板重配为 A0 或反向改装；实物与板内驱动必须对应。
- 接受已知的 `00120030FFFFFF` / `01120030FFFFFF` 扫描配置，并按读出的方向调整传输数据；其他规格校验仍然保留。写入前复核发现方向变化即停止，不传图。
- 预览快照 SHA-256 与实际发送 SHA-256 分别记录；扫描方向转换发生在 D1 校验之后。只读检查仍可在未选择型号时执行，但不能据此识别 A0 / A1。
- 全帧映射与参考打包函数逐字节比对通过，回归覆盖两种型号、两种扫描方向、实际 D3 数据、预览还原及未知选择拒绝；APK 构建和签名验证通过。尚未完成两种实物屏的刷新验证；此改动不解决 D1 返回 `6401` 的问题。

## v2.11 已知缺陷：setReaderMode 反射签名不匹配

v2.11.x 在部分 ROM 上**一条寻卡命令都发不出去**，日志形如：

```
CLEANUP_ERROR · 释放 ReaderMode 失败：java.lang.NoSuchMethodException: android.nfc.INfcAdapter.setReaderMode
                [interface android.os.IBinder, interface android.nfc.IAppCallback, int, class android.os.Bundle, class java.lang.String]
ERROR · NoSuchMethodException: ... at org.nfcresearch.qpolllab.QPollWorker.invoke(QPollWorker.java:76)
RESTORE_FAILED · 恢复未确认，请重启 NFC 开关后再试
PROCESS_EXIT · 2
```

**这与目标屏无关，是纯软件回归。** 较新的 Android 在 `INfcAdapter.setReaderMode` 末尾加了一个调用方包名参数；代码只按 5 参数签名反射，而目标设备的 NFC 服务上只存在经典的 4 参数签名，于是 `getMethod` 直接抛 `NoSuchMethodException`，ReaderMode 从未启用。

已修两处（`src/org/nfcresearch/qpolllab/QPollWorker.java`）：

1. `reader()` 改为按 **5 参数 → 4 参数** 依次探测并缓存命中的 `Method`，沿用成功时的参数个数装配实参；第一次命中会重新打印 `READER_API · 使用 setReaderMode N 参数接口`，可直接确认这台机走的是哪个签名。两种都没有时抛出提示两者的异常。
2. `readerTouched = true` 移到 `reader(0x81)` **成功之后**。原先是在调用前赋值，导致启用失败也会被当成“已改动读头”，于是清理阶段再失败一次、并误报 `RESTORE_FAILED` 与退出码 2 —— 真正的 `NoSuchMethodException` 被这条假象盖住了。

> 注意：修完这一处，日志会恢复成 `START` → `本轮超时，未收到标签`。那是另一个问题 —— 见上文「PN532 本机实测结论」：这块板只接受私有 7 位短帧 `7A`/`7D`，Android 公开 API 发不出来。本节的修复只负责让 App 回到“能正常发起寻卡”的状态，不代表能读到这块屏。

### 术语对照：日志里的模式名 vs UI 按钮

Worker 的命令行参数名和 UI 上的按钮名不一致，排查时对一下：

| 日志 / 命令里的名字 | App 里的按钮 | 做什么 | 是否下发 QTag |
|---|---|---|---|
| `standard` | **普通 NFC-A 对照**（诊断页） | 只用系统标准 NFC-A 轮询找标签，只读 UID/ATQA/SAK，不发 APDU | 否 |
| `qtag` | **测试 Q-Poll 能力**（诊断页） | 先开 QTag 再走同样的轮询，观察 NCI 是否真的用了 0x71 | 是 |
| `qtag-hold` | **组合测试：QTag + 前台寻卡**（诊断页） | 只开 QTag 并驻留；**寻卡由应用前台进程执行**，绕开后台准入限制 | 是（仅此进程） |
| `foreground` | **前台 ReaderMode 对照（应用进程）** | 不经过 worker，完全由应用进程调用 `enableReaderMode` 找标签 | 否 |
| `inspect` | **检查芯片 / NFC**（诊断页） | 只读配置、确认 NXP 测试入口是否存在，不发寻卡命令 | 否 |
| `identity` | **只读检查屏幕** | 找到标签后发 D1 校验屏幕规格，**不写图像** | 是 |
| `refresh` | **发送当前预览到墨水屏** | 完整流程：D1→D8(PIN)→D3 传图→D4 刷新→DE 查忙 | 是 |

验证上述 setReaderMode 修复时用 **`standard`（普通 NFC-A 对照）**：贴一张任意普通卡（银行卡、公交卡、门禁卡都行），10 秒内日志应出现 `READER_API · 使用 setReaderMode 4 参数接口` 和 `TAG 收到标签 UID=…`。能读到普通卡就证明寻卡链路恢复正常；读不到那块墨水屏是正常的，原因在上一段。

## v2.12 根因：后台进程调用的 setReaderMode 会被系统静默忽略

修好反射签名后仍然读不到**任何**标签（包括普通卡），不是还有一个软件 bug，而是撞上了 Android 框架的权限模型：

```java
// platform/packages/apps/Nfc · NfcService#setReaderMode（Android 12 起，AOSP / LineageOS 一致）
int callingUid = Binder.getCallingUid();
boolean privilegedCaller = (callingUid == Process.SYSTEM_UID
        || getPackageNameFromUid(callingUid).equals("com.android.systemui"));
if (!privilegedCaller && !mForegroundUtils.isInForeground(callingUid)) {
    Log.e(TAG, "setReaderMode: Caller is not in foreground and is not system process.");
    return;                      // ← 静默返回：不抛异常、不注册回调、不改轮询配置
}
```

要点：

- **调用成功的判定不可靠。** `READER_API · 使用 setReaderMode 4 参数接口` 只是反射找到方法并调用完毕，**不代表服务端接受了它**。服务端这一条路径是 `return`，不是 `throw`，所以进程不会报错，`START` 也会照常打印 —— 看起来一切正常，实际上轮询配置一行都没改。
- **根 UID 也救不了。** 判定只看 UID：必须是 `Process.SYSTEM_UID`（1000）、包名 `com.android.systemui`，或者调用方处于前台。Worker 跑在 **UID 2000（shell）**、无 Activity，三个条件都不满足。
- 因此 **App 现有架构下，任何标签都不可能送达 worker** —— 这与目标屏无关，普通卡同样收不到，此前的“超时未收到标签”不能用来推断硬件或线圈。

### 对照验证（已加进 App）

诊断页新增按钮 **「前台 ReaderMode 对照（应用进程）」**：同样的 `setReaderMode`、同样的 flags（`NFC_A | SKIP_NDEF_CHECK`），但由**应用自身进程**且带前台 Activity 调用，绕开上面这个判定。日志会出现：

```
FRAMEWORK · 由应用自身进程调用系统 ReaderMode：调用方 UID=10xxx，Activity 处于前台。
START · foreground:10
TAG 收到标签 UID=…；技术=[android.nfc.tech.NfcA, ...]
RESULT · 已收到标签（前台 ReaderMode）；只显示元信息，未发送 APDU
```

判读：**普通卡在此模式下出现 `TAG`，而同模式的后台路径没有** ⇒ 确认是框架的后台限制。此后所有涉及 Q-Poll 的判定都必须改走这条前台路径，否则结论无效。

同时 worker 的 logcat 过滤器加了 `NfcService` / `NfcDispatcher`，被忽略时会直接把框架那一行捞出来：

```
READER_IGNORED · 系统忽略本次 ReaderMode：调用方 UID=2000 既不是前台应用也不是 system；这一轮不会收到任何标签，判定的优先级高于本轮结果。
RESULT · 本轮无结果：系统忽略了后台进程的 ReaderMode（见 READER_IGNORED 行）；不能据此认定硬件不支持
```

> 未改动的部分：这条路径仍然只会发标准 `26`/`52` 轮询，因此**墨水屏大概率依旧不出现**（见「PN532 本机实测结论」）。它解决的是“为什么连普通卡都收不到”，而不是“怎么唤醒墨水屏”。

### 拆分修复：QTag 交给特权进程，寻卡交给前台进程

上面那条判定带来一个无法回避的结论：**worker 永远不可能收到标签**。但开启 QTag 又必须走特权（`sendVendorNciMessage` 只有 shell/system 调得到）。这两件事本来就该由两个不同的进程分别做：

| 动作 | 需要什么 | 由谁执行 |
|---|---|---|
| 开启 / 关闭 QTag（vendor NCI `31 01`） | shell / system 特权 | `QPollWorker`（UID 2000） |
| `setReaderMode` 寻卡、收 `onTagDiscovered` | 调用方在前台 | `MainActivity` 所在的应用进程 |

因此新增 worker 模式 **`qtag-hold`**：只做 `init → qtag(1) → 驻留等待 → qtag(0)`，**完全不碰 `setReaderMode`**（在那里调它只会被静默丢弃，白白留下一行误导日志）。驻留期间它持续读 logcat，所以 `NxpNciX` / `NxpNciR` 里的 `0x71` 证据仍然会被抓到。

诊断页对应按钮 **「组合测试：QTag + 前台寻卡」**，时序是：

```
特权进程：QTAG_ON 启用 QTag（驻留模式），API 状态=0
特权进程：START · qtag-hold:10
应用进程：FRAMEWORK · QTag 已由特权进程开启；本进程 UID=10xxx（前台 Activity）接管 ReaderMode
应用进程：START · foreground:10
      …… 10 秒内贴合并观察 ……
应用进程：RESULT · 组合测试：本轮超时，未收到标签；QTag 与前台 ReaderMode 均未产生标签
特权进程：QTAG_OFF / RESTORED（应用写入 stop 文件后自动关闭 QTag）
```

`qtag-hold` 自带兜底：即使应用进程异常退出，驻留时间到（`seconds + 15` 秒）也会自己关掉 QTag，不会把 NFC 留在改写状态。

**这个实验的判读要靠普通卡做标尺**，顺序不能省：

| 步骤 | 普通卡（NTAG / MIFARE） | 判读 |
|---|---|---|
| ① 只开前台 ReaderMode | 有 `TAG` | 前台路径可用，框架限制确认 |
| ② 组合测试（QTag 已开） | **读不到了** | QTag 真的把发现流程改写成了 `Q_POLL` —— 这正是我们要的状态 |
| ② 组合测试（QTag 已开） | 仍能读到 | QTag 未生效或该 HAL 忽略它，`0x71` 那条线可以判死 |
| ③ 组合测试 + 墨水屏 | 出现 `TAG` 且 `ATQA` 合理 | 私有 `7A`/`7D` 之外的**第二条**唤醒通路成立，App 有救 |
| ③ 组合测试 + 墨水屏 | 仍无标签 | Q-Poll 不是这块板的唤醒方式，Android 侧定论为「公开 API 做不到」 |

③ 的两种结果都有价值：前者意味着 App 只需按上面的分工重构就能用，后者则要放弃手机方案、转向 PN532 / 外接读头。

### 2026-10-22 实测：① 成立、② 也成立（QTag 被控制器拒绝）

v2.13-combo-test1 在实机上跑出下面两段（同一张 MIFARE Classic 普通卡）：

```
──── foreground · 前台 ReaderMode 对照 10 秒 ────
FRAMEWORK · 由应用自身进程调用系统 ReaderMode：调用方 UID=10430，Activity 处于前台。
TAG · 收到标签 UID=AAB59F1F；技术=[android.nfc.tech.NfcA, android.nfc.tech.MifareClassic]
META · ATQA=0400；SAK=0008

──── qtag-hold · 寻卡 10 秒 ────
STATE · 执行身份 UID=0；NFC 状态=3（已开启）
QTAG_ON · 启用 QTag（驻留模式），API 状态=0（只是主机状态）
QTAG_REJECTED · 系统拒绝 QTag，响应状态=05；当前系统未成功开启 Q-Poll。
TAG · 收到标签 UID=AAB59F1F；……（前台寻卡照常拿到标签）
RF_TX · 210303010001
RF_RX · 61051501808000FF010A040004AAB59F1F01080000000000
```

三条结论：

1. **框架限制确证。** 同一台机器、同样的 flags，应用进程（UID 10430、有前台 Activity）一次就拿到标签；后台 worker 无论 UID 2000 还是 0 都拿不到。
2. **Q-Poll 在本机不可用。** 这次 worker 跑在 **UID=0（Root）**，权限不可能更宽，控制器仍返回 `05`。按 NCI 状态表，`05 = SYNTAX_ERROR`，`07/08` 才是「不支持该命令组/命令」。所以下一步要区分的是**编码不对**还是**功能缺失**，而不是权限。
3. **NCI 层不再失明（修正早先判断）。** `RF_TX` / `RF_RX` 现在能稳定打出来 —— 之前「`NxpNciX`/`NxpNciR` 一个包都不吐」的结论，是因为当时后台 ReaderMode 被丢弃、根本没有空口活动，不是因为日志 tag 不对。

为此新增诊断项 **「QTag 命令探测（只读扫参）」**（worker 模式 `qtag-sweep`）：依次发出 7 种厂商命令编码，并把控制器每条原始响应 `4F70…` 连同状态码含义打印出来。

| 探测结果 | 含义 | 下一步 |
|---|---|---|
| 某个候选返回 `00` | 编码问题，Q-Poll 功能其实存在 | 用它替换 `qtag()` 的 payload，重跑组合测试 |
| 全部返回 `05` | 命令结构始终不被接受 | 基本无望，但仍可试 NCI 层 `RF_DISCOVER` 直填 `0x71` |
| 返回 `07` / `08` | 该固件没有这条命令 | Q-Poll 判死，手机方案到此为止 |

无论哪种结果，**按下按钮前先确认电池与贴合位置**：探测只改读卡器配置，不写屏幕、不传图，失败也不会损坏驱动板。

### 2026-10-22 实测：7 个候选全部 `05`

```
QTAG_TRY  · 候选 1/7 · 3101      → QTAG_RESP · 4F700105 · 状态 05 = SYNTAX_ERROR，命令结构不被接受
QTAG_TRY  · 候选 2/7 · 310100    → 4F700105
QTAG_TRY  · 候选 3/7 · 310101    → 4F700105
QTAG_TRY  · 候选 4/7 · 310102    → 4F700105
QTAG_TRY  · 候选 5/7 · 31010000  → 4F700105
QTAG_TRY  · 候选 6/7 · 3100      → 4F700105
QTAG_TRY  · 候选 7/7 · 31        → 4F700105
```

连**只有一个字节**的 `31` 都回 `05`，说明不是长度或参数取值的问题：这台机器的 NFC 固件对 `(GID 0x0F, OID 0x70)` 这条厂商命令一律拒绝。它也没有回 `08 UNKNOWN_OID`，但 NXP 固件对未编入的功能常统一回 `SYNTAX_ERROR`，所以**「编码不对」这条路已经基本排除，剩下的可能只有「该机型根本没编入该功能」**。

顺带注意日志里那些 `RF_RX · 6105…`：探测期间前台仍有一张普通卡贴着，系统在正常轮询并反复上报同一张卡 —— 也就是说链路本身一直是好的。

### v2.13-combo-test3：能力报告（只读，不发任何报文）

「检查芯片 / NFC」现在额外打印三行 `SYSTEM`，用来一次性回答「这台机器到底有没有这个功能」：

| 字段 | 内容 | 判读 |
|---|---|---|
| 机型 | 厂商、型号、代号、Android 版本 | 若非华为墨水屏手机壳适配机型，Q-Poll 本就不会存在 |
| NFC 入口 | `INfcAdapter` / `NfcAdapter` 里含 vendor / discover / reader / raw 等关键字的方法及参数个数 | 若没有任何 `sendVendorNciMessage` 之外的厂商入口，可反射的面就到头了 |
| 配置关键字 | 所有 `libnfc*.conf` 中含 `qpoll` / `eink` / `epd` 的行 | 出现即说明固件支持该项；一行都没有则基本判死 |

这一项纯读、零风险，可以在任何状态下点。

### 2026-10-22 能力报告实测：realme RMX3888 上没有这项功能，手机方案结案

实机回传三行：

```
SYSTEM · 机型=realme RMX3888（RE5C37）；Android 15 / SDK 35
SYSTEM · NFC 入口=INfcAdapter.enableReaderOption(1参)；INfcAdapter.getNfcAdapterVendorInterface(1参)；
         INfcAdapter.isReaderOptionEnabled(0参)；INfcAdapter.isReaderOptionSupported(0参)；
         INfcAdapter.registerVendorExtensionCallback(1参)；INfcAdapter.sendVendorNciMessage(4参)；
         INfcAdapter.setReaderMode(4参)；INfcAdapter.updateDiscoveryTechnology(3参)；
         NfcAdapter.enableReaderMode(4参)；NfcAdapter.enableReaderOption(1参)；
         NfcAdapter.registerNfcVendorNciCallback(2参)；NfcAdapter.resetDiscoveryTechnology(1参)；
         NfcAdapter.sendVendorNciMessage(4参)；NfcAdapter.setDiscoveryTechnology(3参)；……
SYSTEM · 配置关键字=已扫描 1 个 libnfc 配置，未出现 QPOLL / EPD / EINK 等关键字
```

**结论：这台机器没有 Q-Poll，手机侧方案正式结案。** 三条证据各自独立、方向一致：

1. **配置层**：唯一一个 `libnfc*.conf` 里没有任何 e-ink / Q-Poll 相关配置项。
2. **命令层**：厂商命令 `(GID 0x0F, OID 0x70)` 的 7 种编码全部 `4F 70 01 05`，连单字节 `31` 也一样 —— 固件里没有这条命令的处理分支。
3. **空口层**：前台 ReaderMode 一次就能拿到普通卡（`ATQA=0400`、`SAK=0008`，MIFARE Classic），链路、线圈、控制器都是好的。

差的只有「发出 7 位短帧 `7A` / `7D`」这一件事，而 **NCI 协议里没有位级短帧原语** —— 这是协议层的硬边界，不是权限、不是型号白名单、也不是编码问题。所以报告里那几个看起来像「新机会」的入口都救不了这条路径：

| 入口 | 实际能做什么 | 为什么不够 |
|---|---|---|
| `setDiscoveryTechnology` / `resetDiscoveryTechnology` | 整体接管 / 恢复发现流程，可完全关掉轮询 | 关掉轮询 ≠ 能发出 `7A`；短帧仍需协议支持 |
| `getNfcAdapterVendorInterface` | 拿到 NXP HAL 厂商接口，可读固件版本、可 `doAction` | 最终还是拼 NCI 报文，同样没有位级帧 |
| `enableReaderOption` / `isReaderOptionSupported` | realme 自己加的（AOSP 没有），用于息屏 / 后台保持寻卡 | 与唤醒码无关 |
| `sendVendorNciMessage` | 唯一的厂商命令通道 | 已实测被固件拒绝 |

**手机端若还要能用，只剩「外挂读卡器」一条路**（USB-OTG 接 PN532 或 Chameleon Ultra）。但在此之前先看清真正的墙：

- PC + PN532 已经能发 `7A`、能传完 53/53 帧，却在 `D4` 刷新时 `68CA`（低电压）。
- 加大 `--precharge-ms`、降低 `--de-hz`、延后首次 `DE` 查询都无效 ⇒ 是**场强 / 耦合**不足，不是时序。
- 而且早先一次 `send-test` 未带 `--use-stored-driver`，板内 `DA`/`DB` 已被写成上游通用驱动，**原厂驱动不可恢复**。

也就是说：把 PN532 挂到手机上，也只能做到「唤醒 + 传图」，刷新照样 `68CA`。**刷新才是当前唯一的真瓶颈，优先级高于「App 怎么发 7A」。**

## GitHub Actions 自动构建

仓库里有 `.github/workflows/build-apk.yml`，不需要 Gradle —— 流水线就是「装 JDK + 补齐 build-tools → `python3 build.py`」。

**触发条件**：push 到 `main` / `feature/**` / `fix/**`、PR、push `v*` 标签，或在 Actions 页面手动 `Run workflow`。

**取产物**：Actions → 对应运行 → 底部 `Artifacts` → `QPollLab-apk`，内含 `QPollLab-<version>.apk` 与 `.sha256`。推送 `v*` 标签时会额外创建一个 Release 并附上 APK。

**fork 之后第一次**：GitHub 对 fork 默认不自动运行 workflow，需要到 Actions 标签页点一次 “I understand my workflows, go ahead and enable them”。

**签名要注意**：`build.py` 在没有现成 keystore 时会现场生成一把随机调试密钥，于是每次 CI 产物的签名都不同，直接覆盖安装会报 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`（得先卸载）。两种解法：

- 配一个仓库 secret `KEYSTORE_B64`（内容为 `base64 -w0 debug.keystore`），workflow 会还原它，签名从此固定；
- 什么都不配也行，workflow 会用 Actions 缓存保存同一把钥匙 —— 但缓存 **7 天不用会被清理**，那时签名会重新随机。

**工具链兼容**：优先用 `build-tools/34.0.0` + `platforms/android-34`；镜像里没有时 `build.py` 会自动回退到已安装的最新版本，workflow 也会先尝试用 `sdkmanager` 补装。本地构建命令不变：

```sh
ANDROID_HOME=/path/to/sdk JAVA_HOME=/path/to/jdk python3 build.py
```
