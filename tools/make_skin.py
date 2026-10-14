#!/usr/bin/env python3
"""Draws the Sonix faceplate in its three colorways and writes the matching layout, from one set of coordinates.

    python3 tools/make_skin.py            (needs Playwright's Chromium; run on a PC, not in the plugin build)

Writes vst/art/... (PNGs) and vst/layout.conf; both are committed, so the plugin build only reads them.
The design is the approved Sonix UI mockup (Midnight, Platinum, Studio Grey), refitted to MPC's 1280 x 628
plugin area: header with the motion-blur logo, MIDI LED and CPU meter; the category buttons; the LCD; browse and
layer buttons; two rows of LED-ring knobs.

What is static (chassis, panels, engraved names, the LCD's glass and frame) is one background per theme. What
moves is a control: knobs (a filmstrip per theme, LED ring lit to the value), toggles and buttons (off / on
pictures), the category grid (one picture per lit category), the LCD wave, the two LEDs and the CPU meter
(filmstrips the engine drives), and MPC's own live text on the LCD. The engine changes its pictures only on
events, never per audio block (every repaint costs MPC's screen thread: mpc-vst-plugins docs/NOTES.md).

Coordinates below are the plugin area (1280 x 628); layout.conf's are 86 px lower (MPC's header).
"""
import base64
import json
import os

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
VST = os.path.join(HERE, "..", "vst")
ART = os.path.join(VST, "art")
FONTS = os.path.join(VST, "fonts")
Y_OFF = 86

THEMES = ["midnight", "platinum", "studio"]                 # index = the "theme" parameter's option
THEME_NAMES = ["MIDNIGHT", "PLATINUM", "STUDIO GREY"]
CATS = ["PIANO", "KEYS", "PAD", "STRINGS", "BRASS", "CHOIR", "LEAD", "PLUCK", "BASS", "BELLS", "SYNTH", "FX"]
CAT_KEYS = ["cat_" + c.lower() for c in CATS]

BTN_W, BTN_H, BTN_GAP = 108, 47, 8                          # every button is this size (big-hands rule)
MID_Y, MID_H = 52, 246                                      # the middle band: categories, LCD, browse
CAT_PANEL = (8, MID_Y, 364, MID_H)
GRID_X, GRID_Y = 8 + 12, MID_Y + 30                         # the 3 x 4 category grid's top-left
RIGHT_PANEL = (1020, MID_Y, 252, MID_H)
RIGHT_X = (1020 + 14, 1020 + 14 + BTN_W + BTN_GAP)
BEZEL = (380, MID_Y, 632, MID_H)
SCREEN = (392, MID_Y + 12, 608, MID_H - 24)
SX, SY = SCREEN[0] + 14, SCREEN[1]                           # LCD content origin
LCD_TOP = (SX, SY + 8, 580, 22)                              # x, y, w, h of the top line
LCD_TAG = (SX, SY + 40, 110, 34)                             # category tag box
LCD_NAME = (SX + 122, SY + 34, 458, 46)
LCD_WAVE = (SX, SY + 86, 580, 58)
LCD_CELLS_Y, LCD_CELL_H, LCD_CELL_W, LCD_CELL_GAP = SY + 150, 30, 139, 8
LCD_LINE = (SX, SY + 188, 580, 24)

ROW_Y = (304, 466)                                           # the two knob rows (each 160 tall)
KNOB_R = 40                                                  # the builder's knob box is 2r + 10
ROW1_SLOT, ROW2_SLOT = 178, 156
ROW1 = [("FILTER", [("cutoff", "CUTOFF"), ("resonance", "RESONANCE"), ("env_amt", "ENV AMT")]),
        ("AMP ENVELOPE", [("attack", "ATTACK"), ("decay", "DECAY"), ("sustain", "SUSTAIN"), ("release", "RELEASE")])]
ROW2 = [("LFO", [("lfo_rate", "RATE"), ("lfo_depth", "DEPTH")]),
        ("EFFECTS", [("chorus", "CHORUS"), ("delay", "DELAY"), ("reverb", "REVERB")]),
        ("MASTER", [("tune", "TUNE"), ("a4_ref", "A4 REF"), ("volume", "VOLUME")])]
QLINKS = ["cutoff", "resonance", "env_amt", "attack", "decay", "sustain", "release", "browse",
          "lfo_rate", "lfo_depth", "chorus", "delay", "reverb", "tune", "a4_ref", "volume"]

