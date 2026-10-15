/* Sonix: a ROMpler for MPC OS (mpc_engine, see mpc-vst-plugins wrapper/engine.h).
 *
 * Four layers (A-D), each playing one of the packed instruments (engine/<name>.snx, built by tools/pack_sonix.py),
 * with its own level, pan, octave, fine tune, on and solo. A note finds its zone by key and velocity in every layer
 * that plays, is repitched from the zone's root, runs through a 12 dB low-pass (CUTOFF, RESONANCE, ENV AMT, KEY TRACK,
 * velocity, LFO, mod wheel) and an ADSR; the LFO also gives vibrato and tremolo. Then chorus, a tempo-synced delay
 * and reverb. 128 factory presets (src/presets.h, tools/presets.py) are MPC's PRESET list (vst.json "programs").
 *
 * Samples stay compressed in RAM and every voice decodes its own sample as it plays (the lossless codec is cheap to
 * run forward), so the whole library (~115 MB) is resident and any preset plays at once, with no decode-on-pick
 * pause. A looped voice jumps back to its loop start through a decoder state saved when the bank loaded. One loader
 * thread reads every bank in the background when the first Sonix opens, the ones the current preset uses first;
 * the banks are shared by every Sonix instance in the process.
 *
 * The faceplate's pictures (the LCD waveform, LEDs, CPU bar, lit category) change only on events, or for the
 * waveform at most every 100 ms while notes sound (ANIMATION setting), because every repaint costs MPC's screen
 * thread (mpc-vst-plugins docs/NOTES.md, "Animation is expensive").
 *
 * The codec and the effects are adapted from Fretty (3dthc/fretty-mpc, MIT). */
#define _GNU_SOURCE
#include <math.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "engine.h"
#include "instruments.h"
#include "params.h"
#include "presets.h"

#define SR 44100.0f
#define MAX_EV 256
#define NLAYER 4
#define MAX_POLY 32
#define MAX_V (MAX_POLY * NLAYER)
#define RING 2048                    /* decoded frames a voice keeps: enough for 15x speed over a 128-frame block */
#define PI_F 3.14159265f

/* ---------- sample file (written by tools/pack_sonix.py) ----------
 * header (32 bytes): "SNXB", u32 version 1, u32 samples, u32 zones, u32 data offset, u32 flags (1 = mono), f32 level
 * trim, u32 sample rate (0 = 44100); per sample { u32 byte offset into data, u32 frames, u32 loop start, u32 loop end
 * (0 = none) }; per zone zone_t; then the data. Each sample: u32 len0, u32 len1, then one or two bitstreams (left,
 * right; mono has one). A stream is 1024-frame blocks: a header byte (order << 5 | k), then one Rice code per frame (a
 * run of q zeros, a 1, k low bits; 24 zeros = a raw 20-bit value) of the zigzagged residual of a fixed order-0..3
 * predictor that runs across blocks. Lossless 16-bit. (The codec is Fretty's.) */
typedef struct { uint32_t off, frames, loop_s, loop_e; } ent_t;
typedef struct __attribute__((packed)) { uint8_t lo, hi, root, lv, hv, pad; int16_t tune; uint32_t smp; } zone_t;
typedef struct { uint32_t bit; int32_t h1, h2, h3; uint8_t order, k; } seek_t;    /* a decoder state at a frame */
typedef struct { const uint8_t *s[2]; uint32_t len[2]; seek_t loop[2]; } sinfo_t;

enum { BANK_NONE, BANK_LOADING, BANK_READY, BANK_FAILED };
enum { FLAG_MONO = 1 };

typedef struct {
    int state;
    uint8_t *file;
    uint32_t nsamp, nzone, flags, rate;
    float gain, step;                /* level trim; playback step at the root (rate / 44100) */
    const ent_t *ent; const zone_t *zone;
    sinfo_t *si;
} bank_t;

typedef struct { const uint8_t *p; uint32_t n, pos; uint64_t cache; int avail; } br_t;
typedef struct { br_t b; int32_t h1, h2, h3; int order, k; uint32_t i; } dec_t;

static inline void br_fill(br_t *b) {
    while (b->avail <= 56) {
        uint64_t byte = b->pos < b->n ? b->p[b->pos] : 0;
        b->pos++;
        b->cache |= byte << (56 - b->avail);
        b->avail += 8;
    }
}
static inline uint32_t br_take(br_t *b, int k) {
    if (!k) return 0;
    uint32_t v = (uint32_t)(b->cache >> (64 - k));
    b->cache <<= k; b->avail -= k;
    return v;
}
static void dec_seek(dec_t *d, const uint8_t *p, uint32_t n, const seek_t *s, uint32_t frame) {
    d->b.p = p; d->b.n = n; d->b.pos = s->bit >> 3; d->b.cache = 0; d->b.avail = 0;
    br_fill(&d->b);
    br_take(&d->b, (int)(s->bit & 7));
    d->h1 = s->h1; d->h2 = s->h2; d->h3 = s->h3; d->order = s->order; d->k = s->k; d->i = frame;
}
static void dec_state(const dec_t *d, seek_t *s) {
    s->bit = d->b.pos * 8 - (uint32_t)d->b.avail;
    s->h1 = d->h1; s->h2 = d->h2; s->h3 = d->h3; s->order = (uint8_t)d->order; s->k = (uint8_t)d->k;
}
/* Decode `count` frames into ring[slot...] (wrapping at RING), keeping the bit reader and predictor in registers:
 * the hot loop of every voice. Returns -1 on a corrupt stream. */
/* refill: eight bytes at once (big-endian) while they are inside the stream, one at a time near its end. Bits below
 * `avail` are always the stream's own next bits, so OR-ing the same word again later is harmless. */
#define REFILL() do { \
        if (avail <= 56) { \
            if (pos + 8 <= n) { \
                uint64_t w_; memcpy(&w_, p + pos, 8); w_ = __builtin_bswap64(w_); \
                cache |= w_ >> avail; int by_ = (63 - avail) >> 3; pos += by_; avail += by_ * 8; \
            } else while (avail <= 56) { uint64_t by_ = pos < n ? p[pos] : 0; pos++; cache |= by_ << (56 - avail); avail += 8; } \
        } } while (0)
#define TAKE(v_, k_) do { int kk_ = (k_); if (kk_) { v_ = (uint32_t)(cache >> (64 - kk_)); cache <<= kk_; avail -= kk_; } else v_ = 0; } while (0)
static int dec_run(dec_t *d, int16_t *ring, uint32_t slot, uint32_t count) {
    const uint8_t *p = d->b.p;
    uint32_t n = d->b.n, pos = d->b.pos, i = d->i;
    uint64_t cache = d->b.cache;
    int avail = d->b.avail, order = d->order, k = d->k;
    int32_t h1 = d->h1, h2 = d->h2, h3 = d->h3;
    int err = 0;
    while (count) {
        if ((i & 1023) == 0) {
            uint32_t hd;
            REFILL(); TAKE(hd, 8);
            order = (int)(hd >> 5); k = (int)(hd & 31);
            if (order > 3 || k > 20) { err = 1; break; }
        }
        uint32_t run = 1024 - (i & 1023);
        if (run > count) run = count;
        for (uint32_t j = 0; j < run; j++) {
            REFILL();
            int z = cache ? __builtin_clzll(cache) : 64;
            uint32_t u, lo;
            if (z >= 24) { cache <<= 24; avail -= 24; TAKE(u, 20); }
            else { cache <<= z + 1; avail -= z + 1; TAKE(lo, k); u = ((uint32_t)z << k) | lo; }
            int32_t r = (int32_t)(u >> 1) ^ -(int32_t)(u & 1), x;
            switch (order) {
            case 0: x = r; break;
            case 1: x = r + h1; break;
            case 2: x = r + 2 * h1 - h2; break;
            default: x = r + 3 * h1 - 3 * h2 + h3; break;
            }
            if (x < -32768) x = -32768, err = 1;
            if (x > 32767) x = 32767, err = 1;
            h3 = h2; h2 = h1; h1 = x;
            ring[slot] = (int16_t)x;
            slot = (slot + 1) & (RING - 1);
        }
        i += run; count -= run;
        if (err) break;
    }
    d->b.pos = pos; d->b.cache = cache; d->b.avail = avail;
    d->order = order; d->k = k; d->h1 = h1; d->h2 = h2; d->h3 = h3; d->i = i;
    return err ? -1 : 0;
}
#undef REFILL
#undef TAKE

