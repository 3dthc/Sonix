#!/usr/bin/env python3
"""Draws the Sonix faceplate (three pages, three colorways) and writes the matching layout, from one set of coordinates.

    python3 tools/make_skin.py                 artwork in vst/art/ and vst/layout.conf (both committed)
    python3 tools/make_skin.py --mockup DIR    full-page previews with sample values and MPC's live text drawn in,
                                               DIR/<page>_<theme>.png (needs the toolchain's Titillium Web fonts)

Needs Playwright's Chromium; run on a PC, not in the plugin build.

Pages (MPC's function-key tabs), each with its own 16 Q-Links in four columns of four:
  MAIN      the LCD in the centre (preset name with prev / next arrows, a live waveform, the four layers), the
            category buttons, layer and favourite buttons below it; FILTER and AMP ENVELOPE on the left, LFO and
            FX & OUTPUT on the right (Q-Link columns 1-4).
  LAYERS    one strip per layer: a small LCD with its instrument, prev / next, on and solo, LEVEL, PAN, OCTAVE, FINE.
  SETTINGS  TUNING and PLAYING on the left, DELAY and REVERB on the right; the about screen and the SYSTEM choices
            (theme, display animation, mod wheel, velocity curve) in the centre.

What is static (chassis, panels, engraved names, LCD glass and frames) is one background per page and theme. What
moves is a control or a picture the engine drives (knobs, lit buttons, the category grid, the LCD waveform, LEDs,
the CPU bar) plus MPC's own live text. The engine changes pictures only on events or, for the waveform, at most ten
times a second while notes sound (every repaint costs MPC's screen thread: mpc-vst-plugins docs/NOTES.md).

Coordinates below are the plugin area (1280 x 628); layout.conf's are 86 px lower (MPC's header).
"""
import base64
import json
import os
import sys

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
VST = os.path.join(HERE, "..", "vst")
ART = os.path.join(VST, "art")
FONTS = os.path.join(VST, "fonts")
MPC_FONTS = os.path.join(os.environ.get("MPC_VST", os.path.join(HERE, "..", "..", "mpc-vst-plugins")), "tools", "html_art", "fonts")
Y_OFF = 86
VERSION = "0.3.0"

THEMES = ["midnight", "platinum", "studio", "purple", "yellow", "gold", "green", "camo", "red", "silver", "ice"]   # = "theme" options
THEME_NAMES = ["MIDNIGHT", "PLATINUM", "STUDIO GREY", "PURPLE", "YELLOW", "GOLD", "GREEN", "CAMO", "METALLIC RED", "SILVER", "ICE BLUE"]
CATS = ["PIANO", "KEYS", "GUITAR", "STRINGS", "BRASS", "CHOIR", "PAD", "SYNTH", "LEAD", "PLUCK", "BASS", "BELLS"]
CAT_KEYS = ["cat_" + c.lower() for c in CATS]
LAYERS = "abcd"

# ---- sizes: every button one size, every knob one size (big-hands rule); text sizes in px
BTN_W, BTN_H, BTN_GAP = 108, 47, 8                          # a button picture is BTN_W x (BTN_H + 4): the drop shadow
KNOB_R = 36
LABEL_PX, VALUE_PX, BUTTON_PX, TITLE_PX = 17, 21, 16, 12
LCD_PX = dict(top=22, name=42, cell=28, line=24, lcat=22, lname=27, list=26, bank=25)

# ---- the chassis shared by MAIN and SETTINGS: a 2 x 2 knob panel in each corner, the centre column between
W, H = 1280, 628
SIDE_W = 268
LX, RX = 8, W - 8 - 268                                      # 8, 1004
CX0, CX1 = LX + SIDE_W + 8, RX - 8                           # 284 .. 996
CW = CX1 - CX0                                               # 712
TOP_Y = 46                                                   # the centre column starts under the header
SIDE_Y, PANEL_H = 8, 303                                     # the side panels run the full height
BOT_Y = SIDE_Y + PANEL_H + 6                                 # 317
KCOL = (70, 198)                                             # knob centres across a side panel
KROW0, KPITCH = 30, 136                                      # first knob row's top below the panel top, row pitch

MAIN_SECTIONS = [  # (x, y, title, four (key, label)); list order = Q-Link order (one column of four each)
    (LX, SIDE_Y, "FILTER", [("cutoff", "CUTOFF"), ("resonance", "RESONANCE"), ("env_amt", "ENV AMT"), ("key_track", "KEY TRACK")]),
    (LX, BOT_Y, "AMP ENVELOPE", [("attack", "ATTACK"), ("decay", "DECAY"), ("sustain", "SUSTAIN"), ("release", "RELEASE")]),
    (RX, SIDE_Y, "LFO", [("lfo_rate", "RATE"), ("lfo_filter", "FILTER"), ("lfo_pitch", "PITCH"), ("lfo_amp", "AMP")]),
    (RX, BOT_Y, "FX & OUTPUT", [("chorus", "CHORUS"), ("delay", "DELAY"), ("reverb", "REVERB"), ("volume", "VOLUME")]),
]
SETTINGS_SECTIONS = [
    (LX, SIDE_Y, "TUNING", [("a4_ref", "A4 REF"), ("tune", "TUNE"), ("transpose", "TRANSPOSE"), ("bend_range", "BEND")]),
    (LX, BOT_Y, "PLAYING", [("polyphony", "POLYPHONY"), ("vel_sens", "VELOCITY"), ("vel_filter", "VEL > FILTER"), ("spread", "SPREAD")]),
    (RX, SIDE_Y, "DELAY", [("delay_time", "TIME"), ("delay_fb", "FEEDBACK"), ("delay_tone", "TONE"), ("delay_width", "WIDTH")]),
    (RX, BOT_Y, "REVERB", [("rev_size", "SIZE"), ("rev_damp", "DAMPING"), ("rev_predelay", "PRE-DELAY"), ("rev_width", "WIDTH")]),
]
# MAIN's Q-Links: the four sections' knobs, but the 16th (VOLUME, which MPC's track has too) browses presets
MAIN_QLINKS = [k for _, _, _, ks in MAIN_SECTIONS for k, _ in ks][:15] + ["browse"]
LAYER_KNOBS = [("level", "LEVEL"), ("pan", "PAN"), ("octave", "OCTAVE"), ("fine", "FINE")]
SYSTEM_ROWS = [  # (key, label, caption, options)
    ("anim", "ANIMATION", "LCD waveform", ["OFF", "LOW", "SMOOTH"]),
    ("mod_wheel", "MOD WHEEL", "what the wheel adds", ["VIBRATO", "FILTER", "OFF"]),
    ("vel_curve", "VELOCITY", "response curve", ["SOFT", "NORMAL", "HARD"]),
]

# ---- MAIN's centre: one tall LCD. Two views on it: BROWSE (Nexus-style: banks on the left, that bank's patches on the
# right) and LAYERS (the waveform, the four layers, the preset's description); a tab strip at its foot switches them.
BEZEL = (CX0, TOP_Y, CW, 574)
SCREEN = (CX0 + 12, TOP_Y + 12, CW - 24, 550)
SX, SY, SW = SCREEN[0], SCREEN[1], SCREEN[2]
LCD_TOP = (SX + 18, SY + 6, 410, 32)
LCD_REF = (SX + SW - 18 - 230, SY + 6, 230, 32)
LCD_RULE_Y = SY + 42
LCD_PREV = (SX + 8, SY + 50, BTN_W, BTN_H + 4)
LCD_NEXT = (SX + SW - 8 - BTN_W, SY + 50, BTN_W, BTN_H + 4)
LCD_NAME = (SX + 8 + BTN_W + 8, SY + 50, SW - 2 * (8 + BTN_W + 8), BTN_H + 4)
LCD_RULE2_Y = SY + 108
AREA_Y = SY + 116                                            # the views' area, down to the tab strip
LIST_N, LIST_TH, LIST_GAP = 12, 30, 1                        # 12 rows: every bank, and every category's patches
LIST_H = LIST_N * (LIST_TH + LIST_GAP) - LIST_GAP            # 371
BANK_BOX = (SX + 10, AREA_Y, 198, LIST_H)
PATCH_BOX = (SX + 228, AREA_Y, SW - 238, LIST_H)
DIVIDER_X = SX + 218
LCD_WAVE = (SX + 16, AREA_Y + 4, SW - 32, 112)               # two meters side by side (MPC's strip width limit)
WAVE_FRAMES = 17                                             # 0 idle, then 4 levels x 4 phases
CELL_Y, CELL_H, CELL_W, CELL_GAP = AREA_Y + 130, 62, (SW - 32 - 8) // 2, 8   # the four layers, 2 x 2
LCD_LINE = (SX + 18, AREA_Y + 280, SW - 36, 40)
TAB_Y = SY + 493                                             # the tab strip: BROWSE | LAYERS, then FAV and LIST
VIEWS = ["BROWSE", "LAYERS"]
TAB_X = SX + 8
LCD_TOGGLES = [("fav", "★  FAV"), ("fav_only", "★  LIST")]
TOGGLE_X = SX + SW - 8 - 2 * BTN_W - BTN_GAP
LFO_LED = (RX + SIDE_W - 26, SIDE_Y + 15)                    # an LED by the LFO panel's title, lit while the LFO runs


def list_w(box, n):
    """A list's width for colorway n. The skin builder names a list's row pictures by size, so each colorway's list is
    2 px narrower than the one before: its own pictures, invisible on screen."""
    return box[2] - 2 * n


# ---- LAYERS: four strips
STRIP_W, STRIP_PITCH = 310, 318
STRIP_Y, STRIP_H = TOP_Y, 574
MINI = (12, 30, 286, 104)                                    # mini LCD bezel within a strip (x, y offsets, w, h)
STRIP_BTN_Y = (STRIP_Y + 150, STRIP_Y + 207)
STRIP_KCOL = (82, 228)
STRIP_KROW = STRIP_Y + 272                                   # first knob row's top
STRIP_KPITCH = 138

# ---- SETTINGS' centre
ABOUT = (CX0, TOP_Y, CW, 250)
SYSTEM = (CX0, TOP_Y + 258, CW, 316)
SYS_ROW_Y0, SYS_ROW_PITCH = SYSTEM[1] + 38, 66
SYS_BTN_X = CX1 - 22 - (3 * BTN_W + 2 * BTN_GAP)

LOGO_X = 640 - 116                                           # the header sits over the centre column: MIDI, logo, CPU
HEADER_LED = (CX0 + 22, 22)                                  # MIDI LED centre
CPU_BAR = (CX1 - 112, 17, 96, 10)