# the right column: (kind, key, label, col, row)
RIGHT = [("button", "prev", "◀  PREV", 0, 0), ("button", "next", "NEXT  ▶", 1, 0),
         ("toggle", "layer_a", "LAYER A", 0, 1), ("toggle", "layer_b", "LAYER B", 1, 1),
         ("toggle", "layer_c", "LAYER C", 0, 2), ("toggle", "layer_d", "LAYER D", 1, 2),
         ("toggle", "fav", "★  FAV", 0, 3), ("button", "theme_next", "THEME", 1, 3)]
HEADER_LED = (884, 22)                                       # MIDI LED centre
LFO_LED_DX = 52                                              # the LFO LED sits right of the LFO panel title
CPU_BAR = (1044, 17, 96, 10)

# live LCD text colours per theme (MPC draws it in Titillium Web)
LCD_INK = {"midnight": ("8fdcff", "5f9cbd"), "platinum": ("ffb648", "c38c3f"), "studio": ("16200c", "3b4b27")}
KNOB_INK = {"midnight": "e4e7ec", "platinum": "1b1e22", "studio": "121417"}


def knob_positions():
    out = []
    x = 8
    for title, knobs in ROW1:
        w = ROW1_SLOT * len(knobs)
        out.append(("panel", title, x, ROW_Y[0], w))
        for i, (k, l) in enumerate(knobs):
            out.append(("knob", k, l, x + ROW1_SLOT * (i + 0.5), ROW_Y[0] + 90, ROW1_SLOT - 12))
        x += w + 8
    x = 8
    for title, knobs in ROW2:
        w = ROW2_SLOT * len(knobs)
        out.append(("panel", title, x, ROW_Y[1], w))
        for i, (k, l) in enumerate(knobs):
            out.append(("knob", k, l, x + ROW2_SLOT * (i + 0.5), ROW_Y[1] + 84, ROW2_SLOT - 12))
        x += w + 8
    return out