/* ---------- the shared banks: one loader thread reads them all, the wanted ones first ---------- */
static bank_t g_banks[NINST];
static int g_prio[NINST];
static int g_users, g_shutdown, g_loader_on, g_loaded;
static pthread_t g_loader;
static char g_data_dir[1024];
static pthread_mutex_t g_bank_mx = PTHREAD_MUTEX_INITIALIZER;

static int bank_state_of(int i) { return __atomic_load_n(&g_banks[i].state, __ATOMIC_ACQUIRE); }

static int bank_load(bank_t *b, const char *path) {
    FILE *f = fopen(path, "rb");
    long size = 0;
    if (!f) return -1;
    if (fseek(f, 0, SEEK_END) || (size = ftell(f)) < 32 || size > (256L << 20) || fseek(f, 0, SEEK_SET)) { fclose(f); return -1; }
    uint8_t *m = malloc((size_t)size);
    if (!m || fread(m, 1, (size_t)size, f) != (size_t)size) { fclose(f); free(m); return -1; }
    fclose(f);
    b->file = m;
    uint32_t ver, ns, nz, doff, flags, rate;
    float gain;
    if (memcmp(m, "SNXB", 4)) return -1;
    memcpy(&ver, m + 4, 4); memcpy(&ns, m + 8, 4); memcpy(&nz, m + 12, 4); memcpy(&doff, m + 16, 4);
    memcpy(&flags, m + 20, 4); memcpy(&gain, m + 24, 4); memcpy(&rate, m + 28, 4);
    size_t need = 32 + (size_t)ns * sizeof(ent_t) + (size_t)nz * sizeof(zone_t);
    if (ver != 1 || ns == 0 || ns > 65535 || nz == 0 || nz > 4096 || doff != need || doff > (size_t)size) return -1;
    if (!(gain > 0.05f && gain < 20.0f)) gain = 1.0f;
    if (rate == 0) rate = 44100;
    if (rate < 8000 || rate > 96000) return -1;
    b->nsamp = ns; b->nzone = nz; b->flags = flags; b->gain = gain; b->rate = rate; b->step = rate / SR;
    b->ent = (const ent_t *)(m + 32);
    b->zone = (const zone_t *)(m + 32 + ns * sizeof(ent_t));
    for (uint32_t z = 0; z < nz; z++)
        if (b->zone[z].smp >= ns || b->zone[z].lo > b->zone[z].hi || b->zone[z].lv > b->zone[z].hv) return -1;
    b->si = calloc(ns, sizeof(sinfo_t));
    if (!b->si) return -1;
    const uint8_t *data = m + doff;
    size_t dlen = (size_t)size - doff;
    int nch = (flags & FLAG_MONO) ? 1 : 2;
    for (uint32_t i = 0; i < ns; i++) {
        const ent_t *e = &b->ent[i];
        sinfo_t *si = &b->si[i];
        if (e->frames < 8 || e->frames > 60 * 96000 || (size_t)e->off + 8 > dlen) return -1;
        if (e->loop_e && (e->loop_e > e->frames || e->loop_s + 64 >= e->loop_e)) return -1;
        uint32_t l0, l1;
        memcpy(&l0, data + e->off, 4); memcpy(&l1, data + e->off + 4, 4);
        if ((size_t)e->off + 8 + (size_t)l0 + l1 > dlen || (nch == 2 && !l1)) return -1;
        si->s[0] = data + e->off + 8; si->len[0] = l0;
        si->s[1] = data + e->off + 8 + l0; si->len[1] = l1;
        if (!e->loop_e) continue;
        for (int c = 0; c < nch; c++) {                 /* run up to the loop start once, to jump there later */
            dec_t d; seek_t z = { 0, 0, 0, 0, 0, 0 };
            int16_t scratch[RING];
            dec_seek(&d, si->s[c], si->len[c], &z, 0);
            for (uint32_t k = 0; k < e->loop_s; k += RING)
                if (dec_run(&d, scratch, 0, e->loop_s - k < RING ? e->loop_s - k : RING)) return -1;
            dec_state(&d, &si->loop[c]);
            if (__atomic_load_n(&g_shutdown, __ATOMIC_ACQUIRE)) return -1;
        }
    }
    return 0;
}

static void *loader_main(void *arg) {
    (void)arg;
    for (;;) {
        int pick = -1;
        pthread_mutex_lock(&g_bank_mx);
        for (int i = 0; i < NINST; i++)
            if (g_banks[i].state == BANK_NONE && (pick < 0 || g_prio[i] > g_prio[pick])) pick = i;
        if (pick >= 0) g_banks[pick].state = BANK_LOADING;
        pthread_mutex_unlock(&g_bank_mx);
        if (pick < 0 || __atomic_load_n(&g_shutdown, __ATOMIC_ACQUIRE)) break;
        char path[1200];
        snprintf(path, sizeof path, "%s/%s.snx", g_data_dir, INST_FILE[pick]);
        bank_t *b = &g_banks[pick];
        int ok = bank_load(b, path) == 0;
        if (!ok) { free(b->file); free(b->si); b->file = NULL; b->si = NULL; }
        __atomic_store_n(&b->state, ok ? BANK_READY : BANK_FAILED, __ATOMIC_RELEASE);
        __atomic_add_fetch(&g_loaded, 1, __ATOMIC_ACQ_REL);
    }
    return NULL;
}

static void banks_attach(const char *data_dir) {
    pthread_mutex_lock(&g_bank_mx);
    if (g_users++ == 0) {
        snprintf(g_data_dir, sizeof g_data_dir, "%s", data_dir ? data_dir : ".");
        g_shutdown = 0; g_loaded = 0;
        memset(g_prio, 0, sizeof g_prio);
        g_loader_on = !pthread_create(&g_loader, NULL, loader_main, NULL);
    }
    pthread_mutex_unlock(&g_bank_mx);
}

static void banks_detach(void) {
    pthread_mutex_lock(&g_bank_mx);
    int last = --g_users == 0;
    if (last) __atomic_store_n(&g_shutdown, 1, __ATOMIC_RELEASE);
    pthread_mutex_unlock(&g_bank_mx);
    if (!last) return;
    if (g_loader_on) { pthread_join(g_loader, NULL); g_loader_on = 0; }
    pthread_mutex_lock(&g_bank_mx);
    if (g_users == 0)
        for (int i = 0; i < NINST; i++) { free(g_banks[i].file); free(g_banks[i].si); memset(&g_banks[i], 0, sizeof g_banks[i]); }
    pthread_mutex_unlock(&g_bank_mx);
}

static void bank_want(int i) {         /* the loader takes the banks a preset uses before the rest */
    if (i >= 0 && i < NINST && g_prio[i] < 1) __atomic_store_n(&g_prio[i], 1, __ATOMIC_RELEASE);
}

/* ---------- parameters (src/params.h, from tools/sonix_params.py) ---------- */
static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
static float time_of(float p, float lo) { return lo * powf(10.0f / lo, clamp01(p)); }     /* 1 ms / 5 ms .. 10 s */
static float lfo_hz(float p) { return 0.05f * powf(400.0f, clamp01(p)); }                  /* 0.05 .. 20 Hz */
static float cutoff_hz(float p) { return 20.0f * powf(1000.0f, clamp01(p)); }              /* 20 Hz .. 20 kHz */
static float tone_hz(float p) { return 800.0f * powf(15.0f, clamp01(p)); }                 /* 800 Hz .. 12 kHz */
static int preset_key_index[NPRESET_KEYS];