# ---- colorways. The first three are drawn by hand; the rest come from a few base colours each (theme()).
def rgba(hexc, a):
    h = hexc.lstrip("#")
    return "rgba(%d,%d,%d,%g)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def theme(name, dark, bg, panel, accent, lcd, lcd_text, lcd_dim, btn, btn_on, text=None, muted=None, **extra):
    """A colorway from its base colours. dark: light text on dark metal (else dark text on light metal)."""
    t = dict(bg1=bg[0], bg2=bg[1], bg3=bg[2], panel1=panel[0], panel2=panel[1],
             led=accent, ledGlow=rgba(accent, 0.7), logoGlow=rgba(accent, 0.55),
             lcd=lcd, lcdText=lcd_text, lcdDim=lcd_dim, lcdFaint=rgba(lcd_text, 0.10), lcdGlow=rgba(lcd_text, 0.5), rowFill=0.2,
             btnTop=btn[0], btnBot=btn[1], btnOnTop=btn_on[0], btnOnBot=btn_on[1], name=name,
             knobHi="#5a616d", knobLo="#0a0b0d", knobEdge="#050607", capHi="#454a54", capLo="#16181c",
             ridge="rgba(255,255,255,0.10)", knobLine="#eef1f5")
    if dark:
        t.update(edgeHi="rgba(255,255,255,0.08)", panelEdge="#0b0c0f", bevelHi="rgba(255,255,255,0.09)",
                 drop="rgba(0,0,0,0.55)", text=text or "#eceff3", muted=muted or "#b3bac6",
                 engrave=[0, -1, "rgba(0,0,0,0.75)"], logo="#f2f4f7", ledOff="rgba(0,0,0,0.45)",
                 bezel1="#0b0c0f", bezel2="#24262c", btnEdge="#0b0c0f", btnShadow="#07080a", btnText="#eceff3",
                 btnEngrave=[0, -1, "rgba(0,0,0,0.75)"], screw=["#7a7f88", "#2a2d33"], well="rgba(0,0,0,0.16)")
    else:
        t.update(edgeHi="rgba(255,255,255,0.75)", panelEdge="rgba(0,0,0,0.28)", bevelHi="rgba(255,255,255,0.75)",
                 drop="rgba(0,0,0,0.24)", text=text or "#15171a", muted=muted or "#3a3f46",
                 engrave=[0, 1, "rgba(255,255,255,0.7)"], logo=text or "#15171a", ledOff="rgba(0,0,0,0.28)",
                 bezel1="#2a2c30", bezel2="#5a5d62", btnEdge="rgba(0,0,0,0.35)", btnShadow="rgba(0,0,0,0.38)",
                 btnText="#16181b", btnEngrave=[0, 1, "rgba(255,255,255,0.75)"], screw=["#ffffff", "#8d9198"],
                 well="rgba(0,0,0,0.09)", knobHi="#7c828c", knobLo="#121417", capHi="#686e77", capLo="#1e2125",
                 ridge="rgba(255,255,255,0.13)", knobLine="#f4f4f4")
    t.update(extra)
    return t


THEME_TABLE = {
    "midnight": dict(bg1='#2b2e35', bg2='#16181c', bg3='#111216', edgeHi='rgba(255,255,255,0.08)',
        panel1='#262a32', panel2='#1a1d22', panelEdge='#0b0c0f', bevelHi='rgba(255,255,255,0.09)', drop='rgba(0,0,0,0.55)',
        text='#e4e7ec', muted='#a3acba', engrave=[0, -1, 'rgba(0,0,0,0.75)'], logo='#eef1f5', logoGlow='rgba(76,195,255,0.6)',
        led='#4cc3ff', ledOff='#1d2732', ledGlow='rgba(76,195,255,0.7)',
        bezel1='#0b0c0f', bezel2='#22252c', lcd='#06121f', lcdText='#9fe4ff', lcdDim='#7cc2e6', rowFill=0.2,
        lcdFaint='rgba(143,220,255,0.10)', lcdGlow='rgba(120,210,255,0.5)',
        knobHi='#5a616d', knobLo='#0a0b0d', knobEdge='#050607', capHi='#454a54', capLo='#16181c', ridge='rgba(255,255,255,0.10)', knobLine='#eef1f5',
        btnTop='#3b404a', btnBot='#22252b', btnOnTop='#16293c', btnOnBot='#0e1b28', btnEdge='#0b0c0f', btnShadow='#07080a', btnText='#d9dde4',
        btnEngrave=[0, -1, 'rgba(0,0,0,0.75)'], screw=['#70767f', '#2a2d33'], well='rgba(0,0,0,0.16)', name='MIDNIGHT'),
    "platinum": dict(bg1='#eef0f3', bg2='#c9cdd2', bg3='#b2b6bc', edgeHi='rgba(255,255,255,0.85)',
        panel1='#e4e7eb', panel2='#c7cbd1', panelEdge='#9aa0a7', bevelHi='rgba(255,255,255,0.9)', drop='rgba(0,0,0,0.22)',
        text='#16191d', muted='#3d444e', engrave=[0, 1, 'rgba(255,255,255,0.8)'], logo='#16191d', logoGlow='rgba(255,154,31,0.4)',
        led='#ff9a1f', ledOff='#9f9b93', ledGlow='rgba(255,154,31,0.7)',
        bezel1='#7c8188', bezel2='#c3c7cd', lcd='#1a1206', lcdText='#ffbe5c', lcdDim='#e6a24f', rowFill=0.2,
        lcdFaint='rgba(255,182,72,0.10)', lcdGlow='rgba(255,170,60,0.5)',
        knobHi='#7c828c', knobLo='#121417', knobEdge='#0c0d0f', capHi='#686e77', capLo='#1e2125', ridge='rgba(255,255,255,0.13)', knobLine='#f4f4f4',
        btnTop='#fbfbfc', btnBot='#cdd1d6', btnOnTop='#fff5e6', btnOnBot='#efd2ad', btnEdge='#939880', btnShadow='#858a91', btnText='#1f2328',
        btnEngrave=[0, 1, 'rgba(255,255,255,0.85)'], screw=['#ffffff', '#999da4'], well='rgba(0,0,0,0.08)', name='PLATINUM'),
    "studio": dict(bg1='#c3c5c8', bg2='#a4a7ab', bg3='#909397', edgeHi='rgba(255,255,255,0.55)',
        panel1='#b8bbbf', panel2='#9ea1a5', panelEdge='#7a7d82', bevelHi='rgba(255,255,255,0.6)', drop='rgba(0,0,0,0.26)',
        text='#121417', muted='#262a2f', engrave=[0, 1, 'rgba(255,255,255,0.5)'], logo='#121417', logoGlow='rgba(255,61,46,0.3)',
        led='#ff3d2e', ledOff='#74777b', ledGlow='rgba(255,61,46,0.65)',
        bezel1='#222427', bezel2='#4b4e52', lcd='#a8bc86', lcdText='#16200c', lcdDim='#3a4826', rowFill=0.45, rowTint='#eef6dc',
        lcdFaint='rgba(22,32,12,0.08)', lcdGlow='rgba(0,0,0,0)',
        knobHi='#6e747d', knobLo='#101113', knobEdge='#0a0b0c', capHi='#5b6068', capLo='#1a1c1f', ridge='rgba(255,255,255,0.11)', knobLine='#f0f0f0',
        btnTop='#eceef0', btnBot='#b7babe', btnOnTop='#fff1ef', btnOnBot='#efc6c0', btnEdge='#73767b', btnShadow='#62656a', btnText='#15171a',
        btnEngrave=[0, 1, 'rgba(255,255,255,0.6)'], screw=['#e7e8ea', '#7c7f84'], well='rgba(0,0,0,0.10)', name='STUDIO GREY'),
    "purple": theme("PURPLE", True, ("#3d2b4f", "#241833", "#1a1126"), ("#35253f", "#22172c"), "#c77dff",
                    "#120a1c", "#dcb8ff", "#b08ad9", ("#4a3760", "#2b1f3a"), ("#3a2058", "#24123a"), muted="#c1aed6"),
    "yellow": theme("YELLOW", False, ("#f6dc55", "#e0c02e", "#c9a71c"), ("#f7e06a", "#dfc23a"), "#e0561b",
                    "#15130a", "#ffd84a", "#d9b33c", ("#fff3b3", "#e8cd5c"), ("#fff7d8", "#f1d880"), muted="#4a4220"),
    "gold": theme("GOLD", False, ("#e2c993", "#bf9e60", "#a27f44"), ("#dcc188", "#b99757"), "#ff9f2e",
                  "#140e05", "#ffd27a", "#dba855", ("#f3e1b6", "#cfb17a"), ("#fff2d2", "#ebcc8e"), muted="#4d3b1c"),
    "green": theme("GREEN", True, ("#21402f", "#14281d", "#0d1b13"), ("#1f382a", "#13251b"), "#4dff9a",
                   "#05140b", "#8fffc2", "#62d597", ("#2e4c3b", "#1b3125"), ("#173d29", "#0c2819"), muted="#a6ccb5"),
    "camo": theme("CAMO", True, ("#5e5d3c", "#48472d", "#3b3a25"), ("#5c5b3b", "#46452c"), "#b6ff3b",
                  "#0e1109", "#bdf56c", "#8dbf4e", ("#6c6b49", "#4e4d33"), ("#3e4b25", "#2a3418"), muted="#d8d1b0",
                  camo=["#3b3a24", "#6f6b45", "#2a2a1b", "#8a7f55"]),
    "red": theme("METALLIC RED", True, ("#8e1d24", "#601116", "#470a0f"), ("#7c1a21", "#531016"), "#ffd0d4",
                 "#170406", "#ff9aa0", "#dd6c73", ("#a5272f", "#701720"), ("#5c0f15", "#3f0a0e"), muted="#ecb6b9"),
    "silver": theme("SILVER", False, ("#f5f6f8", "#dadde1", "#c5c9ce"), ("#edeff2", "#d4d7dc"), "#3d8bff",
                    "#0a0f17", "#eaf2ff", "#a3b7d0", ("#ffffff", "#d9dce1"), ("#eef5ff", "#c9dcf7")),
    "ice": theme("ICE BLUE", False, ("#e6f3fc", "#c6def1", "#accde8"), ("#dbecf8", "#bcd7ed"), "#00b7ff",
                 "#061420", "#a6f2ff", "#74c9de", ("#f5fbff", "#c9e3f6"), ("#e6f8ff", "#b5e6f7"), text="#0f2233",
                 muted="#3a5a75"),
}

LCD_BG = {k: v["lcd"].lstrip("#") for k, v in THEME_TABLE.items()}                       # each colorway's LCD glass
LCD_INK = {k: (v["lcdText"].lstrip("#"), v["lcdDim"].lstrip("#")) for k, v in THEME_TABLE.items()}
KNOB_INK = {k: (v["text"].lstrip("#"), v["muted"].lstrip("#")) for k, v in THEME_TABLE.items()}
KNOB_INK.update(midnight=("e4e7ec", "c3cad5"), platinum=("1b1e22", "343a42"), studio=("121417", "24282d"))