def font_url(name):
    with open(os.path.join(FONTS, name), "rb") as f:
        return "data:font/ttf;base64," + base64.b64encode(f.read()).decode()


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face { font-family: Michroma; src: url('F_MICHROMA'); }
@font-face { font-family: Barlow; src: url('F_BARLOW'); font-weight: 600; }
@font-face { font-family: Barlow; src: url('F_BARLOW_BOLD'); font-weight: 700; }
@font-face { font-family: Mono; src: url('F_MONO'); }
body { margin: 0; background: transparent; } canvas { display: block; }
</style></head><body><canvas id="c"></canvas>
<div style="font-family:Michroma">.</div><div style="font-family:Barlow;font-weight:600">.</div>
<div style="font-family:Barlow;font-weight:700">.</div><div style="font-family:Mono">.</div>
<script>
const G = GEOM_JSON;
const T = {
  midnight: { bg1:'#2b2e35', bg2:'#16181c', bg3:'#111216', edgeHi:'rgba(255,255,255,0.08)', edgeLo:'rgba(0,0,0,0.6)',
    panel1:'#262a32', panel2:'#1a1d22', panelEdge:'#0b0c0f', bevelHi:'rgba(255,255,255,0.09)', bevelLo:'rgba(0,0,0,0.5)', drop:'rgba(0,0,0,0.55)',
    text:'#e4e7ec', muted:'#98a1af', engrave:[0,-1,'rgba(0,0,0,0.75)'], logo:'#eef1f5', logoGlow:'rgba(76,195,255,0.6)',
    led:'#4cc3ff', ledOff:'#1d2732', ledGlow:'rgba(76,195,255,0.7)',
    bezel1:'#0b0c0f', bezel2:'#22252c', lcd:'#06121f', lcdText:'#8fdcff', lcdDim:'#3d6a85', lcdGlow:'rgba(120,210,255,0.5)',
    knobHi:'#5a616d', knobLo:'#0a0b0d', knobEdge:'#050607', capHi:'#454a54', capLo:'#16181c', ridge:'rgba(255,255,255,0.10)', knobLine:'#eef1f5',
    btnTop:'#3b404a', btnBot:'#22252b', btnOnTop:'#16293c', btnOnBot:'#0e1b28', btnEdge:'#0b0c0f', btnShadow:'#07080a', btnText:'#d9dde4',
    btnEngrave:[0,-1,'rgba(0,0,0,0.75)'], screw:['#70767f','#2a2d33'], name:'MIDNIGHT' },
  platinum: { bg1:'#eef0f3', bg2:'#c9cdd2', bg3:'#b2b6bc', edgeHi:'rgba(255,255,255,0.85)', edgeLo:'rgba(0,0,0,0.25)',
    panel1:'#e4e7eb', panel2:'#c7cbd1', panelEdge:'#9aa0a7', bevelHi:'rgba(255,255,255,0.9)', bevelLo:'rgba(0,0,0,0.14)', drop:'rgba(0,0,0,0.22)',
    text:'#16191d', muted:'#434a54', engrave:[0,1,'rgba(255,255,255,0.8)'], logo:'#16191d', logoGlow:'rgba(255,154,31,0.4)',
    led:'#ff9a1f', ledOff:'#9f9b93', ledGlow:'rgba(255,154,31,0.7)',
    bezel1:'#7c8188', bezel2:'#c3c7cd', lcd:'#1a1206', lcdText:'#ffb648', lcdDim:'#7d5a25', lcdGlow:'rgba(255,170,60,0.5)',
    knobHi:'#7c828c', knobLo:'#121417', knobEdge:'#0c0d0f', capHi:'#686e77', capLo:'#1e2125', ridge:'rgba(255,255,255,0.13)', knobLine:'#f4f4f4',
    btnTop:'#fbfbfc', btnBot:'#cdd1d6', btnOnTop:'#fff5e6', btnOnBot:'#efd2ad', btnEdge:'#939880', btnShadow:'#858a91', btnText:'#1f2328',
    btnEngrave:[0,1,'rgba(255,255,255,0.85)'], screw:['#ffffff','#999da4'], name:'PLATINUM' },
  studio: { bg1:'#c3c5c8', bg2:'#a4a7ab', bg3:'#909397', edgeHi:'rgba(255,255,255,0.55)', edgeLo:'rgba(0,0,0,0.3)',
    panel1:'#b8bbbf', panel2:'#9ea1a5', panelEdge:'#7a7d82', bevelHi:'rgba(255,255,255,0.6)', bevelLo:'rgba(0,0,0,0.16)', drop:'rgba(0,0,0,0.26)',
    text:'#121417', muted:'#2a2e33', engrave:[0,1,'rgba(255,255,255,0.5)'], logo:'#121417', logoGlow:'rgba(255,61,46,0.3)',
    led:'#ff3d2e', ledOff:'#74777b', ledGlow:'rgba(255,61,46,0.65)',
    bezel1:'#222427', bezel2:'#4b4e52', lcd:'#a8bc86', lcdText:'#16200c', lcdDim:'#6f805a', lcdGlow:'rgba(0,0,0,0)',
    knobHi:'#6e747d', knobLo:'#101113', knobEdge:'#0a0b0c', capHi:'#5b6068', capLo:'#1a1c1f', ridge:'rgba(255,255,255,0.11)', knobLine:'#f0f0f0',
    btnTop:'#eceef0', btnBot:'#b7babe', btnOnTop:'#fff1ef', btnOnBot:'#efc6c0', btnEdge:'#73767b', btnShadow:'#62656a', btnText:'#15171a',
    btnEngrave:[0,1,'rgba(255,255,255,0.6)'], screw:['#e7e8ea','#7c7f84'], name:'STUDIO GREY' },
};
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
function panel(t, x, y, w, h, title, titleX) {
  ctx.save();
  ctx.shadowColor = t.drop; ctx.shadowBlur = 10; ctx.shadowOffsetY = 4;
  rr(x, y, w, h, 12); ctx.fillStyle = lin(y, y + h, [t.panel1, t.panel2]); ctx.fill();
  ctx.restore();
  rr(x + 0.5, y + 0.5, w - 1, h - 1, 12); ctx.strokeStyle = t.panelEdge; ctx.lineWidth = 1; ctx.stroke();
  ctx.save(); rr(x + 1.5, y + 1.5, w - 3, h - 3, 11); ctx.strokeStyle = t.bevelHi; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x + 12, y + 1.5); ctx.lineTo(x + w - 12, y + 1.5); ctx.stroke(); ctx.restore();
  if (title) text(title, titleX || x + 14, y + 17, { size: 10, font: 'Michroma', weight: 400, spacing: 3, color: t.muted, engrave: t.engrave });
}
function screw(t, cx, cy, ang) {
  const g = ctx.createRadialGradient(cx - 2, cy - 2, 0, cx, cy, 7); g.addColorStop(0, t.screw[0]); g.addColorStop(1, t.screw[1]);
  ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill();
  ctx.strokeStyle = 'rgba(0,0,0,0.35)'; ctx.lineWidth = 1; ctx.stroke();
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(ang); ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(-4, -1, 8, 2); ctx.restore();
}