static void init_tables(void) {
    static int done;
    if (done) return;
    for (int k = 0; k < NPRESET_KEYS; k++) {
        preset_key_index[k] = -1;
        for (int i = 0; i < NP; i++) if (!strcmp(PKEY[i], PRESET_KEYS[k])) preset_key_index[k] = i;
    }
    done = 1;
}

/* ---------- voices ---------- */
enum { ENV_OFF, ENV_ATTACK, ENV_DECAY, ENV_SUSTAIN, ENV_RELEASE, ENV_KILL };

typedef struct {
    int stage, bank, layer, key, held, sustained, nch, done;
    uint32_t note_id, smp, produced;
    double sp;                       /* position in the decoded stream (a loop's repeats continue it) */
    float semis, gain, env, vel;     /* semis: from the zone root, with the zone's tuning correction */
    float ic1[2], ic2[2];            /* filter state, per channel */
    dec_t d[2];
    int16_t ring[2][RING];
} voice_t;

typedef struct { uint8_t st, d1, d2; } ev_t;

#define DLY_LEN 131072               /* delay line: 2.97 s (a half note at 40 BPM) */
#define PRE_LEN 8192                 /* reverb pre-delay */
typedef struct {
    float p[NP];
    double mom_t[NP];
    voice_t v[MAX_V];
    ev_t ev[MAX_EV]; int nev;
    int pedal;
    float bend, mw;
    float vol_smooth;
    uint32_t note_counter;
    float lfo_ph, vib_ph, trem_prev;
    /* effects */
    float *cho; int cho_w; float cho_ph;
    float *dly; int dly_w; float dly_t, dly_lp[2];
    float *rv; int rv_comb[8], rv_len_c[8], rv_ap[4], rv_len_a[4]; float rv_store[8];
    float *pre; int pre_w;
    float bpm;
    /* what the faceplate shows */
    int rev;
    double clock;
    int touch; double touch_t; int touch_shown;
    double midi_t, wave_t, cpu_t, quiet_t;
    double cpu_acc; int cpu_n;
    float level;                     /* output level the waveform follows */
    int view_cat;                    /* the category the LCD's preset list shows */
    int wave_ph;
    int layer_busy[NLAYER];
} inst_t;

static int find_key(const char *k) {
    for (int i = 0; i < NP; i++) if (!strcmp(k, PKEY[i])) return i;
    return -1;
}
static int ival(const inst_t *s, int i, int lo, int hi) {
    int x = (int)lroundf(s->p[i]);
    return x < lo ? lo : x > hi ? hi : x;
}
#define LP(base, l) ((base) + (l) * (P_LAYER_B - P_LAYER_A))   /* a layer's parameter: A's index + l x stride */
static int layer_on(const inst_t *s, int l) { return s->p[LP(P_LAYER_A, l)] > 0.5f; }
static int layer_inst(const inst_t *s, int l) { return ival(s, LP(P_INST_A, l), 0, NINST - 1); }
static int any_solo(const inst_t *s) {
    for (int l = 0; l < NLAYER; l++) if (layer_on(s, l) && s->p[LP(P_SOLO_A, l)] > 0.5f) return 1;
    return 0;
}
static int layer_plays(const inst_t *s, int l) {
    return layer_on(s, l) && (!any_solo(s) || s->p[LP(P_SOLO_A, l)] > 0.5f);
}
static int cur_preset(const inst_t *s) { return ival(s, P_PRESET, 0, NPRESET - 1); }

static void want_banks(inst_t *s) {
    for (int l = 0; l < NLAYER; l++) if (layer_on(s, l)) bank_want(layer_inst(s, l));
}

/* favourites: one bit per preset in ten 13-bit parameters (saved with the project) */
static int fav_get(const inst_t *s, int pr) { return (ival(s, P_FAV_0 + pr / 13, 0, 8191) >> (pr % 13)) & 1; }
static void fav_set(inst_t *s, int pr, int on) {
    int w = ival(s, P_FAV_0 + pr / 13, 0, 8191);
    w = on ? w | (1 << (pr % 13)) : w & ~(1 << (pr % 13));
    s->p[P_FAV_0 + pr / 13] = (float)w;
}
static int any_fav(const inst_t *s, int cat) {
    for (int i = 0; i < NPRESET; i++) if (fav_get(s, i) && (cat < 0 || PRESET_CAT[i] == cat)) return 1;
    return 0;
}

/* the MAIN page's look: theme x LCD view (BROWSE | LAYERS), one option so a picture of both needs one when= */
static int browsing(const inst_t *s) { return s->p[P_VIEW] < 0.5f; }
static void sync_view(inst_t *s) {
    int look = ival(s, P_THEME, 0, NTHEME - 1) * 2 + !browsing(s);
    if (look != (int)s->p[P_LOOK]) { s->p[P_LOOK] = (float)look; s->rev++; }
}
/* the n-th (0-based) preset of the listed category, or -1 */
static int list_preset(const inst_t *s, int n) {
    for (int i = 0, k = 0; i < NPRESET; i++)
        if (PRESET_CAT[i] == s->view_cat && k++ == n) return i;
    return -1;
}

static void apply_preset(inst_t *s, int pr) {
    if (pr < 0) pr = 0;
    if (pr >= NPRESET) pr = NPRESET - 1;
    for (int k = 0; k < NPRESET_KEYS; k++) if (preset_key_index[k] >= 0) s->p[preset_key_index[k]] = PRESET_VAL[pr][k];
    for (int l = 0; l < NLAYER; l++) s->p[LP(P_SOLO_A, l)] = 0;
    s->p[P_PRESET] = (float)pr;
    s->p[P_FAV] = (float)fav_get(s, pr);
    s->view_cat = PRESET_CAT[pr];                 /* an open list follows the preset into its category */
    s->touch_shown = 0;
    want_banks(s);
    sync_view(s);
    s->rev++;
}
static int edited(const inst_t *s) {
    int pr = cur_preset(s);
    for (int k = 0; k < NPRESET_KEYS; k++) {
        int i = preset_key_index[k];
        if (i >= 0 && fabsf(s->p[i] - PRESET_VAL[pr][k]) > 0.0015f) return 1;
    }
    return 0;
}
/* prev / next, the data wheel and Q-Link 16: the next preset in the bank (the one picked on the LCD while browsing),
 * wrapping inside it; with FAV LIST on (and favourites in it) only the favourites */
static void step_preset(inst_t *s, int dir) {
    int cur = cur_preset(s);
    int cat = browsing(s) ? s->view_cat : PRESET_CAT[cur];
    int only = s->p[P_FAV_ONLY] > 0.5f && any_fav(s, cat);
    int list[NPRESET], n = 0, at = -1;
    for (int i = 0; i < NPRESET; i++)
        if (PRESET_CAT[i] == cat && (!only || fav_get(s, i))) { if (i == cur) at = n; list[n++] = i; }
    if (!n) return;
    int k = at < 0 ? (dir > 0 ? 0 : n - 1) : ((at + dir) % n + n) % n;
    if (list[k] != cur) apply_preset(s, list[k]);
}
/* an option stepped by a button, wrapping like a hardware selector */
static void step_option(inst_t *s, int i, int dir) {
    int n = (int)PINFO[i].hi + 1, v = ival(s, i, 0, n - 1);
    s->p[i] = (float)(((v + dir) % n + n) % n);
}
/* a tap on MPC's touch screen: buttons there act like toggles (each tap alternates 1 / 0, and a hold may send
 * nothing more), so every set is a tap; a second set within 150 ms is the same tap (MPC's echo of it) */
static int tapped(inst_t *s, int i) {
    if (s->clock - s->mom_t[i] < 0.15) return 0;
    s->mom_t[i] = s->clock;
    return 1;
}

static const zone_t *find_zone(const bank_t *b, int key, int vel) {
    const zone_t *best = NULL;
    int best_d = 1 << 30;
    for (uint32_t i = 0; i < b->nzone; i++) {
        const zone_t *z = &b->zone[i];
        if (vel < z->lv || vel > z->hv) continue;
        if (key >= z->lo && key <= z->hi) return z;
        int d = abs(key - z->root);
        if (d < best_d) { best_d = d; best = z; }
    }
    if (best) return best;
    for (uint32_t i = 0; i < b->nzone; i++)
        if (abs(key - b->zone[i].root) < best_d) { best_d = abs(key - b->zone[i].root); best = &b->zone[i]; }
    return best;
}