def knob_items(sections):
    out = []
    for x, y, title, knobs in sections:
        out.append(dict(t="panel", x=x, y=y, w=SIDE_W, h=PANEL_H, title=title))
        for i, (key, label) in enumerate(knobs):
            r0 = y + KROW0 + (i // 2) * KPITCH
            out.append(dict(t="knob", key=key, label=label, cx=x + KCOL[i % 2], cy=r0 + 62, ly=r0 + 9, bw=124))
    return out


def page_main():
    """MAIN's items; view=0 only in BROWSE, view=1 only in LAYERS, no view: both."""
    it = knob_items(MAIN_SECTIONS)
    it.append(dict(t="lcd", bezel=BEZEL, screen=SCREEN))
    it += [dict(t="readout", key="lcd_top", box=LCD_TOP, size=LCD_PX["top"], ink="dim", align="left", weight="semibold"),
           dict(t="readout", key="lcd_ref", box=LCD_REF, size=LCD_PX["top"], ink="dim", align="right", weight="semibold"),
           dict(t="rule", x=SX + 18, y=LCD_RULE_Y, w=SW - 36),
           dict(t="lcdarrow", key="prev", box=LCD_PREV, dir=-1), dict(t="lcdarrow", key="next", box=LCD_NEXT, dir=1),
           dict(t="presetname", key="browse", box=LCD_NAME, size=LCD_PX["name"]),
           dict(t="rule", x=SX + 18, y=LCD_RULE2_Y, w=SW - 36),
           # BROWSE
           dict(t="list", key="bank", box=BANK_BOX, size=LCD_PX["bank"], view=0),
           dict(t="vrule", x=DIVIDER_X, y=AREA_Y + 4, h=LIST_H - 8, view=0),
           dict(t="list", key="row", box=PATCH_BOX, size=LCD_PX["list"], view=0),
           # LAYERS
           dict(t="wave", box=LCD_WAVE, view=1)]
    for i, l in enumerate(LAYERS):
        x = SX + 16 + (i % 2) * (CELL_W + CELL_GAP)
        y = CELL_Y + (i // 2) * (CELL_H + 8)
        it.append(dict(t="cell", x=x, y=y, w=CELL_W, h=CELL_H, letter=l.upper(), view=1))
        it.append(dict(t="readout", key="lcd_" + l, box=(x + 40, y, CELL_W - 48, CELL_H), size=LCD_PX["cell"],
                       ink="ink", align="left", weight="semibold", view=1))
        it.append(dict(t="celltoggle", key="layer_" + l, x=x, y=y, w=CELL_W, h=CELL_H, view=1))   # tap: layer on / off
    it += [dict(t="readout", key="lcd_line", box=LCD_LINE, size=LCD_PX["line"], ink="dim", align="center", weight="semibold", view=1),
           dict(t="rule", x=SX + 18, y=TAB_Y - 6, w=SW - 36),
           dict(t="lcdtabs", key="view", x=TAB_X, y=TAB_Y),
           dict(t="led", key="lfo_led", cx=LFO_LED[0], cy=LFO_LED[1])]
    for i, (key, label) in enumerate(LCD_TOGGLES):
        it.append(dict(t="lcdtoggle", key=key, label=label, x=TOGGLE_X + i * (BTN_W + BTN_GAP), y=TAB_Y))
    return it


def page_layers():
    it = []
    for i, l in enumerate(LAYERS):
        x = 8 + i * STRIP_PITCH
        it.append(dict(t="panel", x=x, y=STRIP_Y, w=STRIP_W, h=STRIP_H, title="LAYER " + l.upper()))
        bx, by, bw, bh = x + MINI[0], STRIP_Y + MINI[1], MINI[2], MINI[3]
        it.append(dict(t="minilcd", bezel=(bx, by, bw, bh), screen=(bx + 7, by + 7, bw - 14, bh - 14)))
        it.append(dict(t="readout", key="lcat_" + l, box=(bx + 18, by + 10, bw - 64, 32), size=LCD_PX["lcat"], ink="dim",
                       align="left", weight="semibold"))
        it.append(dict(t="lcdled", key="act_" + l, cx=bx + bw - 28, cy=by + 28, label=""))
        it.append(dict(t="readout", key="inst_" + l, box=(bx + 18, by + 46, bw - 34, 46), size=LCD_PX["lname"], ink="ink",
                       align="left", weight="bold"))
        bx0 = x + (STRIP_W - (2 * BTN_W + BTN_GAP)) // 2
        it.append(dict(t="button", key="inst_%s_prev" % l, label="◀  PREV", x=bx0, y=STRIP_BTN_Y[0]))
        it.append(dict(t="button", key="inst_%s_next" % l, label="NEXT  ▶", x=bx0 + BTN_W + BTN_GAP, y=STRIP_BTN_Y[0]))
        it.append(dict(t="toggle", key="layer_" + l, label="ON", x=bx0, y=STRIP_BTN_Y[1]))
        it.append(dict(t="toggle", key="solo_" + l, label="SOLO", x=bx0 + BTN_W + BTN_GAP, y=STRIP_BTN_Y[1]))
        it.append(dict(t="divider", x=x + 16, y=STRIP_KROW - 8, w=STRIP_W - 32))
        for k, (key, label) in enumerate(LAYER_KNOBS):
            r0 = STRIP_KROW + (k // 2) * STRIP_KPITCH
            it.append(dict(t="knob", key="%s_%s" % (key, l), label=label, cx=x + STRIP_KCOL[k % 2], cy=r0 + 62, ly=r0 + 9, bw=136))
    return it


def page_settings():
    it = knob_items(SETTINGS_SECTIONS)
    it.append(dict(t="about", bezel=ABOUT, screen=(ABOUT[0] + 12, ABOUT[1] + 12, ABOUT[2] - 24, ABOUT[3] - 24)))
    it.append(dict(t="panel", x=SYSTEM[0], y=SYSTEM[1], w=SYSTEM[2], h=SYSTEM[3], title="SYSTEM"))
    # THEME: its name on a small LCD, stepped with two buttons (eleven colorways don't fit as buttons)
    y = SYS_ROW_Y0
    nb = (450, y - 1, SYS_BTN_X + BTN_W - 450, BTN_H + 6)
    it.append(dict(t="themerow", label="THEME", caption="faceplate colours", lx=CX0 + 26, y=y,
                   bezel=nb, screen=(nb[0] + 5, nb[1] + 5, nb[2] - 10, nb[3] - 10)))
    it.append(dict(t="readout", key="theme", box=(nb[0] + 14, nb[1], nb[2] - 28, nb[3]), size=24, ink="ink", align="center",
                   weight="bold"))
    it.append(dict(t="button", key="theme_prev", label="◀  PREV", x=SYS_BTN_X + BTN_W + BTN_GAP, y=y))
    it.append(dict(t="button", key="theme_next", label="NEXT  ▶", x=SYS_BTN_X + 2 * (BTN_W + BTN_GAP), y=y))
    for n, (key, label, cap, opts) in enumerate(SYSTEM_ROWS):
        y = SYS_ROW_Y0 + (n + 1) * SYS_ROW_PITCH
        it.append(dict(t="sysrow", key=key, label=label, caption=cap, opts=opts, x=SYS_BTN_X, y=y, lx=CX0 + 26))
    return it


PAGES = [("MAIN", page_main), ("LAYERS", page_layers), ("SETTINGS", page_settings)]


def in_view(items, view):
    """MAIN's items for one LCD view (0: waveform and layers, 1: the preset list)."""
    return [it for it in items if it.get("view", view) == view]

# ---- what the mockups show
MOCK = {
    "MAIN": dict(
        knobs=dict(cutoff=(0.74, "6.3 KHZ"), resonance=(0.22, "22 %"), env_amt=(0.62, "+1.2 OCT"), key_track=(0.5, "50 %"),
                   attack=(0.08, "4 MS"), decay=(0.46, "820 MS"), sustain=(0.78, "78 %"), release=(0.4, "1.20 S"),
                   lfo_rate=(0.35, "3.1 HZ"), lfo_filter=(0.0, "0 %"), lfo_pitch=(0.12, "12 CT"), lfo_amp=(0.0, "0 %"),
                   chorus=(0.3, "30 %"), delay=(0.18, "18 %"), reverb=(0.42, "42 %"), volume=(0.8, "-1.9 DB")),
        text=dict(lcd_top="STRINGS  ·  034 / 128", lcd_ref="A4  440 HZ", preset="Slow Strings"),
        lists=dict(bank=dict(rows=["PIANO", "KEYS", "GUITAR", "STRINGS", "BRASS", "CHOIR", "PAD", "SYNTH", "LEAD",
                                   "PLUCK", "BASS", "BELLS"], lit=3),
                   row=dict(rows=["33  STRING ENSEMBLE", "34  SLOW STRINGS", "35  SOLO VIOLIN", "36  PIZZICATO  ★",
                                  "37  TREMOLO STRINGS", "38  SPICCATO", "39  SYNTH STRINGS", "40  STRINGS & BRASS",
                                  "41  EPIC OCTAVES", "42  PIZZ & SPIC", "43  MACHINE STRINGS", ""], lit=1)),
        on=dict(fav=0, fav_only=0, lfo_led=1), view=0),
    "MAIN_LAYERS": dict(
        text=dict(lcd_top="PIANO  ·  002 / 128  ·  ★", lcd_ref="A4  440 HZ", preset="Velvet Grand",
                  lcd_a="GRAND PIANO", lcd_b="WARM PAD", lcd_c="STRING ENSEMBLE", lcd_d="—",
                  lcd_line="Soft grand over a slow pad and wide strings"),
        on=dict(fav=1, fav_only=0, lfo_led=1), view=1, wave=(2, 1)),
    "LAYERS": dict(
        knobs=dict(level_a=(0.86, "-1.3 DB"), pan_a=(0.5, "C"), octave_a=(0.5, "0"), fine_a=(0.5, "0 CT"),
                   level_b=(0.62, "-6.9 DB"), pan_b=(0.38, "L 24"), octave_b=(0.5, "0"), fine_b=(0.54, "+8 CT"),
                   level_c=(0.55, "-9.0 DB"), pan_c=(0.62, "R 24"), octave_c=(0.667, "+1"), fine_c=(0.46, "-8 CT"),
                   level_d=(0.7, "-4.6 DB"), pan_d=(0.5, "C"), octave_d=(0.333, "-1"), fine_d=(0.5, "0 CT")),
        text=dict(lcat_a="PIANO", inst_a="GRAND PIANO", lcat_b="PAD", inst_b="WARM PAD", lcat_c="STRINGS",
                  inst_c="STRING ENSEMBLE", lcat_d="BASS  ·  OFF", inst_d="SUB BASS"),
        on=dict(layer_a=1, layer_b=1, layer_c=1, layer_d=0, solo_a=0, solo_b=0, solo_c=0, solo_d=0,
                act_a=1, act_b=1, act_c=1, act_d=0)),
    "SETTINGS": dict(
        knobs=dict(a4_ref=(0.48, "440 HZ"), tune=(0.5, "0 CT"), transpose=(0.5, "0 ST"), bend_range=(0.083, "2 ST"),
                   polyphony=(0.48, "16"), vel_sens=(0.7, "70 %"), vel_filter=(0.3, "30 %"), spread=(0.4, "40 %"),
                   delay_time=(0.43, "1/8 ."), delay_fb=(0.38, "38 %"), delay_tone=(0.6, "60 %"), delay_width=(0.8, "80 %"),
                   rev_size=(0.62, "62 %"), rev_damp=(0.45, "45 %"), rev_predelay=(0.2, "20 MS"), rev_width=(1.0, "100 %")),
        text={}, on={}, sys=dict(theme=None, anim=2, mod_wheel=0, vel_curve=1)),
}


def font_url(path):
    with open(path, "rb") as f:
        return "data:font/ttf;base64," + base64.b64encode(f.read()).decode()


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: Michroma; src: url('F_MICHROMA'); }
@font-face { font-family: Barlow; src: url('F_BARLOW'); font-weight: 600; }
@font-face { font-family: Barlow; src: url('F_BARLOW_BOLD'); font-weight: 700; }
@font-face { font-family: Mono; src: url('F_MONO'); }
@font-face { font-family: Titillium; src: url('F_TIT_R'); font-weight: 400; }
@font-face { font-family: Titillium; src: url('F_TIT_S'); font-weight: 600; }
@font-face { font-family: Titillium; src: url('F_TIT_B'); font-weight: 700; }
body { margin: 0; background: transparent; } canvas { display: block; }
</style></head><body><canvas id="c"></canvas>
<div style="font-family:Michroma">.</div><div style="font-family:Barlow;font-weight:600">.</div>
<div style="font-family:Barlow;font-weight:700">.</div><div style="font-family:Mono">.</div>
<div style="font-family:Titillium;font-weight:400">.</div><div style="font-family:Titillium;font-weight:600">.</div>
<div style="font-family:Titillium;font-weight:700">.</div>
<script>
const C = CONST_JSON;
const T = THEMES_JSON;
const cv = document.getElementById('c'); let ctx;
function setup(w, h) { cv.width = w; cv.height = h; ctx = cv.getContext('2d'); ctx.clearRect(0, 0, w, h); }
function out() { return cv.toDataURL('image/png'); }
function rr(x, y, w, h, r) { ctx.beginPath(); ctx.roundRect(x, y, w, h, r); }
function lin(y0, y1, stops) { const g = ctx.createLinearGradient(0, y0, 0, y1); stops.forEach((c, i) => g.addColorStop(i / (stops.length - 1), c)); return g; }
function text(s, x, y, o) {
  ctx.save();
  ctx.font = (o.italic ? 'italic ' : '') + (o.weight || 600) + ' ' + o.size + 'px ' + (o.font || 'Barlow');
  ctx.textAlign = o.align || 'left'; ctx.textBaseline = o.base || 'alphabetic';
  if (o.spacing) ctx.letterSpacing = o.spacing + 'px';
  if (o.engrave) { ctx.fillStyle = o.engrave[2]; ctx.fillText(s, x + o.engrave[0], y + o.engrave[1]); }
  if (o.glow) { ctx.shadowColor = o.glow; ctx.shadowBlur = o.glowBlur || 8; }
  ctx.globalAlpha = o.alpha == null ? 1 : o.alpha;
  if (o.blur) ctx.filter = 'blur(' + o.blur + 'px)';
  ctx.fillStyle = o.color; ctx.fillText(s, x, y);
  ctx.restore();
}
function noise(x, y, w, h, amt, seed) {
  const img = ctx.getImageData(x, y, w, h), d = img.data;
  let a = seed | 0;
  const rnd = () => { a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  for (let j = 0; j < h; j++) {
    const row = (rnd() - 0.5) * amt * 0.6;                 // brushed: a per-row streak plus fine grain
    for (let i = 0; i < w; i++) {
      const k = (j * w + i) * 4, n = row + (rnd() - 0.5) * amt * 0.4;
      d[k] = Math.max(0, Math.min(255, d[k] + n)); d[k + 1] = Math.max(0, Math.min(255, d[k + 1] + n)); d[k + 2] = Math.max(0, Math.min(255, d[k + 2] + n));
    }
  }
  ctx.putImageData(img, x, y);
}
function camo(t, x, y, w, h, seed, alpha) {      // soft overlapping blobs in the colorway's camo colours
  let a = seed | 0;
  const rnd = () => { a = a + 0x6D2B79F5 | 0; let q = Math.imul(a ^ a >>> 15, 1 | a); q = q + Math.imul(q ^ q >>> 7, 61 | q) ^ q; return ((q ^ q >>> 14) >>> 0) / 4294967296; };
  ctx.save(); ctx.beginPath(); ctx.rect(x, y, w, h); ctx.clip(); ctx.globalAlpha = alpha;
  const n = Math.round(w * h / 2600);
  for (let i = 0; i < n; i++) {
    ctx.fillStyle = t.camo[Math.floor(rnd() * t.camo.length)];
    const cx = x + rnd() * w, cy = y + rnd() * h, r = 14 + rnd() * 34;
    ctx.beginPath();
    for (let k = 0; k <= 10; k++) { const ang = k / 10 * 2 * Math.PI, rr2 = r * (0.65 + 0.5 * rnd()); const px = cx + Math.cos(ang) * rr2 * 1.5, py = cy + Math.sin(ang) * rr2;
      if (k === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); }
    ctx.closePath(); ctx.fill();
  }
  ctx.restore();
}
function panel(t, x, y, w, h, title) {
  ctx.save();
  ctx.shadowColor = t.drop; ctx.shadowBlur = 10; ctx.shadowOffsetY = 4;
  rr(x, y, w, h, 12); ctx.fillStyle = lin(y, y + h, [t.panel1, t.panel2]); ctx.fill();
  ctx.restore();
  if (t.camo) { ctx.save(); rr(x, y, w, h, 12); ctx.clip(); camo(t, x, y, w, h, x * 7 + y, 0.55); ctx.restore(); }
  rr(x + 0.5, y + 0.5, w - 1, h - 1, 12); ctx.strokeStyle = t.panelEdge; ctx.lineWidth = 1; ctx.stroke();
  ctx.save(); ctx.strokeStyle = t.bevelHi; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x + 12, y + 1.5); ctx.lineTo(x + w - 12, y + 1.5); ctx.stroke(); ctx.restore();
  if (title) {
    text(title, x + 16, y + 20, { size: C.TITLE_PX, font: 'Michroma', weight: 400, spacing: 3, color: t.muted, engrave: t.engrave });
    ctx.save(); ctx.font = '400 ' + C.TITLE_PX + 'px Michroma'; ctx.letterSpacing = '3px';
    const tw = ctx.measureText(title).width; ctx.restore();
    ctx.save(); ctx.strokeStyle = t.bevelHi; ctx.globalAlpha = 0.6; ctx.beginPath(); ctx.moveTo(x + 26 + tw, y + 15.5); ctx.lineTo(x + w - 16, y + 15.5); ctx.stroke(); ctx.restore();
  }
}
function screw(t, cx, cy, ang) {
  const g = ctx.createRadialGradient(cx - 2, cy - 2, 0, cx, cy, 7); g.addColorStop(0, t.screw[0]); g.addColorStop(1, t.screw[1]);
  ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = 1; ctx.stroke();
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(ang); ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(-4, -1, 8, 2); ctx.restore();
}
function logo(t, lx, ly, size) {
  const s = size / 30;
  [[0, 12, 84, 3, t.led, 0.85], [18, 22, 64, 2, t.logo, 0.6], [34, 31, 48, 2, t.led, 0.7]].forEach(([dx, dy, w, h, c, a]) => {
    const x0 = lx + (-72 + dx) * s, g = ctx.createLinearGradient(x0, 0, x0 + w * s, 0); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, c);
    ctx.globalAlpha = a; ctx.fillStyle = g; rr(x0, ly + (-34 + dy) * s, w * s, h * s, 1); ctx.fill(); ctx.globalAlpha = 1;
  });
  text('SONIX', lx - 24 * s, ly, { size: size, font: 'Michroma', weight: 400, italic: 1, spacing: 10 * s, color: t.logo, alpha: 0.18, blur: 4 * s });
  text('SONIX', lx - 12 * s, ly, { size: size, font: 'Michroma', weight: 400, italic: 1, spacing: 10 * s, color: t.logo, alpha: 0.32, blur: 2 * s });
  text('SONIX', lx, ly, { size: size, font: 'Michroma', weight: 400, italic: 1, spacing: 10 * s, color: t.logo, glow: t.logoGlow, glowBlur: 10 * s });
}
// a recessed LCD: bezel, screen, vignette, inner shadow (glass glare is drawn last, over the content)
function lcdFrame(t, bezel, screen, r, smooth) {   // smooth: the list view's glass (no scan lines, a faint vignette)
  const [bx, by, bw, bh] = bezel, [sx, sy, sw, sh] = screen;
  rr(bx, by, bw, bh, r + 8); ctx.fillStyle = lin(by, by + bh, [t.bezel1, t.bezel2]); ctx.fill();
  ctx.save(); rr(bx, by, bw, bh, r + 8); ctx.clip(); ctx.strokeStyle = 'rgba(0,0,0,0.8)'; ctx.lineWidth = 4; ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2; rr(bx - 2, by - 2, bw + 4, bh + 4, r + 10); ctx.stroke(); ctx.restore();
  rr(sx, sy, sw, sh, r); ctx.fillStyle = t.lcd; ctx.fill();
  ctx.save(); rr(sx, sy, sw, sh, r); ctx.clip();
  const vg = ctx.createRadialGradient(sx + sw / 2, sy + sh / 2, sh * 0.3, sx + sw / 2, sy + sh / 2, sw * 0.7);
  vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, smooth ? 'rgba(0,0,0,0.12)' : 'rgba(0,0,0,0.45)'); ctx.fillStyle = vg; ctx.fillRect(sx, sy, sw, sh);
  // a fine pixel grid: the LCD's dot matrix
  if (!smooth) { ctx.fillStyle = t.lcdFaint; for (let y = sy + 2; y < sy + sh; y += 3) ctx.fillRect(sx, y, sw, 1); }
  ctx.strokeStyle = 'rgba(0,0,0,0.75)'; ctx.lineWidth = 6; ctx.shadowColor = 'rgba(0,0,0,0.85)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 3; rr(sx - 3, sy - 3, sw + 6, sh + 6, r + 2); ctx.stroke();
  ctx.restore();
}
function listTiles(it) {
  const [x, y, w] = it.box, out = [];
  for (let i = 0; i < C.LIST_N; i++) out.push([x, y + i * (C.LIST_TH + C.LIST_GAP), w, C.LIST_TH]);
  return out;
}
// a button drawn on the LCD glass: an outline in the screen's dim ink; lit: tinted, framed and marked like a list row
function lcdBtn(t, x, y, w, h, label, on) {
  if (on) rowLit(t, x + 2, y + 3, w - 4, h - 6);
  else { ctx.save(); rr(x + 2.5, y + 3.5, w - 5, h - 7, 6); ctx.strokeStyle = t.lcdDim; ctx.globalAlpha = 0.7; ctx.lineWidth = 1.4; ctx.stroke(); ctx.restore(); }
  text(label, x + w / 2 + (on ? 4 : 0), y + h / 2 + 6, { size: 17, font: 'Barlow', weight: 700, spacing: 1.5, align: 'center',
    color: on ? t.lcdText : t.lcdDim });
}
function tabStrip(th, labels, w, h, gap) {          // one frame per tab lit, side by side
  const t = T[th], fw = labels.length * w + (labels.length - 1) * gap; setup(fw * labels.length, h);
  for (let f = 0; f < labels.length; f++) labels.forEach((l, i) => lcdBtn(t, f * fw + i * (w + gap), 0, w, h, l, i === f));
  return out();
}
function lcdToggle(th, w, h, label, on) { setup(w, h); lcdBtn(T[th], 0, 0, w, h, label, on); return out(); }
function rowLit(t, x, y, w, h) {   // the selected preset: a tinted bar, a bright frame and a solid marker on the left
  ctx.save(); rr(x + 1, y + 1, w - 2, h - 2, 4); ctx.fillStyle = t.rowTint || t.lcdText; ctx.globalAlpha = t.rowFill; ctx.fill(); ctx.restore();
  ctx.save(); ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 10; rr(x + 1.5, y + 1.5, w - 3, h - 3, 4); ctx.strokeStyle = t.lcdText; ctx.lineWidth = 2.5; ctx.stroke(); ctx.restore();
  ctx.save(); rr(x + 3, y + 5, 5, h - 10, 2); ctx.fillStyle = t.lcdText; ctx.fill(); ctx.restore();
}
// an idle list row: the LCD's own glass (cut from the screen behind it), so no host colour shows through
function rowOff(th, w, h) { const t = T[th]; setup(w, h); ctx.fillStyle = t.lcd; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = t.lcdFaint; for (let y = 2; y < h; y += 3) ctx.fillRect(0, y, w, 1);
  rr(0.5, 0.5, w - 1, h - 1, 4); ctx.strokeStyle = t.lcdDim; ctx.globalAlpha = 0.3; ctx.stroke(); return out(); }
