/*
 * 墨水屏接线自检固件 —— AirM2M Core C3 (ESP32-C3) + SE0398NZ07 / JD79665AA
 *
 * 这块屏是华为 Nova 彩显手机壳里的那颗 3.98 英寸四色墨水屏：
 *   可见区 768 x 552，2 bit/pixel，四色（黑 / 白 / 红 / 黄），
 *   控制器帧存固定为 800 x 600，可见图像放在 (32, 0)。
 *
 * 本固件的唯一目的：在**几十秒内**告诉你"八根线到底接对没有"，而不是先做一个完整
 * 的图片播放器。所以它按阶段打日志，任何一步失败都会停下来并直接指出该查哪根线：
 *
 *   阶段 1  采样 BUSY 的空闲电平（浮空 / 接错会马上暴露）
 *   阶段 2  硬复位，观察 BUSY 是否先忙后闲（验证 RST + BUSY）
 *   阶段 3  写 16 条初始化寄存器（验证 SCK / DIN / DC / CS 是否真的到了控制器）
 *   阶段 4  PON 上电，等 BUSY 释放（验证升压电路能被点亮）
 *   阶段 5  传一帧测试图，600 行 x 200 字节（验证数据通路）
 *   阶段 6  DRF 刷新，等 BUSY 释放（真正出画面，约 15~25 秒）
 *
 * 串口 115200。启动后自动跑一遍；之后可用键盘重绘：
 *   p = 测试图   b = 全黑   w = 全白   c = 依次循环
 *
 * 引脚按实际飞线（就是图里那张对照表），改线只需要改下面六个宏。
 */

#include <Arduino.h>
#include <SPI.h>
#include <string.h>

/* ------------------------------------------------------------------ 引脚 --- */
#define PIN_EPD_SCK   4    /* CLK  */
#define PIN_EPD_MOSI  6    /* SDA / DIN */
#define PIN_EPD_CS    7
#define PIN_EPD_DC    1
#define PIN_EPD_RST   2
#define PIN_EPD_BUSY  10

/* ---------------------------------------------------------------- 面板参数 --- */
#define IMG_W          768            /* 可见区宽 */
#define IMG_H          552            /* 可见区高 */
#define PHY_W          800            /* 控制器帧存宽（必须） */
#define PHY_H          600            /* 控制器帧存高（必须） */
#define IMG_X0         32             /* 可见图像在控制器帧里的横向偏移 */
#define PHY_LINE_BYTES (PHY_W / 4)    /* 200 字节/行，4 像素/字节 */
#define IMG_LINE_BYTES (IMG_W / 4)    /* 192 字节/行 */

#define EPD_SPI_HZ     10000000       /* 参考实现跑 10 MHz；出问题先降到 4 MHz */

/* BUSY 高 = 空闲，低 = 忙。这是 JD79665 的极性，别按"低有效"的直觉改。 */
#define BUSY_READY     1

/* 四色的 2 bit 编码由屏定义，不能随意置换 */
#define C_BLACK   0x00
#define C_WHITE   0x01
#define C_YELLOW  0x02
#define C_RED     0x03

/* ---------------------------------------------------------------- 命令字 --- */
#define CMD_PSR    0x00
#define CMD_PWR    0x01
#define CMD_POF    0x02
#define CMD_PON    0x04
#define CMD_BTST   0x06
#define CMD_DSLP   0x07
#define CMD_DTM    0x10
#define CMD_DRF    0x12
#define CMD_PLL    0x30
#define CMD_VCOM   0x50
#define CMD_TCON   0x60
#define CMD_TRES   0x61
#define CMD_GSST   0x65
#define CMD_PTL    0x83
#define CMD_TSSET  0x84
#define CMD_AN_TM  0xAA
#define CMD_CCSET  0xE3

/* PSR 的扫描方向位。参考实现用 0x4B；如果出图是 180 度旋转的，
 * 把 EPD_PSR_FLIP 打开（0x43）即可。 */
#define EPD_PSR_NORMAL 0x4B
#define EPD_PSR_FLIP   0x43
// #define EPD_PSR_FLIP_ENABLE