/* polyphony counts notes, not voices: a note on four layers is one note. Over POLYPHONY notes, the oldest note (a
 * released one first) fades out in ~3 ms on every layer. */
static void make_room(inst_t *s) {
    uint32_t ids[MAX_V]; int rel[MAX_V], n = 0;
    for (int i = 0; i < MAX_V; i++) {
        voice_t *v = &s->v[i];
        if (v->stage == ENV_OFF || v->stage == ENV_KILL) continue;
        int k;
        for (k = 0; k < n && ids[k] != v->note_id; k++) ;
        if (k == n) { ids[n] = v->note_id; rel[n] = 1; n++; }
        if (v->stage != ENV_RELEASE) rel[k] = 0;
    }
    if (n < ival(s, P_POLYPHONY, 1, MAX_POLY)) return;
    int pick = -1;
    for (int k = 0; k < n; k++) if (rel[k] && (pick < 0 || ids[k] < ids[pick])) pick = k;
    if (pick < 0) for (int k = 0; k < n; k++) if (pick < 0 || ids[k] < ids[pick]) pick = k;
    for (int i = 0; i < MAX_V; i++)
        if (s->v[i].stage != ENV_OFF && s->v[i].note_id == ids[pick]) s->v[i].stage = ENV_KILL;
}

static voice_t *free_voice(inst_t *s) {
    voice_t *f = NULL;
    for (int i = 0; i < MAX_V; i++) if (s->v[i].stage == ENV_OFF) return &s->v[i];
    for (int i = 0; i < MAX_V; i++)
        if (s->v[i].stage == ENV_KILL && (!f || s->v[i].note_id < f->note_id)) f = &s->v[i];
    if (!f) for (int i = 0; i < MAX_V; i++) if (!f || s->v[i].note_id < f->note_id) f = &s->v[i];
    return f;
}

static const float CURVE_EXP[3] = { 0.6f, 1.2f, 2.2f };   /* velocity curve: soft, normal, hard */

static void note_on(inst_t *s, int key, int vel) {
    int playing = 0;
    for (int l = 0; l < NLAYER; l++) playing += layer_plays(s, l);
    if (!playing) return;
    make_room(s);
    uint32_t id = ++s->note_counter;
    float vn = powf(vel / 127.0f, CURVE_EXP[ival(s, P_VEL_CURVE, 0, 2)]);
    int zvel = (int)lroundf(vn * 127.0f);                      /* the curve also picks the sample layer */
    if (zvel < 1) zvel = 1;
    float sens = clamp01(s->p[P_VEL_SENS]);
    float vgain = (1.0f - sens) + sens * (0.08f + 0.92f * vn);
    float lg = 1.0f / sqrtf((float)playing);                  /* stacked layers keep about the level of one */
    int any = 0;
    for (int l = 0; l < NLAYER; l++) {
        if (!layer_plays(s, l)) continue;
        int bi = layer_inst(s, l);
        if (bank_state_of(bi) != BANK_READY) continue;          /* still loading: that layer is silent */
        const bank_t *b = &g_banks[bi];
        const zone_t *z = find_zone(b, key, zvel);
        if (!z) continue;
        for (int i = 0; i < MAX_V; i++)                         /* the same key again on this layer: the old note rings out */
            if (s->v[i].stage != ENV_OFF && s->v[i].layer == l && s->v[i].key == key && s->v[i].stage < ENV_RELEASE) {
                s->v[i].stage = ENV_RELEASE; s->v[i].held = 0; s->v[i].sustained = 0;
            }
        voice_t *v = free_voice(s);
        const sinfo_t *si = &b->si[z->smp];
        v->stage = ENV_ATTACK;
        v->bank = bi; v->layer = l; v->key = key; v->held = 1; v->sustained = 0; v->note_id = id;
        v->smp = z->smp; v->nch = (b->flags & FLAG_MONO) ? 1 : 2; v->done = 0;
        v->produced = 0; v->sp = 0; v->env = 0; v->vel = vn;
        v->ic1[0] = v->ic1[1] = v->ic2[0] = v->ic2[1] = 0;
        v->semis = (float)(key - z->root) + z->tune * 0.01f;
        v->gain = b->gain * lg * vgain;
        seek_t start = { 0, 0, 0, 0, 0, 0 };
        for (int c = 0; c < v->nch; c++) dec_seek(&v->d[c], si->s[c], si->len[c], &start, 0);
        any = 1;
    }
    if (!any) return;
    if (s->p[P_MIDI_LED] < 0.5f) { s->p[P_MIDI_LED] = 1; s->rev++; }
    s->midi_t = s->clock;
}

static void note_off(inst_t *s, int key) {
    for (int i = 0; i < MAX_V; i++) {
        voice_t *v = &s->v[i];
        if (v->stage == ENV_OFF || v->stage >= ENV_RELEASE || v->key != key || !v->held) continue;
        v->held = 0;
        if (s->pedal) v->sustained = 1;
        else v->stage = ENV_RELEASE;
    }
}

static void all_off(inst_t *s, int hard) {
    for (int i = 0; i < MAX_V; i++) {
        voice_t *v = &s->v[i];
        if (v->stage == ENV_OFF) continue;
        v->held = v->sustained = 0;
        v->stage = hard ? ENV_KILL : (v->stage < ENV_RELEASE ? ENV_RELEASE : v->stage);
    }
}

static void handle_event(inst_t *s, const ev_t *e) {
    int type = e->st & 0xF0;
    if (type == 0x90 && e->d2 > 0) note_on(s, e->d1 & 127, e->d2 & 127);
    else if (type == 0x80 || (type == 0x90 && e->d2 == 0)) note_off(s, e->d1 & 127);
    else if (type == 0xB0) {
        if (e->d1 == 1) s->mw = (e->d2 & 127) / 127.0f;
        else if (e->d1 == 64) {
            int down = e->d2 >= 64;
            if (!down && s->pedal)
                for (int i = 0; i < MAX_V; i++)
                    if (s->v[i].sustained) { s->v[i].sustained = 0; if (s->v[i].stage < ENV_RELEASE) s->v[i].stage = ENV_RELEASE; }
            s->pedal = down;
        } else if (e->d1 == 120) all_off(s, 1);
        else if (e->d1 == 123) all_off(s, 0);
    } else if (type == 0xE0) {
        int pb = ((e->d2 & 127) << 7 | (e->d1 & 127)) - 8192;
        s->bend = pb / 8192.0f;
    }
}

/* decode a voice's sample up to stream frame `need` (exclusive); a loop's end continues at its start, a one-shot's
 * end is followed by silence */
static int voice_fill(voice_t *v, const bank_t *b, uint32_t need) {
    const ent_t *e = &b->ent[v->smp];
    const sinfo_t *si = &b->si[v->smp];
    while (v->produced < need) {
        uint32_t slot = v->produced & (RING - 1), count = need - v->produced;
        if (!v->done) {
            uint32_t end = e->loop_e ? e->loop_e : e->frames;
            if (v->d[0].i >= end) {
                if (e->loop_e) {
                    for (int c = 0; c < v->nch; c++) dec_seek(&v->d[c], si->s[c], si->len[c], &si->loop[c], e->loop_s);
                } else v->done = 1;
                continue;
            }
            if (count > end - v->d[0].i) count = end - v->d[0].i;
            for (int c = 0; c < v->nch; c++) if (dec_run(&v->d[c], v->ring[c], slot, count)) return -1;
        } else
            for (uint32_t j = 0; j < count; j++) v->ring[0][(slot + j) & (RING - 1)] = v->ring[1][(slot + j) & (RING - 1)] = 0;
        v->produced += count;
    }
    return 0;
}

/* ---------- reverb (Freeverb-style, 4 combs + 2 allpasses per side; from Fretty) ---------- */
static const int COMB_L[4] = { 1116, 1188, 1277, 1356 };
static const int AP_L[2] = { 556, 441 };