function rowOn(th, w, h) { const t = T[th]; setup(w, h); rowLit(t, 0, 0, w, h); return out(); }
function glare(screen, r) {
  const [sx, sy, sw, sh] = screen;
  ctx.save(); rr(sx, sy, sw, sh, r); ctx.clip();
  const gg = ctx.createLinearGradient(sx, sy, sx + sw * 0.55, sy + sh); gg.addColorStop(0, 'rgba(255,255,255,0.09)'); gg.addColorStop(0.45, 'rgba(255,255,255,0.025)'); gg.addColorStop(0.46, 'rgba(255,255,255,0)');
  ctx.fillStyle = gg; ctx.fillRect(sx, sy, sw, sh); ctx.restore();
}
function lcdGlyph(t, s, x, y, o) { text(s, x, y, Object.assign({ color: t.lcdText, glow: t.lcdGlow, glowBlur: 6 }, o)); }
function chevron(t, cx, cy, dir, size, lit) {
  ctx.save(); ctx.beginPath(); ctx.moveTo(cx - dir * size * 0.45, cy - size); ctx.lineTo(cx + dir * size * 0.55, cy); ctx.lineTo(cx - dir * size * 0.45, cy + size);
  ctx.lineWidth = lit ? 5 : 4; ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.strokeStyle = lit ? t.lcdText : t.lcdDim;
  ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = lit ? 14 : 6; ctx.stroke(); ctx.restore();
}
function arrowBox(t, x, y, w, h, dir, lit) {
  ctx.save(); rr(x + 2, y + 3, w - 4, h - 6, 6); ctx.lineWidth = 1.2; ctx.strokeStyle = lit ? t.lcdText : t.lcdDim;
  ctx.globalAlpha = lit ? 0.9 : 0.45; if (lit) { ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 10; } ctx.stroke();
  if (lit) { ctx.globalAlpha = 0.12; ctx.fillStyle = t.lcdText; ctx.fill(); }
  ctx.restore();
  chevron(t, x + w / 2, y + h / 2, dir, 11, lit);
}