// ---------- the background: chassis, header, panels, LCD frame, engraved names ----------
function background(th) {
  const t = T[th]; setup(1280, 628);
  ctx.fillStyle = lin(0, 628, [t.bg1, t.bg2, t.bg3]); ctx.fillRect(0, 0, 1280, 628);
  noise(0, 0, 1280, 628, th === 'midnight' ? 7 : 9, 7);
  ctx.fillStyle = t.edgeHi; ctx.fillRect(0, 0, 1280, 1);
  screw(t, 10, 10, 0.6); screw(t, 1270, 10, -0.3); screw(t, 10, 618, 1.2); screw(t, 1270, 618, 0.2);

  // header: motion-blur logo, subtitle, MIDI and CPU labels, theme name
  const lx = 92, ly = 36;
  [[0, 12, 84, 3, t.led, 0.85], [18, 22, 64, 2, t.logo, 0.6], [34, 31, 48, 2, t.led, 0.7]].forEach(([dx, dy, w, h, c, a]) => {
    const g = ctx.createLinearGradient(lx - 72 + dx, 0, lx - 72 + dx + w, 0); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, c);
    ctx.globalAlpha = a; ctx.fillStyle = g; rr(lx - 72 + dx, ly - 34 + dy, w, h, 1); ctx.fill(); ctx.globalAlpha = 1;
  });
  text('SONIX', lx - 24, ly, { size: 30, font: 'Michroma', weight: 400, italic: 1, spacing: 10, color: t.logo, alpha: 0.18, blur: 4 });
  text('SONIX', lx - 12, ly, { size: 30, font: 'Michroma', weight: 400, italic: 1, spacing: 10, color: t.logo, alpha: 0.32, blur: 2 });
  text('SONIX', lx, ly, { size: 30, font: 'Michroma', weight: 400, italic: 1, spacing: 10, color: t.logo, glow: t.logoGlow, glowBlur: 10 });
  text('ROM SYNTHESIZER', 318, ly - 2, { size: 14, spacing: 5, color: t.muted, engrave: t.engrave });
  text('MIDI', G.led[0] + 14, G.led[1] + 5, { size: 14, spacing: 3, color: t.muted, engrave: t.engrave });
  text('CPU', G.cpu[0] - 10, G.cpu[1] + 9, { size: 14, spacing: 3, color: t.muted, engrave: t.engrave, align: 'right' });
  rr(G.cpu[0] - 1, G.cpu[1] - 1, G.cpu[2] + 2, G.cpu[3] + 2, 5); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();
  text(t.name, 1266, ly - 2, { size: 14, spacing: 3, color: t.muted, engrave: t.engrave, align: 'right' });
  ctx.beginPath(); ctx.arc(G.led[0], G.led[1], 8, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();

  // middle band
  panel(t, ...G.catPanel, 'CATEGORY');
  panel(t, ...G.rightPanel, 'BROWSE · LAYERS');
  // the LCD: recessed bezel, screen, glass, and its fixed frame lines
  const [bx, by, bw, bh] = G.bezel, [sx, sy, sw, sh] = G.screen;
  rr(bx, by, bw, bh, 14); ctx.fillStyle = lin(by, by + bh, [t.bezel1, t.bezel2]); ctx.fill();
  ctx.save(); rr(bx, by, bw, bh, 14); ctx.clip(); ctx.strokeStyle = 'rgba(0,0,0,0.8)'; ctx.lineWidth = 4; ctx.shadowColor = 'rgba(0,0,0,0.8)'; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2; rr(bx - 2, by - 2, bw + 4, bh + 4, 16); ctx.stroke(); ctx.restore();
  rr(sx, sy, sw, sh, 6); ctx.fillStyle = t.lcd; ctx.fill();
  ctx.save(); rr(sx, sy, sw, sh, 6); ctx.clip();
  const vg = ctx.createRadialGradient(sx + sw / 2, sy + sh / 2, sh * 0.3, sx + sw / 2, sy + sh / 2, sw * 0.7);
  vg.addColorStop(0, 'rgba(0,0,0,0)'); vg.addColorStop(1, 'rgba(0,0,0,0.45)'); ctx.fillStyle = vg; ctx.fillRect(sx, sy, sw, sh);
  ctx.strokeStyle = 'rgba(0,0,0,0.75)'; ctx.lineWidth = 6; ctx.shadowColor = 'rgba(0,0,0,0.85)'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 3; rr(sx - 3, sy - 3, sw + 6, sh + 6, 8); ctx.stroke();
  ctx.restore();
  // fixed LCD graphics: the category tag box, the layer cells, divider lines
  ctx.save(); ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 6; ctx.strokeStyle = t.lcdText; ctx.lineWidth = 1.2;
  rr(...G.tag, 3); ctx.stroke();
  ctx.strokeStyle = t.lcdDim;
  for (let i = 0; i < 4; i++) { rr(G.cellsX[i], G.cellsY, G.cellW, G.cellH, 3); ctx.stroke(); }
  ctx.setLineDash([2, 5]); ctx.beginPath(); ctx.moveTo(G.wave[0], G.wave[1] + G.wave[3] / 2); ctx.lineTo(G.wave[0] + G.wave[2], G.wave[1] + G.wave[3] / 2); ctx.stroke();
  ctx.restore();
  // glass glare over the upper left of the screen
  ctx.save(); rr(sx, sy, sw, sh, 6); ctx.clip();
  const gg = ctx.createLinearGradient(sx, sy, sx + sw * 0.55, sy + sh); gg.addColorStop(0, 'rgba(255,255,255,0.10)'); gg.addColorStop(0.45, 'rgba(255,255,255,0.03)'); gg.addColorStop(0.46, 'rgba(255,255,255,0)');
  ctx.fillStyle = gg; ctx.fillRect(sx, sy, sw, sh); ctx.restore();

  // knob panels, their titles and engraved names; wells under every knob
  G.knobs.forEach(k => {
    if (k[0] === 'panel') panel(t, k[2], k[3], k[4], 160, k[1]);
  });
  G.knobs.forEach(k => {
    if (k[0] !== 'knob') return;
    text(k[2], k[3], k[4] - 48, { size: 14, spacing: 2, color: t.text, engrave: t.engrave, align: 'center', weight: 700 });
    ctx.beginPath(); ctx.arc(k[3], k[4] + 2, 41, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.12)'; ctx.fill();
  });
  // the LFO LED's socket
  ctx.beginPath(); ctx.arc(G.lfoLed[0], G.lfoLed[1], 8, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(0,0,0,0.35)'; ctx.fill();
  return out();
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
  // LED dot
  const lx = x + 15, ly = y + (h - 5) / 2 + 1 + shift;
  ctx.save(); ctx.beginPath(); ctx.arc(lx, ly, 4.5, 0, 2 * Math.PI);
  if (on) { const g = ctx.createRadialGradient(lx - 1, ly - 1, 0, lx, ly, 5); g.addColorStop(0, '#ffffff'); g.addColorStop(0.5, t.led); g.addColorStop(1, t.led);
    ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 10; ctx.fillStyle = g; }
  else ctx.fillStyle = t.ledOff;
  ctx.fill(); ctx.restore();
  text(label, x + w / 2 + 7, ly + 5, { size: 15, spacing: 1.5, color: t.btnText, engrave: t.btnEngrave, align: 'center', weight: 700 });
}
function button(th, w, h, on, label) { const t = T[th]; setup(w, h + 4); btnFace(t, 0, 0, w, h, on, label); return out(); }
function blank(w, h) { setup(w, h); return out(); }
// the category grid with one category lit (frames side by side: one per category)
function catStrip(th, cats, cols, w, h, gap) {
  const t = T[th], rows = Math.ceil(cats.length / cols), fw = cols * w + (cols - 1) * gap, fh = rows * (h + gap) - gap + 4;
  setup(fw * cats.length, fh);
  for (let f = 0; f < cats.length; f++)
    cats.forEach((c, i) => btnFace(t, f * fw + (i % cols) * (w + gap), Math.floor(i / cols) * (h + gap), w, h, i === f, c));
  return out();
}
// the overlay a category button shows while pressed
function pressOverlay(th, w, h) {
  const t = T[th]; setup(w, h + 4);
  rr(1, 4, w - 2, h - 6, 8); ctx.fillStyle = 'rgba(0,0,0,0.22)'; ctx.fill();
  rr(1.5, 4.5, w - 3, h - 7, 8); ctx.strokeStyle = t.led; ctx.lineWidth = 2; ctx.stroke();
  return out();
}

// ---------- the knob: 3D body, knurled grip, cap and pointer inside an LED ring lit to the value ----------
function knobStrip(th, frames, r) {
  const t = T[th], S = 2 * (r + 5); setup(S, S * frames);
  const a0 = 0.75 * Math.PI, a1 = 2.25 * Math.PI, dots = 25;
  for (let f = 0; f < frames; f++) {
    const v = f / (frames - 1), cx = S / 2, cy = f * S + S / 2;
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
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot); ctx.strokeStyle = t.ridge; ctx.lineWidth = 4;
    for (let k = 0; k < 36; k++) { ctx.beginPath(); ctx.arc(0, 0, R - 3, k * Math.PI / 18, k * Math.PI / 18 + 0.06); ctx.stroke(); }
    ctx.restore();
    const C = R * 0.74;
    g = ctx.createLinearGradient(0, cy - C, 0, cy + C); g.addColorStop(0, t.capHi); g.addColorStop(1, t.capLo);
    ctx.beginPath(); ctx.arc(cx, cy, C, 0, 2 * Math.PI); ctx.fillStyle = g; ctx.fill(); ctx.strokeStyle = 'rgba(0,0,0,0.45)'; ctx.lineWidth = 1; ctx.stroke();
    ctx.beginPath(); ctx.ellipse(cx - 3, cy - C * 0.45, C * 0.55, C * 0.25, 0, 0, 2 * Math.PI); ctx.fillStyle = 'rgba(255,255,255,0.12)'; ctx.fill();
    ctx.beginPath(); ctx.moveTo(cx + Math.cos(rot) * C * 0.3, cy + Math.sin(rot) * C * 0.3); ctx.lineTo(cx + Math.cos(rot) * C * 0.92, cy + Math.sin(rot) * C * 0.92);
    ctx.strokeStyle = t.knobLine; ctx.lineWidth = 3.5; ctx.lineCap = 'round'; ctx.stroke();
  }
  return out();
}