static int reverb_init(inst_t *s) {
    int total = 0;
    for (int c = 0; c < 8; c++) { s->rv_len_c[c] = COMB_L[c & 3] + (c >= 4 ? 23 : 0); total += s->rv_len_c[c]; }
    for (int a = 0; a < 4; a++) { s->rv_len_a[a] = AP_L[a & 1] + (a >= 2 ? 23 : 0); total += s->rv_len_a[a]; }
    s->rv = calloc(total, sizeof(float));
    return s->rv != NULL;
}

static void reverb_frame(inst_t *s, float in, float fb, float damp, float *ol, float *orr) {
    float *buf = s->rv;
    float out[2] = { 0, 0 };
    int base = 0;
    for (int c = 0; c < 8; c++) {
        float *d = buf + base;
        int i = s->rv_comb[c];
        float y = d[i];
        s->rv_store[c] = y * (1 - damp) + s->rv_store[c] * damp;
        d[i] = in + s->rv_store[c] * fb;
        if (++i >= s->rv_len_c[c]) i = 0;
        s->rv_comb[c] = i;
        out[c >> 2] += y;
        base += s->rv_len_c[c];
    }
    for (int a = 0; a < 4; a++) {
        float *d = buf + base;
        int i = s->rv_ap[a];
        float bo = d[i], x = out[a >> 1];
        d[i] = x + bo * 0.5f;
        out[a >> 1] = bo - x;
        if (++i >= s->rv_len_a[a]) i = 0;
        s->rv_ap[a] = i;
        base += s->rv_len_a[a];
    }
    *ol = out[0]; *orr = out[1];
}

static int need_loading(const inst_t *s);

/* ---------- engine entry points ---------- */
static void *eng_create(const char *data_dir) {
    init_tables();
    inst_t *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    for (int i = 0; i < NP; i++) s->p[i] = PINFO[i].def;
    s->vol_smooth = -1;
    s->touch = -1;
    for (int i = 0; i < NP; i++) s->mom_t[i] = -1;
    s->cho = calloc(2 * 2048, sizeof(float));
    s->dly = calloc(2 * DLY_LEN, sizeof(float));
    s->pre = calloc(PRE_LEN, sizeof(float));
    if (!s->cho || !s->dly || !s->pre || !reverb_init(s)) {
        free(s->cho); free(s->dly); free(s->pre); free(s->rv); free(s); return NULL;
    }
    banks_attach(data_dir);
    apply_preset(s, 0);
    sync_view(s);
    /* the first preset plays the moment Sonix opens: wait (up to 2 s) for its sounds, which the loader takes first */
    for (int w = 0; w < 400 && need_loading(s); w++) usleep(5000);
    return s;
}

static void eng_destroy(void *inst) {
    inst_t *s = inst;
    banks_detach();
    free(s->cho); free(s->dly); free(s->pre); free(s->rv);
    free(s);
}

static void eng_midi(void *inst, const uint8_t *msg, int len) {
    inst_t *s = inst;
    if (len < 1 || s->nev >= MAX_EV) return;
    ev_t e = { msg[0], len > 1 ? msg[1] : 0, len > 2 ? msg[2] : 0 };
    s->ev[s->nev++] = e;
}

static int is_computed(int i) {      /* the engine's own displays: never set from outside */
    if (PINFO[i].kind == K_TEXT) return 1;
    return i == P_LOOK || i == P_WAVE || i == P_MIDI_LED || i == P_LFO_LED || i == P_CPU ||
           (i >= P_ACT_A && i <= P_ACT_D);
}

static void eng_set_param(void *inst, const char *key, const char *val) {
    inst_t *s = inst;
    if (!strcmp(key, "lfo_bpm")) { float b = (float)atof(val); if (b > 20 && b < 400) s->bpm = b; return; }
    int i = find_key(key);
    float x = (float)atof(val);
    if (x != x) return;
    if (i >= P_ROW_1 && i < P_ROW_1 + LIST_ROWS) {    /* a patch row tapped: load its preset */
        int pr = list_preset(s, i - P_ROW_1);
        if (tapped(s, i) && pr >= 0 && pr != cur_preset(s)) apply_preset(s, pr);
        return;
    }
    if (i >= P_BANK_1 && i < P_BANK_1 + NCAT) {       /* a bank row tapped: list its patches (the sound stays) */
        if (tapped(s, i) && s->view_cat != i - P_BANK_1) { s->view_cat = i - P_BANK_1; s->rev++; }
        return;
    }
    if (i < 0 || is_computed(i)) return;
    if (PINFO[i].kind == K_MOMENTARY) {
        s->p[i] = x;
        if (!tapped(s, i)) return;
        if (i == P_PREV) step_preset(s, -1);
        else if (i == P_NEXT) step_preset(s, 1);
        else if (i == P_THEME_PREV || i == P_THEME_NEXT) { step_option(s, P_THEME, i == P_THEME_NEXT ? 1 : -1); sync_view(s); s->rev++; }
        else for (int l = 0; l < NLAYER; l++)
            if (i == LP(P_INST_A_PREV, l) || i == LP(P_INST_A_NEXT, l)) {
                step_option(s, LP(P_INST_A, l), i == LP(P_INST_A_NEXT, l) ? 1 : -1);
                want_banks(s); s->rev++;
            }
        return;
    }
    if (x < PINFO[i].lo) x = PINFO[i].lo;
    if (x > PINFO[i].hi) x = PINFO[i].hi;
    if (PINFO[i].whole) x = roundf(x);
    if (i == P_PRESET) { if ((int)x != cur_preset(s)) apply_preset(s, (int)x); return; }
    if (i == P_BROWSE) {                               /* one step per wheel click / Q-Link event, then back to rest */
        if ((int)x != 500) step_preset(s, x > 500 ? 1 : -1);
        s->p[P_BROWSE] = 500; s->rev++;
        return;
    }
    if (i == P_FAV) { fav_set(s, cur_preset(s), x > 0.5f); s->p[P_FAV] = x > 0.5f; s->rev++; return; }
    s->p[i] = x;
    if (i == P_VIEW || i == P_THEME) { if (i == P_VIEW) s->view_cat = PRESET_CAT[cur_preset(s)]; sync_view(s); s->rev++; return; }
    if (i >= P_FAV_0 && i <= P_FAV_9) { s->p[P_FAV] = (float)fav_get(s, cur_preset(s)); s->rev++; return; }
    if (i >= P_LAYER_A && i <= P_SOLO_D) want_banks(s);
    if (PINFO[i].kind == K_KNOB && i != P_VOLUME && i != P_BROWSE) { s->touch = i; s->touch_t = s->clock; s->touch_shown = 1; }
    s->rev++;
}

static int fmt_time(float sec, char *buf, int n) {
    return sec < 1.0f ? snprintf(buf, n, "%d ms", (int)lroundf(sec * 1000)) : snprintf(buf, n, "%.2f s", sec);
}
static int fmt_hz(float hz, char *buf, int n) {
    return hz < 1000 ? snprintf(buf, n, "%d Hz", (int)lroundf(hz)) : snprintf(buf, n, "%.1f kHz", hz / 1000);
}
static int fmt_signed(int v, const char *unit, char *buf, int n) {
    return v ? snprintf(buf, n, "%+d%s", v, unit) : snprintf(buf, n, "0%s", unit);
}