// ---------- backgrounds: one per page and theme ----------
function chassis(t, th) {
  setup(1280, 628);
  ctx.fillStyle = lin(0, 628, [t.bg1, t.bg2, t.bg3]); ctx.fillRect(0, 0, 1280, 628);
  if (t.camo) camo(t, 0, 0, 1280, 628, 11, 0.7);
  noise(0, 0, 1280, 628, th === 'midnight' ? 7 : 9, 7);
  ctx.fillStyle = t.edgeHi; ctx.fillRect(0, 0, 1280, 1);
  const ly = 36;
  logo(t, C.logoX, ly, 30);
  text('MIDI', C.led[0] + 14, C.led[1] + 5, { size: 14, spacing: 3, color: t.muted, engrave: t.engrave });
  text('CPU', C.cpu[0] - 10, C.cpu[1] + 9, { size: 14, spacing: 3, color: t.muted, engrave: t.engrave, align: 'right' });
  rr(C.cpu[0] - 1, C.cpu[1] - 1, C.cpu[2] + 2, C.cpu[3] + 2, 5); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();
  ctx.beginPath(); ctx.arc(C.led[0], C.led[1], 8, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();
}
function background(th, items) {
  const t = T[th]; chassis(t, th);
  const glares = [];
  items.forEach(it => {
    if (it.t === 'panel') panel(t, it.x, it.y, it.w, it.h, it.title);
  });
  items.forEach(it => {
    if (it.t === 'knob') {
      text(it.label, it.cx, it.ly + 6, { size: C.LABEL_PX, spacing: 1.5, color: t.text, engrave: t.engrave, align: 'center', weight: 700 });
      ctx.beginPath(); ctx.arc(it.cx, it.cy + 2, C.KNOB_R + 1, 0, 2 * Math.PI); ctx.fillStyle = t.well; ctx.fill();
    } else if (it.t === 'lcd' || it.t === 'minilcd' || it.t === 'about') {
      const r = it.t === 'minilcd' ? 4 : 6;
      const smooth = items.some(q => q.t === 'list');
      lcdFrame(t, it.bezel, it.screen, r, smooth && it.t === 'lcd'); if (!(smooth && it.t === 'lcd')) glares.push([it.screen, r]);
      if (it.t === 'about') aboutScreen(t, it.screen);
    } else if (it.t === 'rule') {
      ctx.save(); ctx.strokeStyle = t.lcdDim; ctx.globalAlpha = 0.5; ctx.beginPath(); ctx.moveTo(it.x, it.y + 0.5); ctx.lineTo(it.x + it.w, it.y + 0.5); ctx.stroke(); ctx.restore();
    } else if (it.t === 'lcdarrow') {
      arrowBox(t, ...it.box, it.dir, false);
    } else if (it.t === 'wave') {
      const [x, y, w, h] = it.box;
      ctx.save(); ctx.strokeStyle = t.lcdDim; ctx.globalAlpha = 0.28; ctx.lineWidth = 1;
      for (let i = 1; i < 8; i++) { ctx.beginPath(); ctx.moveTo(x + i * w / 8 + 0.5, y + 4); ctx.lineTo(x + i * w / 8 + 0.5, y + h - 4); ctx.stroke(); }
      ctx.globalAlpha = 0.5; ctx.setLineDash([2, 5]); ctx.beginPath(); ctx.moveTo(x, y + h / 2); ctx.lineTo(x + w, y + h / 2); ctx.stroke(); ctx.restore();
    } else if (it.t === 'cell') {
      ctx.save(); ctx.strokeStyle = t.lcdDim; ctx.lineWidth = 1.2; ctx.globalAlpha = 0.8; rr(it.x, it.y, it.w, it.h, 4); ctx.stroke();
      ctx.globalAlpha = 1; rr(it.x + 4, it.y + 4, 24, it.h - 8, 3); ctx.fillStyle = t.lcdDim; ctx.globalAlpha = 0.35; ctx.fill(); ctx.restore();
      lcdGlyph(t, it.letter, it.x + 16, it.y + it.h / 2 + 7, { size: 18, font: 'Michroma', weight: 400, align: 'center' });
    } else if (it.t === 'led') {
      ctx.beginPath(); ctx.arc(it.cx, it.cy, 8, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();
    } else if (it.t === 'lcdled') {
      if (it.label) lcdGlyph(t, it.label, it.cx + 14, it.cy + 6, { size: 15, font: 'Michroma', weight: 400, color: t.lcdDim, glow: null });
    } else if (it.t === 'vrule') {
      ctx.save(); ctx.strokeStyle = t.lcdDim; ctx.globalAlpha = 0.45; ctx.beginPath(); ctx.moveTo(it.x + 0.5, it.y); ctx.lineTo(it.x + 0.5, it.y + it.h); ctx.stroke(); ctx.restore();
    } else if (it.t === 'list') {
      // nothing: idle rows are clear, straight on the LCD glass
    } else if (it.t === 'catgrid') {
      // nothing: the grid is a picture; its panel is the BROWSE panel
    } else if (it.t === 'divider') {
      ctx.save(); ctx.strokeStyle = 'rgba(0,0,0,0.25)'; ctx.beginPath(); ctx.moveTo(it.x, it.y + 0.5); ctx.lineTo(it.x + it.w, it.y + 0.5); ctx.stroke();
      ctx.strokeStyle = t.bevelHi; ctx.beginPath(); ctx.moveTo(it.x, it.y + 1.5); ctx.lineTo(it.x + it.w, it.y + 1.5); ctx.stroke(); ctx.restore();
    } else if (it.t === 'themerow') {
      text(it.label, it.lx, it.y + 22, { size: C.LABEL_PX + 1, spacing: 1.5, color: t.text, engrave: t.engrave, weight: 700 });
      text(it.caption, it.lx, it.y + 42, { size: 14, spacing: 0.5, color: t.muted, engrave: t.engrave, weight: 600 });
      lcdFrame(t, it.bezel, it.screen, 4); glares.push([it.screen, 4]);
    } else if (it.t === 'sysrow') {
      text(it.label, it.lx, it.y + 22, { size: C.LABEL_PX + 1, spacing: 1.5, color: t.text, engrave: t.engrave, weight: 700 });
      text(it.caption, it.lx, it.y + 42, { size: 14, spacing: 0.5, color: t.muted, engrave: t.engrave, weight: 600 });
    }
  });
  glares.forEach(g => glare(...g));
  return out();
}
function aboutScreen(t, s) {
  const [sx, sy, sw, sh] = s, cx = sx + sw / 2;
  // the plugin's name and version, nothing else
  const lt = Object.assign({}, t, { logo: t.lcdText, led: t.lcdText, logoGlow: t.lcdGlow });
  ctx.save(); logo(lt, cx - 135, sy + sh / 2 + 6, 46); ctx.restore();
  lcdGlyph(t, 'VERSION ' + C.VERSION, cx, sy + sh / 2 + 52, { size: 16, font: 'Michroma', weight: 400, align: 'center', color: t.lcdDim, spacing: 3 });
}

// ---------- buttons: raised off, pressed and lit on ----------
function btnFace(t, x, y, w, h, on, label) {
  const shift = on ? 3 : 0;
  ctx.save();
  if (!on) { rr(x + 1, y + 5, w - 2, h - 6, 8); ctx.fillStyle = t.btnShadow; ctx.fill();
    ctx.shadowColor = 'rgba(0,0,0,0.35)'; ctx.shadowBlur = 8; ctx.shadowOffsetY = 5; }
  else { ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 12; }
  rr(x + 1, y + 1 + shift, w - 2, h - 6, 8);
  ctx.fillStyle = lin(y + shift, y + h - 5 + shift, on ? [t.btnOnTop, t.btnOnBot] : [t.btnTop, t.btnBot]); ctx.fill();
  ctx.restore();
  rr(x + 1.5, y + 1.5 + shift, w - 3, h - 7, 8); ctx.strokeStyle = on ? t.led : t.btnEdge; ctx.lineWidth = 1; ctx.stroke();
  if (!on) { ctx.beginPath(); ctx.moveTo(x + 10, y + 2.5); ctx.lineTo(x + w - 10, y + 2.5); ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.stroke(); }
  else { ctx.save(); rr(x + 1, y + 1 + shift, w - 2, h - 6, 8); ctx.clip(); ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2;
    ctx.strokeStyle = 'rgba(0,0,0,0.5)'; ctx.lineWidth = 3; rr(x - 1, y - 1 + shift, w + 2, h - 2, 9); ctx.stroke(); ctx.restore(); }
  const lx = x + 14, ly = y + (h - 5) / 2 + 1 + shift;
  ctx.save(); ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, 2 * Math.PI);
  if (on) { const g = ctx.createRadialGradient(lx - 1, ly - 1, 0, lx, ly, 5); g.addColorStop(0, '#ffffff'); g.addColorStop(0.5, t.led); g.addColorStop(1, t.led);
    ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 10; ctx.fillStyle = g; }
  else ctx.fillStyle = t.ledOff;
  ctx.fill(); ctx.restore();
  ctx.save(); ctx.font = '700 ' + C.BUTTON_PX + 'px Barlow'; ctx.letterSpacing = '1.2px'; const lw = ctx.measureText(label).width; ctx.restore();
  const sp = lw > w - 32 ? 0.2 : 1.2;                                  // long labels (PLATINUM) close up to clear the LED
  text(label, x + w / 2 + 8, ly + 6, { size: C.BUTTON_PX, spacing: sp, color: t.btnText, engrave: t.btnEngrave, align: 'center', weight: 700 });
}
function button(th, w, h, on, label) { const t = T[th]; setup(w, h + 4); btnFace(t, 0, 0, w, h, on, label); return out(); }
function blank(w, h) { setup(w, h); return out(); }
// a row or grid of buttons with one lit (frames side by side: one per option)
function gridStrip(th, labels, cols, w, h, gap, rowGap) {
  const t = T[th], rows = Math.ceil(labels.length / cols), fw = cols * w + (cols - 1) * gap, fh = rows * (h + rowGap) - rowGap + 4;
  setup(fw * labels.length, fh);
  for (let f = 0; f < labels.length; f++) gridFrame(t, f * fw, 0, labels, cols, w, h, gap, rowGap, f);
  return out();
}
function gridFrame(t, x0, y0, labels, cols, w, h, gap, rowGap, lit) {
  labels.forEach((c, i) => btnFace(t, x0 + (i % cols) * (w + gap), y0 + Math.floor(i / cols) * (h + rowGap), w, h, i === lit, c));
}
function pressOverlay(th, w, h) {
  const t = T[th]; setup(w, h + 4);
  rr(1, 4, w - 2, h - 6, 8); ctx.fillStyle = 'rgba(0,0,0,0.22)'; ctx.fill();
  rr(1.5, 4.5, w - 3, h - 7, 8); ctx.strokeStyle = t.led; ctx.lineWidth = 2; ctx.stroke();
  return out();
}
function arrowLit(th, w, h, dir) { const t = T[th]; setup(w, h); arrowBox(t, 0, 0, w, h, dir, true); return out(); }

// ---------- the knob: 3D body, knurled grip, cap and pointer inside an LED ring lit to the value ----------
function knobFrame(t, cx, cy, r, v) {
  const a0 = 0.75 * Math.PI, a1 = 2.25 * Math.PI, dots = 25;
  for (let i = 0; i < dots; i++) {
    const fr = i / (dots - 1), a = a0 + (a1 - a0) * fr, x = cx + (r + 1) * Math.cos(a), y = cy + (r + 1) * Math.sin(a);
    ctx.save(); ctx.beginPath(); ctx.arc(x, y, 2.6, 0, 2 * Math.PI);
    if (fr <= v + 1e-6) { ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 6; ctx.fillStyle = t.led; } else ctx.fillStyle = t.ledOff;
    ctx.fill(); ctx.restore();
  }
  const R = r - 9;
  ctx.save(); ctx.beginPath(); ctx.arc(cx + 2, cy + 5, R, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.5)'; ctx.filter = 'blur(3px)'; ctx.fill(); ctx.restore();
  let g = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.45, 0, cx, cy, R * 1.25); g.addColorStop(0, t.knobHi); g.addColorStop(1, t.knobLo);
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill(); ctx.strokeStyle = t.knobEdge; ctx.lineWidth = 1.5; ctx.stroke();
  const rot = (a0 + (a1 - a0) * v);
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot); ctx.strokeStyle = t.ridge; ctx.lineWidth = 4; ctx.lineCap = 'round';
  for (let k = 0; k < 36; k++) { ctx.beginPath(); ctx.arc(0, 0, R - 3, k * Math.PI / 18, k * Math.PI / 18 + 0.06); ctx.stroke(); }
  ctx.restore();
  const Cc = R * 0.74;
  g = ctx.createLinearGradient(0, cy - Cc, 0, cy + Cc); g.addColorStop(0, t.capHi); g.addColorStop(1, t.capLo);
  ctx.beginPath(); ctx.arc(cx, cy, Cc, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,0.45)'; ctx.lineWidth = 1; ctx.stroke();
  ctx.beginPath(); ctx.ellipse(cx - 3, cy - Cc * 0.45, Cc * 0.55, Cc * 0.25, 0, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(255,255,255,0.12)'; ctx.fill();
  ctx.beginPath(); ctx.moveTo(cx + Math.cos(rot) * Cc * 0.3, cy + Math.sin(rot) * Cc * 0.3); ctx.lineTo(cx + Math.cos(rot) * Cc * 0.92, cy + Math.sin(rot) * Cc * 0.92);
  ctx.strokeStyle = t.knobLine; ctx.lineWidth = 3.5; ctx.lineCap = 'round'; ctx.stroke();
}
function knobStrip(th, frames, r) {
  const t = T[th], S = 2 * (r + 5); setup(S, S * frames);
  for (let f = 0; f < frames; f++) knobFrame(t, S / 2, f * S + S / 2, r, f / (frames - 1));
  return out();
}