// ---------- LCD pictures ----------
function waveStrip(th, w, h) {
  const t = T[th]; setup(w * 9, h);
  for (let f = 0; f < 9; f++) {
    const x0 = f * w; if (f === 0) continue;               // frame 0: idle (the baked dashed centre line shows)
    const shape = Math.floor((f - 1) / 2), big = (f - 1) % 2, A = (big ? 0.42 : 0.24) * h;
    ctx.save(); ctx.beginPath(); ctx.rect(x0, 0, w, h); ctx.clip();
    ctx.beginPath();
    for (let x = 0; x <= w; x += 2) {
      const p = x / w; let y;
      if (shape === 0) y = Math.exp(-p * 3.2) * (Math.sin(p * 46) + 0.35 * Math.sin(p * 131));
      else if (shape === 1) y = Math.exp(-p * 1.6) * Math.sin(p * 30) * (0.8 + 0.2 * Math.cos(p * 7));
      else if (shape === 2) y = Math.sin(p * 18);
      else y = 0.7 * Math.sin(p * 24) * Math.cos(p * 5.1) + 0.3 * Math.sin(p * 97) * Math.sin(p * 13);
      const yy = h / 2 - A * y;
      if (x === 0) ctx.moveTo(x0 + x, yy); else ctx.lineTo(x0 + x, yy);
    }
    ctx.strokeStyle = t.lcdText; ctx.lineWidth = 2.2; ctx.lineJoin = 'round'; ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 6; ctx.stroke();
    ctx.restore();
  }
  return out();
}
function ledStrip(th, d) {
  const t = T[th]; setup(d * 2, d);
  for (let f = 0; f < 2; f++) {
    const cx = f * d + d / 2, cy = d / 2; ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, d / 2 - 4, 0, 2 * Math.PI);
    if (f) { const g = ctx.createRadialGradient(cx - 1.5, cy - 1.5, 0, cx, cy, d / 2 - 3); g.addColorStop(0, '#ffffff'); g.addColorStop(0.5, t.led); g.addColorStop(1, t.led);
      ctx.shadowColor = t.ledGlow; ctx.shadowBlur = 8; ctx.fillStyle = g; } else ctx.fillStyle = t.ledOff;
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
function cellOn(th, w, h) {
  const t = T[th]; setup(w, h);
  ctx.save(); ctx.shadowColor = t.lcdGlow; ctx.shadowBlur = 8; rr(1, 1, w - 2, h - 2, 3); ctx.strokeStyle = t.lcdText; ctx.lineWidth = 2; ctx.stroke(); ctx.restore();
  rr(1, 1, w - 2, h - 2, 3); ctx.fillStyle = th === 'studio' ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)'; ctx.fill();
  return out();
}
window.API = { background, button, blank, catStrip, pressOverlay, knobStrip, waveStrip, ledStrip, cpuStrip, cellOn };
</script></body></html>"""


def save(data_url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(base64.b64decode(data_url.split(",", 1)[1]))


def main():
    knobs = knob_positions()
    lfo_panel = next(k for k in knobs if k[0] == "panel" and k[1] == "LFO")
    cells_x = [SX + i * (LCD_CELL_W + LCD_CELL_GAP) for i in range(4)]
    geom = dict(catPanel=CAT_PANEL, rightPanel=RIGHT_PANEL, bezel=BEZEL, screen=SCREEN, tag=LCD_TAG, wave=LCD_WAVE,
                cellsX=cells_x, cellsY=LCD_CELLS_Y, cellW=LCD_CELL_W, cellH=LCD_CELL_H, led=HEADER_LED, cpu=CPU_BAR,
                lfoLed=(lfo_panel[2] + LFO_LED_DX + 22, lfo_panel[3] + 13), knobs=knobs)
    page = (PAGE.replace("GEOM_JSON", json.dumps(geom))
            .replace("F_MICHROMA", font_url("Michroma-Regular.ttf"))
            .replace("F_BARLOW_BOLD", font_url("BarlowSemiCondensed-Bold.ttf"))
            .replace("F_BARLOW", font_url("BarlowSemiCondensed-SemiBold.ttf"))
            .replace("F_MONO", font_url("ShareTechMono-Regular.ttf")))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(page)
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(300)
        lbl = {k: l for _, k, l, _, _ in RIGHT}
        for th in THEMES:
            d = os.path.join(ART, th)
            save(pg.evaluate("API.background(%s)" % json.dumps(th)), os.path.join(d, "bg.png"))
            save(pg.evaluate("API.knobStrip(%s, 128, %d)" % (json.dumps(th), KNOB_R)), os.path.join(d, "knob.png"))
            save(pg.evaluate("API.catStrip(%s, %s, 3, %d, %d, %d)" % (json.dumps(th), json.dumps(CATS), BTN_W, BTN_H, BTN_GAP)),
                 os.path.join(d, "cats.png"))
            save(pg.evaluate("API.pressOverlay(%s, %d, %d)" % (json.dumps(th), BTN_W, BTN_H)), os.path.join(d, "press.png"))
            for kind, key, label, _, _ in RIGHT:
                for on in (0, 1):
                    save(pg.evaluate("API.button(%s, %d, %d, %d, %s)" % (json.dumps(th), BTN_W, BTN_H, on, json.dumps(label))),
                         os.path.join(d, "%s_%s.png" % (key, "on" if on else "off")))
            save(pg.evaluate("API.waveStrip(%s, %d, %d)" % (json.dumps(th), LCD_WAVE[2], LCD_WAVE[3])), os.path.join(d, "wave.png"))
            save(pg.evaluate("API.ledStrip(%s, 22)" % json.dumps(th)), os.path.join(d, "led.png"))
            save(pg.evaluate("API.cpuStrip(%s, %d, %d)" % (json.dumps(th), CPU_BAR[2], CPU_BAR[3])), os.path.join(d, "cpu.png"))
            save(pg.evaluate("API.cellOn(%s, %d, %d)" % (json.dumps(th), LCD_CELL_W, LCD_CELL_H)), os.path.join(d, "cell_on.png"))
        save(pg.evaluate("API.blank(%d, %d)" % (BTN_W, BTN_H + 4)), os.path.join(ART, "clear_btn.png"))
        save(pg.evaluate("API.blank(%d, %d)" % (LCD_CELL_W, LCD_CELL_H)), os.path.join(ART, "clear_cell.png"))
        b.close()
    write_layout(knobs, lfo_panel, cells_x)


def write_layout(knobs, lfo_panel, cells_x):
    Y = Y_OFF
    L = ["# Sonix faceplate. Generated by tools/make_skin.py (edit that, not this file).",
         "art_css=skin.css",
         "theme_bg=16181c", "theme_ink=e4e7ec", "theme_ink_dim=98a1af", "theme_accent=4cc3ff", "theme_accent_hi=4cc3ff",
         "theme_lcd=06121f", "theme_line=2e323a", "theme_box=06121f",
         "qlinks_track = " + ",".join(QLINKS),
         "[tab SONIX]"]
    for n, th in enumerate(THEMES):
        L.append("art file=art/%s/bg.png x=0 y=%d w=1280 h=628 fit=stretch when=theme:%d" % (th, Y, n))
    # header pictures
    for n, th in enumerate(THEMES):
        L.append("meter cx=%d cy=%d w=22 h=22 key=midi_led strip=art/%s/led.png frames=2 when=theme:%d" % (HEADER_LED[0], HEADER_LED[1] + Y, th, n))
        L.append("meter cx=%d cy=%d w=%d h=%d key=cpu strip=art/%s/cpu.png frames=11 when=theme:%d" % (
            CPU_BAR[0] + CPU_BAR[2] // 2, CPU_BAR[1] + CPU_BAR[3] // 2 + Y, CPU_BAR[2], CPU_BAR[3], th, n))
        L.append("meter cx=%d cy=%d w=22 h=22 key=lfo_led strip=art/%s/led.png frames=2 when=theme:%d" % (
            lfo_panel[2] + LFO_LED_DX + 22, lfo_panel[3] + 13 + Y, th, n))
    # the category grid: the lit picture, then a touch button per category
    gw = 3 * BTN_W + 2 * BTN_GAP
    gh = 4 * (BTN_H + BTN_GAP) - BTN_GAP + 4
    for n, th in enumerate(THEMES):
        L.append("meter cx=%d cy=%d w=%d h=%d key=category strip=art/%s/cats.png frames=12 when=theme:%d" % (
            GRID_X + gw // 2, GRID_Y + gh // 2 + Y, gw, gh, th, n))
    for i, key in enumerate(CAT_KEYS):
        cx = GRID_X + (i % 3) * (BTN_W + BTN_GAP) + BTN_W // 2
        cy = GRID_Y + (i // 3) * (BTN_H + BTN_GAP) + (BTN_H + 4) // 2 + Y
        for n, th in enumerate(THEMES):
            L.append('button cx=%d cy=%d w=%d h=%d label="" key=%s img=art/clear_btn.png img_on=art/%s/press.png when=theme:%d' % (
                cx, cy, BTN_W, BTN_H + 4, key, th, n))
    # the right column
    for kind, key, label, col, row in RIGHT:
        cx = RIGHT_X[col] + BTN_W // 2
        cy = GRID_Y + row * (BTN_H + BTN_GAP) + (BTN_H + 4) // 2 + Y
        for n, th in enumerate(THEMES):
            if kind == "toggle":
                L.append('toggle cx=%d cy=%d w=%d h=%d label="" key=%s ns=0 bw=%d img=art/%s/%s_off.png img_on=art/%s/%s_on.png when=theme:%d' % (
                    cx, cy, BTN_W, BTN_H + 4, key, BTN_W, th, key, th, key, n))
            else:
                L.append('button cx=%d cy=%d w=%d h=%d label="" key=%s img=art/%s/%s_off.png img_on=art/%s/%s_on.png when=theme:%d' % (
                    cx, cy, BTN_W, BTN_H + 4, key, th, key, th, key, n))
    # the LCD: wave picture, live text, the layer cells (tap one to edit that layer)
    for n, th in enumerate(THEMES):
        ink, dim = LCD_INK[th]
        L.append("meter cx=%d cy=%d w=%d h=%d key=wave strip=art/%s/wave.png frames=9 when=theme:%d" % (
            LCD_WAVE[0] + LCD_WAVE[2] // 2, LCD_WAVE[1] + LCD_WAVE[3] // 2 + Y, LCD_WAVE[2], LCD_WAVE[3], th, n))
        def ro(key, box, size, color, align="left", weight="normal"):
            x, y, w, h = box
            L.append('readout cx=%d cy=%d w=%d h=%d label="" key=%s box=0 tsize=%d tcolor=%s tweight=%s talign=%s when=theme:%d' % (
                x + w // 2, y + h // 2 + Y, w, h, key, size, color, weight, align, n))
        ro("lcd_top", LCD_TOP, 15, dim)
        ro("lcd_cat", LCD_TAG, 15, ink, "center", "bold")
        ro("lcd_name", LCD_NAME, 30, ink, "left", "bold")
        for i, key in enumerate(["lcd_a", "lcd_b", "lcd_c", "lcd_d"]):
            ro(key, (cells_x[i] + 8, LCD_CELLS_Y, LCD_CELL_W - 12, LCD_CELL_H), 13, ink)
        ro("lcd_line", LCD_LINE, 15, dim)
        seg = LCD_CELL_W + LCD_CELL_GAP
        L.append("enum_h cx=%d cy=%d label=\"\" key=edit_layer sw=%d sh=%d img=art/clear_cell.png img_on=art/%s/cell_on.png when=theme:%d" % (
            SX + (4 * seg - LCD_CELL_GAP) // 2, LCD_CELLS_Y + LCD_CELL_H // 2 + Y, seg, LCD_CELL_H, th, n))
    # knobs
    for k in knobs:
        if k[0] != "knob":
            continue
        _, key, label, cx, cy, bw = k
        for n, th in enumerate(THEMES):
            L.append('knob cx=%d cy=%d r=%d label="%s" key=%s ns=0 vs=19 bw=%d ink=%s strip=art/%s/knob.png frames=128 when=theme:%d' % (
                cx, cy + Y, KNOB_R, label, key, bw, KNOB_INK[th], th, n))
    L.append('qlinks "SONIX" = ' + ",".join(QLINKS))
    with open(os.path.join(VST, "layout.conf"), "w") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