static int fmt_display(const inst_t *s, int i, char *buf, int n) {
    float x = s->p[i];
    switch (PINFO[i].fmt) {
    case F_VOL: return x <= 0.001f ? snprintf(buf, n, "-inf dB") : snprintf(buf, n, "%.1f dB", 40.0f * log10f(x));
    case F_LEVEL: return x <= 0.001f ? snprintf(buf, n, "-inf dB") : snprintf(buf, n, "%.1f dB", 40.0f * log10f(x / 0.8f));
    case F_PCT: return snprintf(buf, n, "%d%%", (int)lroundf(clamp01(x) * 100));
    case F_BIPOLAR: return fmt_signed((int)lroundf((clamp01(x) - 0.5f) * 200), "%", buf, n);
    case F_CUTOFF: return fmt_hz(cutoff_hz(x), buf, n);
    case F_TONE: return fmt_hz(tone_hz(x), buf, n);
    case F_ATTACK: return fmt_time(time_of(x, 0.001f), buf, n);
    case F_TIME: return fmt_time(time_of(x, 0.005f), buf, n);
    case F_LFO: { float hz = lfo_hz(x); return snprintf(buf, n, hz < 10 ? "%.2f Hz" : "%.1f Hz", hz); }
    case F_VIB: return snprintf(buf, n, "%d ct", (int)lroundf(x * x * 100));
    case F_PAN: {
        int v = (int)lroundf(x * 50);
        return v == 0 ? snprintf(buf, n, "C") : snprintf(buf, n, "%c %d", v < 0 ? 'L' : 'R', abs(v));
    }
    case F_OCT: return fmt_signed((int)lroundf(x), "", buf, n);
    case F_CENTS: return fmt_signed((int)lroundf(x), " ct", buf, n);
    case F_SEMI:
        if (i == P_BEND_RANGE) return snprintf(buf, n, "%d st", (int)lroundf(x));
        return fmt_signed((int)lroundf(x), " st", buf, n);
    case F_HZINT: return snprintf(buf, n, "%d Hz", (int)lroundf(x));
    case F_INT: return snprintf(buf, n, "%d", (int)lroundf(x));
    case F_PREDELAY: return snprintf(buf, n, "%d ms", (int)lroundf(clamp01(x) * 100));
    case F_BROWSE: return snprintf(buf, n, "%s", PRESET_NAME[cur_preset(s)]);   /* the knob shows the preset's name */
    }
    return snprintf(buf, n, "%g", x);
}

static void upcase(char *d, const char *src, int n) {
    int k = 0;
    for (; src[k] && k < n - 1; k++) d[k] = (src[k] >= 'a' && src[k] <= 'z') ? src[k] - 32 : src[k];
    d[k] = 0;
}
static int need_loading(const inst_t *s) {
    for (int l = 0; l < NLAYER; l++)
        if (layer_on(s, l) && bank_state_of(layer_inst(s, l)) < BANK_READY) return 1;
    return 0;
}

#define DOT "  \xc2\xb7  "
static int lcd_text(inst_t *s, int i, char *buf, int n) {
    int pr = cur_preset(s);
    char name[64];
    if (i >= P_LCD_A && i <= P_LCD_D) {
        int l = i - P_LCD_A;
        if (!layer_on(s, l)) return snprintf(buf, n, "\xe2\x80\x94");
        upcase(name, INST_NAME[layer_inst(s, l)], sizeof name);
        return snprintf(buf, n, "%s", name);
    }
    if (i >= P_LCAT_A && i <= P_LCAT_D) {
        int l = i - P_LCAT_A;
        const char *st = !layer_on(s, l) ? DOT "OFF" : s->p[LP(P_SOLO_A, l)] > 0.5f ? DOT "SOLO" :
                         !layer_plays(s, l) ? DOT "MUTED" : "";
        return snprintf(buf, n, "%s%s", CAT_NAME[INST_CAT[layer_inst(s, l)]], st);
    }
    if (i >= P_LNAME_A && i <= P_LNAME_D) {
        int inst = layer_inst(s, i - P_LNAME_A);
        if (bank_state_of(inst) == BANK_FAILED) return snprintf(buf, n, "SOUND MISSING");
        upcase(name, INST_NAME[inst], sizeof name);
        return snprintf(buf, n, "%s", name);
    }
    switch (i) {
    case P_LCD_TOP:
        return snprintf(buf, n, "%s" DOT "%03d / %d%s%s", CAT_NAME[PRESET_CAT[pr]], pr + 1, NPRESET,
                        fav_get(s, pr) ? DOT "\xe2\x98\x85" : "", edited(s) ? DOT "EDITED" : "");
    case P_LCD_REF: {
        int tr = ival(s, P_TRANSPOSE, -24, 24);
        if (tr) return snprintf(buf, n, "TR %+d   A4  %d HZ", tr, ival(s, P_A4_REF, 400, 480));
        return snprintf(buf, n, "A4  %d HZ", ival(s, P_A4_REF, 400, 480));
    }
    case P_LCD_NAME:
        upcase(name, PRESET_NAME[pr], sizeof name);
        return snprintf(buf, n, "%s%s", name, fav_get(s, pr) ? "  \xe2\x98\x85" : "");
    case P_LCD_LINE: {
        if (need_loading(s)) return snprintf(buf, n, "Loading sounds...");
        if (s->touch >= 0 && s->touch_shown) {
            char v[32];
            fmt_display(s, s->touch, v, sizeof v);
            return snprintf(buf, n, "%s   %s", PNAME[s->touch], v);
        }
        return snprintf(buf, n, "%s", PRESET_DESC[pr]);
    }
    }
    return 0;
}

static int eng_get_param(void *inst, const char *key, char *buf, int n) {
    inst_t *s = inst;
    if (n <= 0) return 0;
    if (!strcmp(key, "display_rev")) return snprintf(buf, n, "%d", s->rev);
    if (!strcmp(key, "preset_display")) {
        char name[64];
        upcase(name, PRESET_NAME[cur_preset(s)], sizeof name);
        return snprintf(buf, n, "%s%s", name, fav_get(s, cur_preset(s)) ? "  \xe2\x98\x85" : "");
    }
    if (!strncmp(key, "bank_", 5)) {                  /* the bank list: the 12 categories, lit = the one listed */
        int r = atoi(key + 5) - 1;
        if (r < 0 || r >= NCAT) return 0;
        if (strstr(key, "_on")) return snprintf(buf, n, "%d", r == s->view_cat);
        return snprintf(buf, n, "%s", CAT_NAME[r]);
    }
    if (!strncmp(key, "row_", 4)) {                   /* the preset list: "003  VELVET GRAND *", lit = the current one */
        int r = atoi(key + 4) - 1, pr = r >= 0 && r < LIST_ROWS ? list_preset(s, r) : -1;
        if (strstr(key, "_on")) return snprintf(buf, n, "%d", pr >= 0 && pr == cur_preset(s));
        if (pr < 0) return snprintf(buf, n, " ");
        char name[64];
        upcase(name, PRESET_NAME[pr], sizeof name);
        return snprintf(buf, n, "%d  %s%s", pr + 1, name, fav_get(s, pr) ? "  \xe2\x98\x85" : "");
    }
    int i = find_key(key);
    if (i >= 0 && PINFO[i].kind == K_TEXT) return lcd_text(s, i, buf, n);
    if (i >= 0) return snprintf(buf, n, "%g", s->p[i]);
    size_t kl = strlen(key);
    if (kl > 8 && !strcmp(key + kl - 8, "_display")) {
        char k2[64];
        if (kl - 8 >= sizeof k2) return 0;
        memcpy(k2, key, kl - 8); k2[kl - 8] = 0;
        i = find_key(k2);
        if (i >= 0 && PINFO[i].kind == K_KNOB) return fmt_display(s, i, buf, n);
    }
    return 0;
}