#if defined(EPD_PSR_FLIP_ENABLE)
  #define EPD_PSR_VALUE EPD_PSR_FLIP
#else
  #define EPD_PSR_VALUE EPD_PSR_NORMAL
#endif

/* ---------------------------------------------------------------- 5x7 字库 --- */
struct Glyph { char c; uint8_t rows[7]; };

static const Glyph FONT[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}},
    {'-', {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00}},
    {'/', {0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10}},
    {'0', {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}},
    {'1', {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'2', {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}},
    {'3', {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E}},
    {'4', {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}},
    {'5', {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E}},
    {'6', {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}},
    {'7', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}},
    {'8', {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}},
    {'9', {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C}},
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'B', {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E}},
    {'C', {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E}},
    {'D', {0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C}},
    {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}},
    {'F', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10}},
    {'G', {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F}},
    {'H', {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'I', {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'J', {0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C}},
    {'K', {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11}},
    {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F}},
    {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}},
    {'N', {0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11}},
    {'O', {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
    {'Q', {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D}},
    {'R', {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
    {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E}},
    {'V', {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04}},
    {'W', {0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A}},
    {'X', {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11}},
    {'Y', {0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04}},
    {'Z', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F}},
};
static const int FONT_N = (int)(sizeof(FONT) / sizeof(FONT[0]));
static int8_t s_font_index[128];

static void font_build_index(void) {
    memset(s_font_index, -1, sizeof(s_font_index));
    for (int i = 0; i < FONT_N; ++i) {
        uint8_t c = (uint8_t)FONT[i].c;
        if (c < 128) s_font_index[c] = (int8_t)i;
    }
}

static const uint8_t *font_rows(char c) {
    uint8_t u = (uint8_t)c;
    if (u >= 128 || s_font_index[u] < 0) return NULL;
    return FONT[(int)s_font_index[u]].rows;
}

/* 判断 (x,y) 是否落在以 (x0,y0) 为左上角、scale 倍放大的字符串笔画上。
 * 字符框宽 6*scale（5 列字形 + 1 列字距），高 7*scale。 */
static int text_ink(const char *s, int x0, int y0, int scale, int x, int y) {
    const int adv = 6 * scale, gh = 7 * scale;
    if (y < y0 || y >= y0 + gh) return 0;
    int dx = x - x0;
    if (dx < 0) return 0;
    int idx = dx / adv;
    if (idx >= (int)strlen(s)) return 0;
    int cx = (dx % adv) / scale;
    if (cx >= 5) return 0;
    const uint8_t *g = font_rows(s[idx]);
    if (!g) return 0;
    int ry = ((y - y0) % gh) / scale;
    return (g[ry] >> (4 - cx)) & 1;
}

/* ------------------------------------------------------------------ 版面 --- */
/* 测试图的每个区块都对应一根线的可验证性：
 *   边框      -> 几何/偏移对不对（歪了边框就不对称）
 *   四色块    -> 2bpp 数据通路 + 四种颜色都出得来
 *   棋盘格    -> 横向寻址是否逐点正确
 *   竖条纹    -> 列方向是否有整列丢失（典型的 DIN/CLK 接触不良）
 *   1px 细线  -> 行方向是否有整行丢失
 *   文字      -> 方向是否颠倒/镜像，一眼可读
 *   角标      -> 上下左右不要靠猜                      */
#define BORDER      4
#define TXT1_X      24
#define TXT1_Y      16
#define TXT1_SCALE  3
#define TXT2_X      24
#define TXT2_Y      46
#define TXT2_SCALE  2

#define SW_Y0       76
#define SW_H        148
#define SW_W        168
#define SW_GAP      16
#define SW_X0       24

#define CHK_X0      24
#define CHK_Y0      256
#define CHK_W       360
#define CHK_H       116
#define CHK_CELL    10

#define STR_X0      408
#define STR_Y0      256
#define STR_W       21
#define STR_N       16
#define STR_H       116

#define FINE_X0     24
#define FINE_Y0     396
#define FINE_X1     744
#define FINE_H      54
#define FINE_STEP   4

#define FCHK_X0     24
#define FCHK_Y0     466
#define FCHK_W      160
#define FCHK_H      40
#define FCHK_CELL   4

#define TXT3_X      204
#define TXT3_Y      478
#define TXT3_SCALE  2

static const uint8_t SW_COLORS[4] = {C_BLACK, C_WHITE, C_RED, C_YELLOW};
static const char    SW_LETTERS[4] = {'B', 'W', 'R', 'Y'};

static inline bool in_box(int x, int y, int x0, int y0, int w, int h) {
    return x >= x0 && x < x0 + w && y >= y0 && y < y0 + h;
}

/* 测试页：返回该像素的颜色编码 */
static uint8_t pattern_color(int x, int y) {
    /* 外边框 —— 位置错、镜像、旋转都会让它变得不对称 */
    if (x < BORDER || y < BORDER || x >= IMG_W - BORDER || y >= IMG_H - BORDER) return C_BLACK;

    /* 四色块：每块 2px 黑描边，块内填本色，正中一个对比色字母 */
    if (y >= SW_Y0 && y < SW_Y0 + SW_H && x >= SW_X0) {
        for (int i = 0; i < 4; ++i) {
            int bx = SW_X0 + i * (SW_W + SW_GAP);
            if (!in_box(x, y, bx, SW_Y0, SW_W, SW_H)) continue;
            bool inner = in_box(x, y, bx + 2, SW_Y0 + 2, SW_W - 4, SW_H - 4);
            if (!inner) return C_BLACK;
            uint8_t base = SW_COLORS[i];
            uint8_t ink = (base == C_BLACK) ? C_WHITE : C_BLACK;
            char label[2] = {SW_LETTERS[i], 0};
            int lx = bx + (SW_W - 6 * 4) / 2;          /* scale 4 -> 字宽 24 */
            int ly = SW_Y0 + (SW_H - 7 * 4) / 2;
            if (text_ink(label, lx, ly, 4, x, y)) return ink;
            return base;
        }
    }

    /* 棋盘格：10px 分辨率，横向寻址错位一眼可见 */
    if (in_box(x, y, CHK_X0, CHK_Y0, CHK_W, CHK_H))
        return (((x / CHK_CELL) + (y / CHK_CELL)) & 1) ? C_BLACK : C_WHITE;

    /* 竖条纹：整列丢失 / 相邻列粘连最容易在这里看出来 */
    if (in_box(x, y, STR_X0, STR_Y0, STR_W * STR_N, STR_H))
        return (((x - STR_X0) / STR_W) & 1) ? C_BLACK : C_WHITE;

    /* 1 像素细线：整行丢失或行错位会表现为线条粗细不均 */
    if (y >= FINE_Y0 && y < FINE_Y0 + FINE_H && x >= FINE_X0 && x <= FINE_X1)
        return (((y - FINE_Y0) % FINE_STEP) == 0) ? C_BLACK : C_WHITE;

    /* 细棋盘（4px）：单像素级寻址 */
    if (in_box(x, y, FCHK_X0, FCHK_Y0, FCHK_W, FCHK_H))
        return (((x / FCHK_CELL) + (y / FCHK_CELL)) & 1) ? C_BLACK : C_WHITE;

    /* 角标：右上黑、左下红。方向不对时它会跑到别处 */
    if (in_box(x, y, IMG_W - BORDER - 32, BORDER + 4, 28, 28)) return C_BLACK;
    if (in_box(x, y, BORDER + 4, IMG_H - BORDER - 32, 28, 28)) return C_RED;

    /* 文字 */
    if (text_ink("WIRING OK", TXT1_X, TXT1_Y, TXT1_SCALE, x, y)) return C_BLACK;
    if (text_ink("JD79665 768X552", TXT2_X, TXT2_Y, TXT2_SCALE, x, y)) return C_RED;
    if (text_ink("TOP LEFT", TXT3_X, TXT3_Y, TXT3_SCALE, x, y)) return C_BLACK;

    return C_WHITE;
}

#define PAGE_PATTERN 0
#define PAGE_BLACK   1
#define PAGE_WHITE   2

static uint8_t page_color(int page, int x, int y) {
    switch (page) {
        case PAGE_BLACK: return C_BLACK;
        case PAGE_WHITE: return C_WHITE;
        case PAGE_PATTERN:
        default:         return pattern_color(x, y);
    }
}

/* ------------------------------------------------------------------- SPI --- */
static inline void cs_low(void)  { digitalWrite(PIN_EPD_CS, LOW); }
static inline void cs_high(void) { digitalWrite(PIN_EPD_CS, HIGH); }

static void epd_cmd(uint8_t c) {
    digitalWrite(PIN_EPD_DC, LOW);
    cs_low();
    SPI.transfer(c);
    cs_high();
}

static void epd_data(const uint8_t *d, size_t n) {
    digitalWrite(PIN_EPD_DC, HIGH);
    cs_low();
    SPI.writeBytes(const_cast<uint8_t *>(d), n);
    cs_high();
}

static void epd_cmd_data(uint8_t c, const uint8_t *d, size_t n) {
    epd_cmd(c);
    if (n) epd_data(d, n);
}

/* ---------------------------------------------------------------- BUSY --- */
static int busy_level(void) { return digitalRead(PIN_EPD_BUSY); }

/* 三种结局必须分开报，否则"一直低"会被误读成"忙过又空闲"。
 * （v1 就踩过这个坑：超时时把 saw 置真，日志打印成"先忙后闲"。） */
enum BusyWait {
    BUSY_OK,           /* 先低（忙）后高（闲）：控制器真的干过活 */
    BUSY_NEVER_LOW,    /* 全程高：忙信号从没出现 */
    BUSY_STUCK_LOW,    /* 全程低：BUSY 一直被拉低，从未释放 */
};

static BusyWait wait_busy_ready(uint32_t timeout_ms, uint32_t *waited_ms) {
    uint32_t t0 = millis();
    bool saw_low = false;
    while (millis() - t0 < timeout_ms) {
        if (busy_level() != BUSY_READY) {
            saw_low = true;
        } else if (saw_low) {
            if (waited_ms) *waited_ms = millis() - t0;
            return BUSY_OK;
        }
        delay(1);
    }
    if (waited_ms) *waited_ms = millis() - t0;
    return saw_low ? BUSY_STUCK_LOW : BUSY_NEVER_LOW;
}

static bool wait_busy_assert(uint32_t timeout_ms, uint32_t *waited_ms) {
    uint32_t t0 = millis();
    while (millis() - t0 < timeout_ms) {
        if (busy_level() != BUSY_READY) {
            if (waited_ms) *waited_ms = millis() - t0;
            return true;
        }
        delay(1);
    }
    if (waited_ms) *waited_ms = millis() - t0;
    return false;
}

/* -------------------------------------------------------------- 初始化表 --- */
static bool panel_init(void) {
    uint8_t psr[2] = {EPD_PSR_VALUE, 0x69};
    uint8_t tres[4] = {(uint8_t)(PHY_W >> 8), (uint8_t)(PHY_W & 0xFF),
                       (uint8_t)(PHY_H >> 8), (uint8_t)(PHY_H & 0xFF)};
    static const uint8_t an_tm[6] = {0x49, 0x55, 0x20, 0x08, 0x09, 0x18};
    static const uint8_t pwr[1]   = {0x3F};
    static const uint8_t v1[4]    = {0x40, 0x1F, 0x1F, 0x2C};   /* 0x05 */
    static const uint8_t v2[4]    = {0x6F, 0x1F, 0x1F, 0x22};   /* 0x08 */
    static const uint8_t v3[4]    = {0x6F, 0x1F, 0x14, 0x14};   /* 0x06 booster */
    static const uint8_t v4[4]    = {0x00, 0x54, 0x00, 0x44};   /* 0x03 */
    static const uint8_t tcon[2]  = {0x02, 0x00};
    static const uint8_t pll[1]   = {0x08};
    static const uint8_t vcom[1]  = {0x3F};
    static const uint8_t gsst[4]  = {0x10, 0x00, 0x20, 0x00};
    static const uint8_t ccset[1] = {0x2F};
    static const uint8_t tsset[1] = {0x01};

    epd_cmd_data(CMD_AN_TM, an_tm, sizeof(an_tm));
    epd_cmd_data(CMD_PWR,   pwr,   sizeof(pwr));
    epd_cmd_data(CMD_PSR,   psr,   sizeof(psr));
    epd_cmd_data(0x05,      v1,    sizeof(v1));
    epd_cmd_data(0x08,      v2,    sizeof(v2));
    epd_cmd_data(CMD_BTST,  v3,    sizeof(v3));
    epd_cmd_data(0x03,      v4,    sizeof(v4));
    epd_cmd_data(CMD_TCON,  tcon,  sizeof(tcon));
    epd_cmd_data(CMD_PLL,   pll,   sizeof(pll));
    epd_cmd_data(CMD_VCOM,  vcom,  sizeof(vcom));
    epd_cmd_data(CMD_TRES,  tres,  sizeof(tres));
    epd_cmd_data(CMD_GSST,  gsst,  sizeof(gsst));
    epd_cmd_data(CMD_CCSET, ccset, sizeof(ccset));
    epd_cmd_data(CMD_TSSET, tsset, sizeof(tsset));
    return true;
}

static void set_full_window(void) {
    uint8_t w[9] = {0x00, 0x00,
                    (uint8_t)((PHY_W - 1) >> 8), (uint8_t)((PHY_W - 1) & 0xFF),
                    0x00, 0x00,
                    (uint8_t)((PHY_H - 1) >> 8), (uint8_t)((PHY_H - 1) & 0xFF),
                    0x01};
    epd_cmd_data(CMD_PTL, w, sizeof(w));
}

/* -------------------------------------------------------------- 整帧发送 --- */
/* 控制器要求 600 x 200 字节。可见图像放在 x=32，右侧/下方补白。 */
static void build_phys_line(uint8_t *out, int py, int page) {
    memset(out, 0x55, PHY_LINE_BYTES);           /* 0x55 = 4 个白 */
    if (py >= IMG_H) return;                     /* 552..599 行在可见区外 */
    const int first = IMG_X0 / 4;                /* 第 8 个字节起是可见区 */
    for (int i = first; i < PHY_LINE_BYTES; ++i) {
        int x = i * 4 - IMG_X0;
        uint8_t c0 = page_color(page, x + 0, py);
        uint8_t c1 = page_color(page, x + 1, py);
        uint8_t c2 = page_color(page, x + 2, py);
        uint8_t c3 = page_color(page, x + 3, py);
        out[i] = (uint8_t)((c0 << 6) | (c1 << 4) | (c2 << 2) | c3);
    }
}

static uint32_t send_frame(int page) {
    static uint8_t line[PHY_LINE_BYTES];
    uint32_t t0 = millis();
    set_full_window();
    epd_cmd(CMD_DTM);
    for (int py = 0; py < PHY_H; ++py) {
        build_phys_line(line, py, page);
        epd_data(line, PHY_LINE_BYTES);
        if ((py & 63) == 0) yield();
    }
    return millis() - t0;
}

/* ---------------------------------------------------------------- 刷新 --- */
/* 盲刷：完全不看 BUSY，按固定时长推进。
 * 用途是把"BUSY 这根线坏了"和"整条链路都不通"分开 —— 若盲刷能出图，
 * 说明只有 BUSY 一根线有问题，其余七根都对。 */
static bool g_blind = false;

static bool power_on(void) {
    if (g_blind) {
        epd_cmd(CMD_PON);
        delay(300);
        Serial.println("      [盲刷] PON 已发，固定等 300 ms（不看 BUSY）");
        return true;
    }
    uint32_t ms = 0;
    epd_cmd(CMD_PON);
    BusyWait r = wait_busy_ready(6000, &ms);
    switch (r) {
        case BUSY_OK:
            Serial.printf("      PON ok：BUSY 先忙后闲，%u ms\n", (unsigned)ms);
            return true;
        case BUSY_NEVER_LOW:
            Serial.println("      PON: BUSY 全程为高，没看到忙 —— 控制器可能根本没收到 PON");
            Serial.println("           → 先查 SCK(IO4)/DIN(IO6)/DC(IO1)/CS(IO7)");
            return false;
        case BUSY_STUCK_LOW:
        default:
            Serial.printf("      PON: BUSY 一直为低，%u ms 都没释放\n", (unsigned)ms);
            Serial.println("           → 屏处在复位态/没上电，或 BUSY 被下拉。见 [1/6][2/6] 的判别结论");
            return false;
    }
}

static bool display_refresh(void) {
    uint8_t zero = 0x00;
    uint32_t ms = 0;
    set_full_window();
    epd_cmd(CMD_DRF);
    epd_data(&zero, 1);

    if (g_blind) {
        Serial.println("      [盲刷] DRF 已发，固定等 25 秒（四色全刷通常 15~25 秒）");
        delay(25000);
        return true;
    }

    uint32_t assert_ms = 0;
    bool asserted = wait_busy_assert(8000, &assert_ms);
    Serial.printf("      DRF 后 %u ms 内 BUSY %s\n",
                  (unsigned)assert_ms, asserted ? "拉低（开始刷新）" : "没有拉低");
    if (!asserted) return false;

    BusyWait r = wait_busy_ready(90000, &ms);
    if (r == BUSY_OK) {
        Serial.printf("      刷新结束，用时 %u ms\n", (unsigned)(assert_ms + ms));
        return true;
    }
    Serial.printf("      DRF 后 BUSY 没有释放（%u ms）：波形可能没真正跑起来\n", (unsigned)ms);
    return false;
}

static bool power_off(void) {
    uint8_t zero = 0x00;
    epd_cmd(CMD_POF);
    epd_data(&zero, 1);
    /* POF 的忙脉冲很短，有一部分固件根本不拉低 BUSY。
     * 先短等一次"有没有忙"，没有就立刻返回，避免每刷一页白等 6 秒。 */
    uint32_t ms = 0;
    if (g_blind) {
        delay(300);
        return true;
    }
    if (wait_busy_assert(1500, &ms)) {
        uint32_t waited = 0;
        wait_busy_ready(6000, &waited);
    }
    return true;
}

/* ------------------------------------------------------------- 单页流程 --- */
static bool show_page(int page) {
    const char *name = (page == PAGE_PATTERN) ? "测试图"
                     : (page == PAGE_BLACK)   ? "全黑"
                     :                          "全白";
    Serial.printf("\n[显示] %s\n", name);

    if (!power_on()) {
        Serial.println("  !! PON 没有正常完成：优先查 VDD/3V3 供电、BUSY(IO10)、RST(IO2)");
        Serial.println("  >> 若想确认「是不是只有 BUSY 一根线坏了」，按 x 盲刷（忽略 BUSY，约 26 秒）");
        return false;
    }

    uint32_t tx = send_frame(page);
    Serial.printf("      传图完成：%d 行 x %d 字节 = %d 字节，用时 %u ms\n",
                  PHY_H, PHY_LINE_BYTES, PHY_H * PHY_LINE_BYTES, (unsigned)tx);

    if (!display_refresh()) {
        Serial.println("  !! 刷新没有启动：控制器很可能没收到有效 SPI 数据。");
        Serial.println("     依次查 DC(IO1) / CS(IO7) / SCK(IO4) / DIN(IO6) 的焊接与是否接反。");
        return false;
    }

    power_off();
    Serial.println("  ok 画面应已更新（四色全刷约 15~25 秒，请稍等再抬眼看）");
    return true;
}

/* ----------------------------------------------------------------- 串口 --- */
static void banner(void) {
    Serial.println();
    Serial.println("==============================================");
    Serial.println(" 墨水屏接线自检  JD79665AA 768x552 BWRY");
    Serial.println(" ESP32-C3 (AirM2M Core) / PlatformIO");
    Serial.println("==============================================");
    Serial.printf("  SCK=IO%d  DIN=IO%d  CS=IO%d  DC=IO%d  RST=IO%d  BUSY=IO%d\n",
                  PIN_EPD_SCK, PIN_EPD_MOSI, PIN_EPD_CS, PIN_EPD_DC, PIN_EPD_RST, PIN_EPD_BUSY);
    Serial.printf("  SPI=%d Hz, PSR=0x%02X%s, 控制器帧 %dx%d\n", EPD_SPI_HZ, EPD_PSR_VALUE,
                  (EPD_PSR_VALUE == EPD_PSR_FLIP) ? " (翻转)" : "", PHY_W, PHY_H);
    Serial.println("  屏幕上应该是：黑边 + 四色块 + 文字 WIRING OK");
    Serial.println("  按 p=测试图  b=全黑  w=全白  c=循环  x=盲刷  d=重判 BUSY  r=重复位");
    Serial.println("----------------------------------------------");
}

/* 阶段 1：判别 BUSY 这根线到底是"没接上"还是"被外面拉住"。
 * 手法是打开 ESP32 内部上拉（约 45k）再读一次：
 *   · 浮空读 0、上拉后读 1  -> 线是断的 / 假焊（上拉能拉起来，说明对面什么都没接）
 *   · 上拉后仍然读 0        -> 被外部强行拉低：屏在复位态主动拉低，或线对地短路
 *   · 两次都读 1            -> 正常空闲（JD79665 高=空闲）
 */
static void check_busy_line(void) {
    pinMode(PIN_EPD_BUSY, INPUT);
    delay(3);
    int floating = busy_level();

    int high = 0;
    for (int i = 0; i < 32; ++i) {
        if (busy_level() == BUSY_READY) high++;
        delay(2);
    }

    pinMode(PIN_EPD_BUSY, INPUT_PULLUP);
    delay(3);
    int pulled = busy_level();
    pinMode(PIN_EPD_BUSY, INPUT);

    Serial.printf("[1/6] BUSY 线路判别：浮空读 %d，32 次里 %d 次为高，内部上拉后读 %d\n",
                  floating, high, pulled);

    if (pulled == 0) {
        Serial.println("      >> 结论：BUSY 被外部拉低（45k 上拉拉不动）");
        Serial.println("         两种可能：① 屏在复位态主动拉低 ② 这根线对 GND 短路");
        Serial.println("         若下面 [2/6] 放开 RST 后仍不升高，优先怀疑 RST 没接到屏");
    } else if (floating == 0) {
        Serial.println("      >> 结论：BUSY 这根线基本是断的（上拉能拉起来 = 对面没接东西）");
        Serial.println("         量 IO10 到屏 BUSY 的通断；这种状态下的忙/闲读数全不可信");
    } else if (high != 32) {
        Serial.println("      >> 结论：线路浮空且在抖 —— 屏没有驱动这根线");
        Serial.println("         典型原因：屏那边没电（3V3/GND 断了）、或 BUSY 线断了");
        Serial.println("         量屏侧 3V3 对 GND 有没有 3.3V，再量这根线的通断");
    } else {
        Serial.println("      >> 结论：空闲电平正常");
    }
}

/* 阶段 2：复位后跟踪 BUSY 轨迹。RST 真的接到了屏，放开后必须看到 0->1 的跳变。 */
static void reset_and_trace(void) {
    digitalWrite(PIN_EPD_RST, HIGH);
    delay(20);
    digitalWrite(PIN_EPD_RST, LOW);
    delay(50);
    digitalWrite(PIN_EPD_RST, HIGH);

    char trace[129];
    int n = 0;
    bool saw_low = false, saw_rise = false;
    int rise_ms = -1;
    uint32_t t0 = millis();
    while (millis() - t0 < 500 && n < 128) {
        int lv = busy_level();
        trace[n++] = (lv == BUSY_READY) ? '1' : '0';
        uint32_t now = millis() - t0;
        if (lv != BUSY_READY) {
            saw_low = true;
        } else if (saw_low && !saw_rise) {
            saw_rise = true;
            rise_ms = (int)now;
        }
        delay(2);
    }
    trace[n] = 0;
    Serial.printf("      复位后 500ms 内 BUSY 轨迹（每 2ms 一点）：%s\n", trace);

    /* 只有"先低后高"才算真正的释放。轨迹一开始就是 1 绝不能算通 ——
     * 那是线路浮空被上拉的结果，v2 曾在这里误报成"复位通路正常"。 */
    if (saw_rise) {
        Serial.printf("      >> BUSY 在 %d ms 由低转高，复位通路正常\n", rise_ms);
    } else if (saw_low) {
        Serial.println("      >> 被拉低后再没升高 ⇒ RST(IO2) 可能没接到屏，或屏没供电");
    } else {
        Serial.println("      >> 全程为高，BUSY 从未被拉低 ⇒ 屏没有响应复位：没上电，或线没接上");
    }
}

void setup() {
    Serial.begin(115200);
    delay(400);                      /* USB CDC 起来要一点时间 */
    font_build_index();
    banner();

    pinMode(PIN_EPD_CS, OUTPUT);   digitalWrite(PIN_EPD_CS, HIGH);
    pinMode(PIN_EPD_DC, OUTPUT);   digitalWrite(PIN_EPD_DC, HIGH);
    pinMode(PIN_EPD_RST, OUTPUT);  digitalWrite(PIN_EPD_RST, HIGH);
    pinMode(PIN_EPD_BUSY, INPUT);  /* 屏侧自己驱动，常态不上拉，避免掩盖浮空 */

    check_busy_line();

    /* 之后统一用内部上拉：屏是推挽输出，45k 上拉压不过它，判读不受影响；
     * 但线断了的时候能把抖动的高阻节点定成稳定的 1，日志才不会一片噪声。 */
    pinMode(PIN_EPD_BUSY, INPUT_PULLUP);
    delay(2);

    SPI.begin(PIN_EPD_SCK, -1, PIN_EPD_MOSI, -1);
    SPI.beginTransaction(SPISettings(EPD_SPI_HZ, MSBFIRST, SPI_MODE0));

    Serial.println("[2/6] 硬复位：RST 高 20ms -> 低 50ms -> 高");
    reset_and_trace();

    Serial.println("[3/6] 写初始化寄存器表（14 条）");
    panel_init();
    Serial.printf("      完成，PSR=0x%02X\n", EPD_PSR_VALUE);

    Serial.println("[4/6] 准备上电并刷第一页");
    show_page(PAGE_PATTERN);

    Serial.println("\n==============================================");
    Serial.println(" 完成。若屏幕有画面 -> 八根线都对。");
    Serial.println(" 若屏幕全白/全黑没变化 -> 见上面失败点提示。");
    Serial.println("==============================================");
}

void loop() {
    if (!Serial.available()) {
        delay(20);
        return;
    }
    int c = Serial.read();
    if (c == 'x' || c == 'X') {
        Serial.println("\n>>> 盲刷模式：完全忽略 BUSY，按固定时长推进 <<<");
        g_blind = true;
        show_page(PAGE_BLACK);        /* 先全黑：整屏变色最容易看出来 */
        delay(2000);
        show_page(PAGE_PATTERN);
        g_blind = false;
    }
    else if (c == 'd' || c == 'D') {
        /* 现场用：把屏那一侧的 BUSY 线拔掉再按 d，对比读数就能分清
         * "线断了" 和 "屏在拉低" */
        Serial.println("\n[重新判别] BUSY 线路");
        check_busy_line();
    }
    else if (c == 'r' || c == 'R') {
        Serial.println("\n[重新复位] 跟踪 BUSY 轨迹");
        reset_and_trace();
    }
    else if (c == 'p' || c == 'P')      show_page(PAGE_PATTERN);
    else if (c == 'b' || c == 'B') show_page(PAGE_BLACK);
    else if (c == 'w' || c == 'W') show_page(PAGE_WHITE);
    else if (c == 'c' || c == 'C') {
        show_page(PAGE_PATTERN);
        delay(3000);
        show_page(PAGE_BLACK);
        delay(3000);
        show_page(PAGE_WHITE);
    } else if (c == '\r' || c == '\n') {
        /* 忽略回车 */
    } else {
        Serial.println("p=测试图  b=全黑  w=全白  c=循环  x=盲刷  d=重判 BUSY  r=重复位");
    }
}