// ---------- LCD pictures ----------
// the waveform: frame 0 idle (the baked graticule shows), then level a (0..3) x phase p (0..3): 1 + a * 4 + p.
// A moving trace with a fading afterimage of the previous phase, drawn across the whole width, cut in two halves.
function waveTrace(t, x0, y0, w, h, a, p, alpha, width) {
  const A = [0.16, 0.26, 0.36, 0.45][a] * h, ph = p / 4 * 2 * Math.PI;
  ctx.save(); ctx.beginPath();
  for (let x = 0; x <= w; x += 2) {
    const q = x / w, env = Math.sin(Math.PI * Math.min(1, q * 1.15)) ** 0.6;
    const y = Math.sin(q * 26 - ph) * 0.62 + Math.sin(q * 61 - 2 * ph + 1.3) * 0.25 + Math.sin(q * 9 + ph) * 0.18;
    const yy = y0 + h / 2 - A * env * y;
    if (x === 0) ctx.moveTo(x0 + x, yy); else ctx.lineTo(x0 + x, yy);
  }
  ctx.globalAlpha = alpha; ctx.strokeStyle = t.lcdText; ctx.lineWidth = width; ctx.lineJoin = 'round'; ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 7; ctx.stroke();
  ctx.restore();
}
function waveFrame(t, x0, y0, w, h, f) {
  if (f === 0) return;
  const a = Math.floor((f - 1) / 4), p = (f - 1) % 4;
  waveTrace(t, x0, y0, w, h, a, (p + 3) % 4, 0.22, 1.6);
  waveTrace(t, x0, y0, w, h, a, p, 1, 2.2);
}
function waveStrip(th, w, h, half) {
  const t = T[th], hw = w / 2; setup(hw * C.WAVE_FRAMES, h);
  for (let f = 1; f < C.WAVE_FRAMES; f++) {
    ctx.save(); ctx.beginPath(); ctx.rect(f * hw, 0, hw, h); ctx.clip();
    waveFrame(t, f * hw - half * hw, 0, w, h, f); ctx.restore();
  }
  return out();
}
function ledStrip(th, d, lcd) {
  const t = T[th]; setup(d * 2, d);
  for (let f = 0; f < 2; f++) {
    const cx = f * d + d / 2, cy = d / 2; ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, d / 2 - 4, 0, 2 * Math.PI);
    const on = lcd ? t.lcdText : t.led, glow = lcd ? t.lcdGlow : t.ledGlow;
    if (f) { const g = ctx.createRadialGradient(cx - 1.5, cy - 1.5, 0, cx, cy, d / 2 - 3); g.addColorStop(0, lcd ? on : '#ffffff'); g.addColorStop(0.5, on); g.addColorStop(1, on);
      ctx.shadowColor = glow; ctx.shadowBlur = 8; ctx.fillStyle = g; }
    else { ctx.fillStyle = lcd ? t.lcdDim : t.ledOff; ctx.globalAlpha = lcd ? 0.3 : 1; }
    ctx.fill(); ctx.restore();
  }
  return out();
}
function cpuStrip(th, w, h) {
  const t = T[th]; setup(w * 11, h);
  for (let f = 0; f < 11; f++) {
    rr(f * w, 0, w, h, h / 2); ctx.fillStyle = t.ledOff; ctx.fill();
    if (f) { ctx.save(); rr(f * w, 0, w * f / 10, h, h / 2); ctx.fillStyle = t.led; ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 6; ctx.fill(); ctx.restore(); }
  }
  return out();
}