/* ---------- audio ---------- */
static void eng_render(void *inst, int16_t *out, int frames) {
    inst_t *s = inst;
    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (frames > 128) frames = 128;
    if (frames < 1) return;
    for (int i = 0; i < s->nev; i++) handle_event(s, &s->ev[i]);
    s->nev = 0;
    const float dt = frames / SR;

    /* pitch: A4 reference, tune, transpose, bend; vibrato from the LFO and the mod wheel */
    float a4 = s->p[P_A4_REF];
    if (!(a4 >= 400 && a4 <= 480)) a4 = 440;
    int mwmode = ival(s, P_MOD_WHEEL, 0, 2);
    float lfo_rate = lfo_hz(s->p[P_LFO_RATE]);
    s->lfo_ph += lfo_rate * dt; s->lfo_ph -= floorf(s->lfo_ph);
    s->vib_ph += 5.5f * dt; s->vib_ph -= floorf(s->vib_ph);
    float lfo = sinf(2 * PI_F * s->lfo_ph);
    float vib_amt = s->p[P_LFO_PITCH] * s->p[P_LFO_PITCH];                     /* up to a semitone */
    float pitch_mod = vib_amt * lfo + (mwmode == 0 ? s->mw * 0.5f * sinf(2 * PI_F * s->vib_ph) : 0.0f);
    float global_semis = ival(s, P_TRANSPOSE, -24, 24) + s->p[P_TUNE] * 0.01f + s->bend * ival(s, P_BEND_RANGE, 0, 24) + pitch_mod;
    float pitch_base = a4 / 440.0f;

    float att = time_of(s->p[P_ATTACK], 0.001f), dec = time_of(s->p[P_DECAY], 0.005f);
    float rel = time_of(s->p[P_RELEASE], 0.005f);
    float sus = clamp01(s->p[P_SUSTAIN]);
    float att_step = 1.0f / (att * SR);
    float dec_k = 1.0f - expf(-1.0f / (dec * SR * 0.33f));
    float rel_k = 1.0f - expf(-1.0f / (rel * SR * 0.33f));
    float kill_k = 1.0f - expf(-1.0f / (0.003f * SR * 0.33f));

    /* filter: cutoff, +/- 5 octaves of envelope (ENV AMT, centre = none), key track (octaves per octave from C4),
     * velocity (soft notes darker), +/- 2 octaves of LFO, the mod wheel opening it */
    float cut_oct = log2f(cutoff_hz(s->p[P_CUTOFF])) + (mwmode == 1 ? s->mw * 4.0f : 0.0f);
    float env_oct = (clamp01(s->p[P_ENV_AMT]) - 0.5f) * 10.0f;
    float lfo_oct = clamp01(s->p[P_LFO_FILTER]) * 2.0f * lfo;
    float lfo_span = clamp01(s->p[P_LFO_FILTER]) * 2.0f;
    float kt = clamp01(s->p[P_KEY_TRACK]), vf = clamp01(s->p[P_VEL_FILTER]);
    float k_res = 2.0f - 1.9f * clamp01(s->p[P_RESONANCE]);
    float open_oct = log2f(17500.0f);

    /* tremolo, ramped across the block */
    float tdep = clamp01(s->p[P_LFO_AMP]);
    float trem = 1.0f - tdep * (0.5f + 0.5f * lfo);
    float trem0 = s->trem_prev > 0 ? s->trem_prev : trem, trem_step = (trem - trem0) / frames;
    s->trem_prev = trem;
    float spread = clamp01(s->p[P_SPREAD]);

    float mix[256];
    memset(mix, 0, sizeof(float) * 2 * frames);
    int sounding = 0, held = 0, busy[NLAYER] = { 0 };
    for (int vi = 0; vi < MAX_V; vi++) {
        voice_t *v = &s->v[vi];
        if (v->stage == ENV_OFF) continue;
        if (bank_state_of(v->bank) != BANK_READY) { v->stage = ENV_OFF; continue; }
        sounding++;
        held |= v->held;
        busy[v->layer] = 1;
        const bank_t *b = &g_banks[v->bank];
        const ent_t *e = &b->ent[v->smp];
        int l = v->layer;
        float lsemis = 12.0f * ival(s, LP(P_OCTAVE_A, l), -3, 3) + s->p[LP(P_FINE_A, l)] * 0.01f;
        double inc = pitch_base * exp2((v->semis + lsemis + global_semis) / 12.0) * b->step;
        if (inc > 15) inc = 15;
        uint32_t need = (uint32_t)(v->sp + inc * frames) + 2;
        if (voice_fill(v, b, need)) { v->stage = ENV_OFF; continue; }
        float lv = s->p[LP(P_LEVEL_A, l)] * 1.25f;
        float lgain = lv * lv;
        float pan = s->p[LP(P_PAN_A, l)] + spread * (v->key - 60) / 30.0f;
        if (pan < -1) pan = -1;
        if (pan > 1) pan = 1;
        float gl, gr;
        if (v->nch == 1) { float th = (pan + 1) * PI_F * 0.25f; gl = cosf(th) * 1.41421f; gr = sinf(th) * 1.41421f; }
        else { gl = pan > 0 ? 1 - pan : 1; gr = pan < 0 ? 1 + pan : 1; }
        float voice_oct = cut_oct + kt * (v->key - 60) / 12.0f + vf * (v->vel - 1.0f) * 3.0f;
        int filter_on = voice_oct - fabsf(env_oct) - lfo_span < open_oct;
        float g1 = 0, a1 = 0, a2 = 0, a3 = 0;
        const int16_t *r0 = v->ring[0], *r1 = v->ring[v->nch - 1];
        float tg = trem0;
        for (int f = 0; f < frames; f++, tg += trem_step) {
            switch (v->stage) {
            case ENV_ATTACK: v->env += att_step; if (v->env >= 1.0f) { v->env = 1.0f; v->stage = ENV_DECAY; } break;
            case ENV_DECAY:
                v->env += (sus - v->env) * dec_k;
                if (fabsf(v->env - sus) < 1e-4f) { v->env = sus; v->stage = ENV_SUSTAIN; }
                break;
            case ENV_SUSTAIN: v->env = sus; break;
            case ENV_RELEASE: v->env -= v->env * rel_k; break;
            case ENV_KILL: v->env -= v->env * kill_k; break;
            }
            if (v->stage >= ENV_RELEASE && v->env < 1e-4f) { v->stage = ENV_OFF; break; }
            if (v->stage == ENV_SUSTAIN && sus <= 0.0f) { v->stage = ENV_OFF; break; }
            if (!e->loop_e && v->sp >= e->frames) { v->stage = ENV_OFF; break; }
            uint32_t ip = (uint32_t)v->sp;
            float fr = (float)(v->sp - ip);
            uint32_t i0 = ip & (RING - 1), i1 = (ip + 1) & (RING - 1);
            float L = (r0[i0] + (r0[i1] - r0[i0]) * fr) * (1.0f / 32768.0f);
            float R = (r1[i0] + (r1[i1] - r1[i0]) * fr) * (1.0f / 32768.0f);
            if (filter_on) {
                if ((f & 15) == 0) {                          /* coefficients every 16 frames */
                    float fc = exp2f(voice_oct + env_oct * v->env + lfo_oct);
                    if (fc < 20.0f) fc = 20.0f;
                    if (fc > 18000.0f) fc = 18000.0f;
                    g1 = tanf(PI_F * fc / SR);
                    a1 = 1.0f / (1.0f + g1 * (g1 + k_res)); a2 = g1 * a1; a3 = g1 * a2;
                }
                float in[2] = { L, R };
                for (int c = 0; c < v->nch; c++) {             /* TPT state-variable low-pass, 12 dB (mono: once) */
                    float v3 = in[c] - v->ic2[c];
                    float v1 = a1 * v->ic1[c] + a2 * v3;
                    float v2 = v->ic2[c] + a2 * v->ic1[c] + a3 * v3;
                    v->ic1[c] = 2 * v1 - v->ic1[c];
                    v->ic2[c] = 2 * v2 - v->ic2[c];
                    in[c] = v2;
                }
                L = in[0]; R = in[v->nch - 1];
            }
            float g = v->env * v->gain * lgain * tg;
            mix[2 * f] += L * g * gl;
            mix[2 * f + 1] += R * g * gr;
            v->sp += inc;
        }
    }

    /* ---- effects: chorus -> delay -> reverb -> volume -> soft limit (from Fretty) ---- */
    float chorus = clamp01(s->p[P_CHORUS]), delay = clamp01(s->p[P_DELAY]), space = clamp01(s->p[P_REVERB]);
    float vol = clamp01(s->p[P_VOLUME]);
    float target = vol * vol;
    if (s->vol_smooth < 0) s->vol_smooth = target;
    float vstep = (target - s->vol_smooth) / frames;
    float dry = 1.0f - 0.35f * space, wet = space * 0.9f;
    float cho_rate = 2 * PI_F * 0.6f / SR, cho_depth = 0.0035f * SR * chorus, cho_base = 0.012f * SR;
    float beat = 60.0f / (s->bpm > 0 ? s->bpm : 120.0f);
    float dly_target = DELAY_BEATS[ival(s, P_DELAY_TIME, 0, NDELAY_TIMES - 1)] * beat * SR;
    while (dly_target > DLY_LEN - 4) dly_target *= 0.5f;
    if (s->dly_t <= 0) s->dly_t = dly_target;
    float dly_fb = 0.85f * clamp01(s->p[P_DELAY_FB]), dly_wet = 0.7f * delay, dw = clamp01(s->p[P_DELAY_WIDTH]);
    float dly_lpk = 1.0f - expf(-2 * PI_F * tone_hz(s->p[P_DELAY_TONE]) / SR);
    float rv_fb = 0.70f + 0.27f * clamp01(s->p[P_REV_SIZE]), rv_damp = 0.05f + 0.65f * clamp01(s->p[P_REV_DAMP]);
    int pre_n = (int)(clamp01(s->p[P_REV_PREDELAY]) * 0.1f * SR);
    float rw = clamp01(s->p[P_REV_WIDTH]);
    float peak = 0;
    for (int i = 0; i < frames; i++) {
        float ch[2] = { mix[2 * i], mix[2 * i + 1] };
        if (chorus > 0.001f) {
            s->cho_ph += cho_rate; if (s->cho_ph > 2 * PI_F) s->cho_ph -= 2 * PI_F;
            int w = s->cho_w;
            s->cho[w] = ch[0]; s->cho[2048 + w] = ch[1];
            for (int c = 0; c < 2; c++) {
                float d = cho_base + cho_depth * (1.0f + (c ? cosf(s->cho_ph) : sinf(s->cho_ph)));
                float rp = (float)w - d;
                while (rp < 0) rp += 2048;
                int q = (int)rp; float fr = rp - q;
                const float *buf = s->cho + c * 2048;
                float dl = buf[q & 2047] + (buf[(q + 1) & 2047] - buf[q & 2047]) * fr;
                ch[c] = ch[c] * (1.0f - 0.3f * chorus) + dl * 0.75f * chorus;
            }
            s->cho_w = (w + 1) & 2047;
        }
        if (delay > 0.004f) {                                 /* ping-pong; WIDTH 0 keeps the repeats in the middle */
            s->dly_t += 0.0005f * (dly_target - s->dly_t);
            float rp = (float)s->dly_w - s->dly_t;
            while (rp < 0) rp += DLY_LEN;
            int q = (int)rp; float fr = rp - q;
            int q1 = q + 1 < DLY_LEN ? q + 1 : 0;
            float yl = s->dly[q] + (s->dly[q1] - s->dly[q]) * fr;
            float yr = s->dly[DLY_LEN + q] + (s->dly[DLY_LEN + q1] - s->dly[DLY_LEN + q]) * fr;
            s->dly_lp[0] += dly_lpk * (yl - s->dly_lp[0]);
            s->dly_lp[1] += dly_lpk * (yr - s->dly_lp[1]);
            s->dly[s->dly_w] = 0.5f * (ch[0] + ch[1]) + dly_fb * s->dly_lp[1];
            s->dly[DLY_LEN + s->dly_w] = dly_fb * s->dly_lp[0];
            if (++s->dly_w >= DLY_LEN) s->dly_w = 0;
            float a = 0.5f + 0.5f * dw, bb = 0.5f - 0.5f * dw;
            ch[0] += dly_wet * (a * yl + bb * yr); ch[1] += dly_wet * (a * yr + bb * yl);
        } else if (s->dly_lp[0] != 0 || s->dly_lp[1] != 0) {
            memset(s->dly, 0, 2 * DLY_LEN * sizeof(float)); s->dly_lp[0] = s->dly_lp[1] = 0; s->dly_t = dly_target;
        }
        float rl = 0, rr = 0;
        if (space > 0.001f) {
            s->pre[s->pre_w] = (ch[0] + ch[1]) * 0.03f;
            float in = s->pre[(s->pre_w - pre_n + PRE_LEN) & (PRE_LEN - 1)];
            s->pre_w = (s->pre_w + 1) & (PRE_LEN - 1);
            float o0, o1;
            reverb_frame(s, in, rv_fb, rv_damp, &o0, &o1);
            float a = 0.5f + 0.5f * rw, bb = 0.5f - 0.5f * rw;
            rl = a * o0 + bb * o1; rr = a * o1 + bb * o0;
        }
        s->vol_smooth += vstep;
        float o[2] = { (ch[0] * dry + rl * wet) * s->vol_smooth, (ch[1] * dry + rr * wet) * s->vol_smooth };
        for (int c = 0; c < 2; c++) {
            float y = o[c];
            if (fabsf(y) > peak) peak = fabsf(y);
            if (y > 0.9f) y = 0.9f + 0.1f * tanhf((y - 0.9f) * 10.0f);
            else if (y < -0.9f) y = -0.9f + 0.1f * tanhf((y + 0.9f) * 10.0f);
            out[2 * i + c] = (int16_t)lrintf(y * 32767.0f);
        }
    }

    /* ---- faceplate state: changed only on events (the waveform at most every 100 ms) ---- */
    s->clock += dt;
    if (held) s->midi_t = s->clock;                          /* the MIDI LED stays lit while keys are down */
    if (s->p[P_MIDI_LED] > 0.5f && s->clock - s->midi_t > 0.15) { s->p[P_MIDI_LED] = 0; s->rev++; }
    for (int l = 0; l < NLAYER; l++)
        if (busy[l] != (int)s->p[P_ACT_A + l]) { s->p[P_ACT_A + l] = (float)busy[l]; s->rev++; }
    int lfo_led = s->p[P_LFO_FILTER] > 0.001f || s->p[P_LFO_PITCH] > 0.001f || s->p[P_LFO_AMP] > 0.001f;
    if (lfo_led != (int)s->p[P_LFO_LED]) { s->p[P_LFO_LED] = (float)lfo_led; s->rev++; }
    /* the waveform: its height follows the output level, it moves one step per tick while anything sounds */
    s->level = peak > s->level ? peak : s->level * expf(-dt / 0.25f);
    int anim = ival(s, P_ANIM, 0, 2);
    if (sounding) s->quiet_t = s->clock;
    if (anim && sounding && s->clock - s->wave_t >= (anim == 2 ? 0.095 : 0.195)) {
        float dbl = 20.0f * log10f(s->level + 1e-9f);
        int a = dbl >= -12 ? 3 : dbl >= -20 ? 2 : dbl >= -30 ? 1 : 0;
        s->wave_ph = (s->wave_ph + 1) & 3;
        s->p[P_WAVE] = (float)(1 + a * 4 + s->wave_ph);
        s->wave_t = s->clock; s->rev++;
    } else if ((!anim || s->clock - s->quiet_t > 0.3) && s->p[P_WAVE] > 0) { s->p[P_WAVE] = 0; s->rev++; }
    if (s->touch_shown && s->clock - s->touch_t > 1.5) s->touch_shown = 0;
    /* CPU meter: this engine's share of the block's time, averaged, shown at most every 2 s */
    struct timespec t1;
    clock_gettime(CLOCK_MONOTONIC, &t1);
    s->cpu_acc += ((t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9) / dt;
    s->cpu_n++;
    if (s->clock - s->cpu_t > 2.0) {
        int c = (int)lround(s->cpu_acc / s->cpu_n * 20.0);   /* 10 steps of 5% */
        if (c > 10) c = 10;
        if (c != (int)s->p[P_CPU]) { s->p[P_CPU] = (float)c; s->rev++; }
        s->cpu_acc = 0; s->cpu_n = 0; s->cpu_t = s->clock;
    }
}

static const mpc_engine_t ENGINE = { eng_create, eng_destroy, eng_midi, eng_set_param, eng_get_param, eng_render, NULL, NULL };
const mpc_engine_t *mpc_engine(void) { return &ENGINE; }