// ---------- the mockup: everything MPC would draw, with sample values ----------
function mockup(th, items, m) {
  const t = T[th];
  const bg = new Image(); bg.src = background(th, items);
  return new Promise(res => bg.onload = () => {
    setup(1280, 628); ctx.drawImage(bg, 0, 0);
    const ink = C.LCD_INK[th], kink = C.KNOB_INK[th];
    items.forEach(it => {
      if (it.t === 'knob') {
        const [v, s] = m.knobs[it.key] || [0, ''];
        knobFrame(t, it.cx, it.cy, C.KNOB_R, v);
        text(s, it.cx, it.cy + C.KNOB_R + 2 + 13 + C.VALUE_PX * 0.33, { size: C.VALUE_PX, font: 'Titillium', weight: 600, color: '#' + kink[1], align: 'center' });
      } else if (it.t === 'readout') {
        const s = (m.text || {})[it.key]; if (s == null) return;
        const [x, y, w, h] = it.box, ax = it.align === 'left' ? x : it.align === 'right' ? x + w : x + w / 2;
        text(s, ax, y + h / 2 + it.size * 0.36, { size: it.size, font: 'Titillium', weight: it.weight === 'bold' ? 700 : 600,
          color: '#' + (it.ink === 'ink' ? ink[0] : ink[1]), align: it.align });
      } else if (it.t === 'presetname') {
        const [x, y, w, h] = it.box;
        text((m.text || {}).preset || '', x + w / 2, y + h / 2 + it.size * 0.36, { size: it.size, font: 'Titillium', weight: 600, color: '#' + ink[0], align: 'center' });
      } else if (it.t === 'list' && m.lists && m.lists[it.key]) {
        const L = m.lists[it.key];
        listTiles(it).forEach(([x, y, w, h], i) => {
          if (i === L.lit) rowLit(t, x, y, w, h);
          if (L.rows[i]) text(L.rows[i], x + 16, y + h / 2 + it.size * 0.36, { size: it.size, font: 'Titillium', weight: 600, color: '#' + ink[0] });
        });
      } else if (it.t === 'lcdtabs') {
        C.VIEWS.forEach((l, i) => lcdBtn(t, it.x + i * (C.BTN_W + C.BTN_GAP), it.y, C.BTN_W, C.BTN_H + 4, l, i === (m.view || 0)));
      } else if (it.t === 'lcdtoggle') {
        lcdBtn(t, it.x, it.y, C.BTN_W, C.BTN_H + 4, it.label, (m.on || {})[it.key]);
      } else if (it.t === 'led') {
        const on = (m.on || {})[it.key];
        ctx.save(); ctx.beginPath(); ctx.arc(it.cx, it.cy, 7, 0, 2 * Math.PI); ctx.fillStyle = on ? t.led : t.ledOff;
        if (on) { ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 8; } ctx.fill(); ctx.restore();
      } else if (it.t === 'button' || it.t === 'toggle') {
        btnFace(t, it.x, it.y, C.BTN_W, C.BTN_H, (m.on || {})[it.key] ? 1 : 0, it.label);
      } else if (it.t === 'catgrid') {
        gridFrame(t, it.x, it.y, C.CATS, 6, C.BTN_W, C.BTN_H, C.BTN_GAP, 10, m.category);
      } else if (it.t === 'wave' && m.wave) {
        const [x, y, w, h] = it.box; waveFrame(t, x, y, w, h, 1 + m.wave[0] * 4 + m.wave[1]);
      } else if (it.t === 'lcdled') {
        const on = (m.on || {})[it.key];
        ctx.save(); ctx.beginPath(); ctx.arc(it.cx, it.cy, 5, 0, 2 * Math.PI);
        if (on) { ctx.fillStyle = t.lcdText; ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 8; } else { ctx.fillStyle = t.lcdDim; ctx.globalAlpha = 0.3; }
        ctx.fill(); ctx.restore();
      } else if (it.t === 'sysrow') {
        const sel = it.key === 'theme' ? C.THEMES.indexOf(th) : m.sys[it.key];
        gridFrame(t, it.x, it.y, it.opts, 3, C.BTN_W, C.BTN_H, C.BTN_GAP, 0, sel);
      }
    });
    // header live bits
    ctx.save(); ctx.beginPath(); ctx.arc(C.led[0], C.led[1], 7, 0, 2 * Math.PI); ctx.fillStyle = t.led; ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 8; ctx.fill(); ctx.restore();
    rr(C.cpu[0], C.cpu[1], C.cpu[2], C.cpu[3], C.cpu[3] / 2); ctx.fillStyle = t.ledOff; ctx.fill();
    rr(C.cpu[0], C.cpu[1], C.cpu[2] * 0.3, C.cpu[3], C.cpu[3] / 2); ctx.fillStyle = t.led; ctx.fill();
    items.forEach(it => { if (it.t === 'lcd' || it.t === 'minilcd') glare(it.screen, it.t === 'minilcd' ? 4 : 6); });
    res(out());
  });
}
window.API = { background, button, blank, gridStrip, pressOverlay, arrowLit, knobStrip, waveStrip, ledStrip, cpuStrip, rowOn, rowOff, tabStrip, lcdToggle, mockup };
</script></body></html>"""


def save(data_url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data_url.split(",", 1)[1]))


ABOUT_LINES = [
    "128 factory presets  ·  4 layers  ·  filter, LFO, chorus, delay, reverb",
    "Recorded sounds: VSCO 2 Community Edition, Versilian Community Sample Library,",
    "Meta Open Sound (all CC0) and the Salamander Grand Piano by Alexander Holm (CC BY 3.0).",
    "Synth sounds rendered by Sonix. Full credits in LICENSES.md.",
]


def open_page(p, with_mpc_fonts):
    consts = dict(VERSION=VERSION, TITLE_PX=TITLE_PX, LABEL_PX=LABEL_PX, VALUE_PX=VALUE_PX, BUTTON_PX=BUTTON_PX,
                  KNOB_R=KNOB_R, BTN_W=BTN_W, BTN_H=BTN_H, BTN_GAP=BTN_GAP, CATS=CATS, THEMES=THEMES,
                  WAVE_FRAMES=WAVE_FRAMES, LIST_N=LIST_N, LIST_TH=LIST_TH, LIST_GAP=LIST_GAP, VIEWS=VIEWS, led=HEADER_LED, cpu=CPU_BAR, logoX=LOGO_X, LCD_INK=LCD_INK, KNOB_INK=KNOB_INK, ABOUT=ABOUT_LINES)
    page = (PAGE.replace("CONST_JSON", json.dumps(consts)).replace("THEMES_JSON", json.dumps(THEME_TABLE))
            .replace("F_MICHROMA", font_url(os.path.join(FONTS, "Michroma-Regular.ttf")))
            .replace("F_BARLOW_BOLD", font_url(os.path.join(FONTS, "BarlowSemiCondensed-Bold.ttf")))
            .replace("F_BARLOW", font_url(os.path.join(FONTS, "BarlowSemiCondensed-SemiBold.ttf")))
            .replace("F_MONO", font_url(os.path.join(FONTS, "ShareTechMono-Regular.ttf"))))
    for k, f in (("F_TIT_R", "TitilliumWeb-Regular.ttf"), ("F_TIT_S", "TitilliumWeb-SemiBold.ttf"), ("F_TIT_B", "TitilliumWeb-Bold.ttf")):
        path = os.path.join(MPC_FONTS, f)
        page = page.replace(k, font_url(path) if with_mpc_fonts and os.path.exists(path) else "")
    b = p.chromium.launch()
    pg = b.new_page()
    pg.set_content(page)
    pg.evaluate("document.fonts.ready")
    pg.wait_for_timeout(400)
    return b, pg


def mockups(outdir):
    os.makedirs(outdir, exist_ok=True)
    with sync_playwright() as p:
        b, pg = open_page(p, True)
        pages = [("MAIN", in_view(page_main(), 0)), ("MAIN_LAYERS", in_view(page_main(), 1)),
                 ("LAYERS", page_layers()), ("SETTINGS", page_settings())]
        for name, items in pages:
            for th in THEMES:
                url = pg.evaluate("API.mockup(%s, %s, %s)" % (json.dumps(th), json.dumps(items), json.dumps(dict({"knobs": MOCK["MAIN"]["knobs"]}, **dict(MOCK[name], text=dict(MOCK[name].get("text", {}), theme=THEME_TABLE[th]["name"].title()))))))
                save(url, os.path.join(outdir, "%s_%s.png" % (name.lower(), th)))
        b.close()


def export_art():
    """Every picture the layout uses, per theme, into vst/art/<theme>/ (plus a few shared transparent ones)."""
    pages = [(name, fn()) for name, fn in PAGES]
    lw = LCD_WAVE[2]
    with sync_playwright() as p:
        b, pg = open_page(p, False)
        ev = lambda js, *a: pg.evaluate("API.%s(%s)" % (js, ", ".join(json.dumps(x) for x in a)))
        for th in THEMES:
            d = os.path.join(ART, th)
            for name, items in pages:
                if name == "MAIN":
                    save(ev("background", th, in_view(items, 0)), os.path.join(d, "bg_main.png"))
                    save(ev("background", th, in_view(items, 1)), os.path.join(d, "bg_main_list.png"))
                else:
                    save(ev("background", th, items), os.path.join(d, "bg_%s.png" % name.lower()))
            # list rows: the skin builder flattens row pictures onto a solid colour, so a "clear" row is cut from this
            # colorway's own LCD (a middle row of the list view), and the lit row is that cut plus the highlight
            from PIL import Image
            tw = list_tw(THEMES.index(th))
            bgl = Image.open(os.path.join(d, "bg_main_list.png")).convert("RGBA")
            x0, y0 = LIST_BOX[0], LIST_BOX[1] + 2 * (LIST_TH + LIST_GAP)
            glass = bgl.crop((x0, y0, x0 + tw, y0 + LIST_TH))
            glass.save(os.path.join(d, "row_off.png"))
            save(ev("rowOn", th, tw, LIST_TH), os.path.join(d, "row_on_overlay.png"))
            lit = glass.copy()
            ov = Image.open(os.path.join(d, "row_on_overlay.png")).convert("RGBA")
            lit.alpha_composite(ov)
            lit.save(os.path.join(d, "row_on.png"))
            os.remove(os.path.join(d, "row_on_overlay.png"))
            save(ev("knobStrip", th, 128, KNOB_R), os.path.join(d, "knob.png"))
            save(ev("gridStrip", th, CATS, 6, BTN_W, BTN_H, BTN_GAP, 10), os.path.join(d, "cats.png"))
            save(ev("pressOverlay", th, BTN_W, BTN_H), os.path.join(d, "press.png"))
            for key, label in [(k, l) for kind, k, l in ROW3] + [("lay_on", "ON"), ("solo", "SOLO"),
                                                                 ("lprev", "◀  PREV"), ("lnext", "NEXT  ▶")]:
                for on in (0, 1):
                    save(ev("button", th, BTN_W, BTN_H, on, label), os.path.join(d, "%s_%s.png" % (key, "on" if on else "off")))
            save(ev("arrowLit", th, BTN_W, BTN_H + 4, -1), os.path.join(d, "arrow_prev.png"))
            save(ev("arrowLit", th, BTN_W, BTN_H + 4, 1), os.path.join(d, "arrow_next.png"))
            for key, _, _, opts in SYSTEM_ROWS:
                save(ev("gridStrip", th, opts, 3, BTN_W, BTN_H, BTN_GAP, 0), os.path.join(d, "sys_%s.png" % key))
            save(ev("waveStrip", th, lw, LCD_WAVE[3], 0), os.path.join(d, "wave_l.png"))
            save(ev("waveStrip", th, lw, LCD_WAVE[3], 1), os.path.join(d, "wave_r.png"))
            save(ev("ledStrip", th, 22, False), os.path.join(d, "led.png"))
            save(ev("ledStrip", th, 18, True), os.path.join(d, "lcdled.png"))
            save(ev("cpuStrip", th, CPU_BAR[2], CPU_BAR[3]), os.path.join(d, "cpu.png"))
        save(ev("blank", BTN_W, BTN_H + 4), os.path.join(ART, "clear_btn.png"))
        save(ev("blank", BTN_W + BTN_GAP, BTN_H + 4), os.path.join(ART, "clear_seg.png"))
        save(ev("blank", 20, 20 * 128), os.path.join(ART, "clear_knob.png"))
        save(ev("blank", (LIST_BOX[2] - (LIST_COLS - 1) * LIST_GAP) // LIST_COLS, LIST_TH), os.path.join(ART, "clear_row.png"))
        b.close()


def write_layout():
    Y = Y_OFF
    L = ["# Sonix faceplate. Generated by tools/make_skin.py (edit that, not this file).",
         "art_css=skin.css",
         "theme_bg=16181c", "theme_ink=e4e7ec", "theme_ink_dim=98a1af", "theme_accent=4cc3ff", "theme_accent_hi=4cc3ff",
         "theme_lcd=06121f", "theme_line=2e323a", "theme_box=06121f",
         "qlinks_track = " + ",".join(MAIN_QLINKS)]
    T = list(enumerate(THEMES))

    def header(n, th):
        L.append("meter cx=%d cy=%d w=22 h=22 key=midi_led strip=art/%s/led.png frames=2 when=theme:%d" % (HEADER_LED[0], HEADER_LED[1] + Y, th, n))
        L.append("meter cx=%d cy=%d w=%d h=%d key=cpu strip=art/%s/cpu.png frames=11 when=theme:%d" % (
            CPU_BAR[0] + CPU_BAR[2] // 2, CPU_BAR[1] + CPU_BAR[3] // 2 + Y, CPU_BAR[2], CPU_BAR[3], th, n))

    def readout(it, n, th):
        ink, dim = LCD_INK[th]
        x, y, w, h = it["box"]
        L.append('readout cx=%d cy=%d w=%d h=%d label="" key=%s box=0 tsize=%d tcolor=%s tweight=%s talign=%s when=theme:%d' % (
            x + w // 2, y + h // 2 + Y, w, h, it["key"], it["size"], ink if it["ink"] == "ink" else dim, it["weight"], it["align"], n))

    def knob(it, n, th):
        ink, dim = KNOB_INK[th]
        L.append('knob cx=%d cy=%d r=%d label="%s" key=%s ns=0 vs=%d bw=%d ink=%s ink_dim=%s strip=art/%s/knob.png frames=128 when=theme:%d' % (
            it["cx"], it["cy"] + Y, KNOB_R, it["label"], it["key"], VALUE_PX, it["bw"], ink, dim, th, n))

    def pic_button(kind, key, img, x, y, n, th, extra=""):
        cx, cy = x + BTN_W // 2, y + (BTN_H + 4) // 2 + Y
        if kind == "toggle":
            L.append('toggle cx=%d cy=%d w=%d h=%d label="" key=%s ns=0 bw=%d img=art/%s/%s_off.png img_on=art/%s/%s_on.png when=theme:%d' % (
                cx, cy, BTN_W, BTN_H + 4, key, BTN_W, th, img, th, img, n))
        else:
            L.append('button cx=%d cy=%d w=%d h=%d label="" key=%s img=art/%s/%s_off.png img_on=art/%s/%s_on.png when=theme:%d' % (
                cx, cy, BTN_W, BTN_H + 4, key, th, img, th, img, n))

    def lcdled(it, n, th):
        L.append("meter cx=%d cy=%d w=18 h=18 key=%s strip=art/%s/lcdled.png frames=2 when=theme:%d" % (it["cx"], it["cy"] + Y, it["key"], th, n))

    # ---- MAIN: per theme, two LCD views (the waveform and layers, or the preset list), each one "look" option
    L.append("[tab MAIN]")
    for n, th in T:
        for view in (0, 1):
            look = n * 2 + view
            start = len(L)
            items = in_view(page_main(), view)
            L.append("art file=art/%s/bg_main%s.png x=0 y=%d w=1280 h=628 fit=stretch when=theme:%d" % (
                th, "_list" if view else "", Y, n))
            header(n, th)
            if not view:
                wx, wy, ww, wh = LCD_WAVE
                for half, nm in ((0, "wave_l"), (1, "wave_r")):
                    L.append("meter cx=%d cy=%d w=%d h=%d key=wave strip=art/%s/%s.png frames=%d when=theme:%d" % (
                        wx + ww // 4 + half * ww // 2, wy + wh // 2 + Y, ww // 2, wh, th, nm, WAVE_FRAMES, n))
            gw, gh = 6 * BTN_W + 5 * BTN_GAP, 2 * (BTN_H + 10) - 10 + 4
            L.append("meter cx=%d cy=%d w=%d h=%d key=category strip=art/%s/cats.png frames=%d when=theme:%d" % (
                GRID_X + gw // 2, GRID_Y + gh // 2 + Y, gw, gh, th, len(CATS), n))
            for it in items:
                if it["t"] == "readout":
                    readout(it, n, th)
                elif it["t"] == "lcdled":
                    lcdled(it, n, th)
                elif it["t"] == "led":
                    L.append("meter cx=%d cy=%d w=22 h=22 key=%s strip=art/%s/led.png frames=2 when=theme:%d" % (it["cx"], it["cy"] + Y, it["key"], th, n))
                elif it["t"] == "lcdarrow":
                    x, y, w, h = it["box"]
                    L.append('button cx=%d cy=%d w=%d h=%d label="" key=%s img=art/clear_btn.png img_on=art/%s/arrow_%s.png when=theme:%d' % (
                        x + w // 2, y + h // 2 + Y, w, h, it["key"], th, it["key"], n))
                elif it["t"] == "presetname":
                    # the preset name is a knob on the preset list: touch it, then the data wheel (or a drag) browses
                    x, y, w, h = it["box"]
                    L.append('knob cx=%d cy=%d r=5 lay=side bw=%d bh=%d vs=%d label="" key=browse ink=%s strip=art/clear_knob.png frames=128 when=theme:%d' % (
                        x + w // 2 - 12, y + h // 2 + Y, w, h, it["size"], LCD_INK[th][0], n))
                elif it["t"] == "list":
                    x, y, w, h = it["box"]
                    L.append('list x=%d y=%d w=%d h=%d key=row cols=%d rows=%d order=cols th=%d gap=%d tsize=%d tcolor=%s tweight=semibold talign=left tx=16 img=art/%s/row_off.png img_on=art/%s/row_on.png when=theme:%d' % (
                        x, y + Y, w - 2 * n, h, LIST_COLS, LIST_ROWS, LIST_TH, LIST_GAP, LCD_PX["list"], LCD_INK[th][0], th, th, n))
                elif it["t"] in ("toggle", "button"):
                    pic_button(it["t"], it["key"], it["key"], it["x"], it["y"], n, th)
                elif it["t"] == "knob":
                    knob(it, n, th)
            for i, key in enumerate(CAT_KEYS):
                cx = GRID_X + (i % 6) * (BTN_W + BTN_GAP) + BTN_W // 2
                cy = GRID_Y + (i // 6) * (BTN_H + 10) + (BTN_H + 4) // 2 + Y
                L.append('button cx=%d cy=%d w=%d h=%d label="" key=%s img=art/clear_btn.png img_on=art/%s/press.png when=theme:%d' % (
                    cx, cy, BTN_W, BTN_H + 4, key, th, n))
            for k in range(start, len(L)):
                L[k] = L[k].replace("when=theme:%d" % n, "when=look:%d" % look)
    L.append('qlinks "MAIN" = ' + ",".join(MAIN_QLINKS))

    # ---- LAYERS
    L.append("[tab LAYERS]")
    items = page_layers()
    for n, th in T:
        L.append("art file=art/%s/bg_layers.png x=0 y=%d w=1280 h=628 fit=stretch when=theme:%d" % (th, Y, n))
    for n, th in T:
        header(n, th)
        for it in items:
            if it["t"] == "readout":
                readout(it, n, th)
            elif it["t"] == "lcdled":
                lcdled(it, n, th)
            elif it["t"] in ("toggle", "button"):
                img = {"layer": "lay_on", "solo": "solo", "inst": None}[it["key"].split("_")[0]]
                if img is None:
                    img = "lprev" if it["key"].endswith("_prev") else "lnext"
                pic_button(it["t"], it["key"], img, it["x"], it["y"], n, th)
            elif it["t"] == "knob":
                knob(it, n, th)
    L.append('qlinks "LAYERS" = ' + ",".join("%s_%s" % (k, l) for l in LAYERS for k, _ in LAYER_KNOBS))

    # ---- SETTINGS
    L.append("[tab SETTINGS]")
    items = page_settings()
    for n, th in T:
        L.append("art file=art/%s/bg_settings.png x=0 y=%d w=1280 h=628 fit=stretch when=theme:%d" % (th, Y, n))
    for n, th in T:
        header(n, th)
        for it in items:
            if it["t"] == "knob":
                knob(it, n, th)
            elif it["t"] == "sysrow":
                w3 = 3 * BTN_W + 2 * BTN_GAP
                L.append("meter cx=%d cy=%d w=%d h=%d key=%s strip=art/%s/sys_%s.png frames=3 when=theme:%d" % (
                    it["x"] + w3 // 2, it["y"] + (BTN_H + 4) // 2 + Y, w3, BTN_H + 4, it["key"], th, it["key"], n))
                L.append('enum_h cx=%d cy=%d label="" key=%s sw=%d sh=%d img=art/clear_seg.png img_on=art/clear_seg.png when=theme:%d' % (
                    it["x"] + (3 * (BTN_W + BTN_GAP)) // 2, it["y"] + (BTN_H + 4) // 2 + Y, it["key"], BTN_W + BTN_GAP, BTN_H + 4, n))
    L.append('qlinks "SETTINGS" = ' + ",".join(k for _, _, _, ks in SETTINGS_SECTIONS for k, _ in ks))
    with open(os.path.join(VST, "layout.conf"), "w") as f:
        f.write("\n".join(L) + "\n")


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--mockup":
        mockups(sys.argv[2])
        return
    import shutil
    for th in THEMES:                                  # start clean: the v0.2 pictures are not used any more
        shutil.rmtree(os.path.join(ART, th), ignore_errors=True)
    for f in ("clear_btn.png", "clear_cell.png", "clear_seg.png", "clear_knob.png", "clear_row.png"):
        if os.path.exists(os.path.join(ART, f)):
            os.remove(os.path.join(ART, f))
    export_art()
    write_layout()


if __name__ == "__main__":
    main()
