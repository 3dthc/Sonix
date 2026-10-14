/* Sonix: a ROMpler for MPC OS (mpc_engine, see mpc-vst-plugins wrapper/engine.h).
 *
 * Four layers (A-D), each playing one of the packed instruments (engine/<name>.snx, built by tools/pack_sonix.py) and
 * switched on or off on the faceplate. A note finds its zone by key and velocity in every layer that is on, is
 * repitched from the zone's root (plus the zone's own tuning correction, TUNE in semitones and the A4 REF pitch
 * standard), loops through its sustain loop if it has one, runs through a 12 dB low-pass (CUTOFF, RESONANCE, ENV AMT
 * on the amp envelope's shape, the LFO) and an ADSR. Then chorus, a tempo-synced ping-pong delay and reverb.
 * 16 notes of polyphony; sustain pedal and pitch bend (+/-2).
 *
 * The faceplate browses the EDIT LAYER's instrument by category (the 12 category buttons, PREV / NEXT) and reads
 * its LCD from the engine (lcd_* text, the wave / LED / CPU pictures). Those change only on events, never per audio
 * block, because every repaint costs MPC's screen thread (mpc-vst-plugins docs/NOTES.md, "Animation is expensive").
 *
 * The sample banks (loading on a worker thread, the lossless codec, retiring unused banks) are adapted from Fretty
 * (3dthc/fretty-mpc, MIT). A bank is decoded into RAM the first time an instrument is picked, and shared by every
 * Sonix instance in the process. */
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

#define SR 44100.0f
#define MAX_EV 256

/* ---------- the shared sample banks ---------- */
typedef struct { uint32_t off, frames, loop_s, loop_e; } ent_t;
typedef struct __attribute__((packed)) { uint8_t lo, hi, root, lv, hv, pad; int16_t tune; uint32_t smp; } zone_t;

enum { BANK_NONE, BANK_LOADING, BANK_READY, BANK_FAILED, BANK_RETIRING };
enum { FLAG_MONO = 1 };

typedef struct {
    int state, stop;
    char path[1200];
    uint8_t *map; size_t maplen;
    uint32_t nsamp, nzone, flags;
    float gain;
    const ent_t *ent; const zone_t *zone;
    int16_t *head; uint32_t *hoff;           /* every sample decoded to interleaved stereo */
    pthread_t loader; int have_loader, own_tables;
} bank_t;

static bank_t g_banks[NINST];
static int g_shutdown;
static int g_bank_users[NINST];             /* instances that have the instrument picked */
static double g_bank_idle[NINST];           /* when the last of them moved on */
static int g_users;
static char g_data_dir[1024];
static pthread_mutex_t g_bank_mx = PTHREAD_MUTEX_INITIALIZER;

static double now_s(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
static int bank_state_of(int i) { return __atomic_load_n(&g_banks[i].state, __ATOMIC_ACQUIRE); }

/* ---------- sample file (written by tools/pack_sonix.py) ----------
 * header (32 bytes): "SNXB", u32 version 1, u32 samples, u32 zones, u32 data offset, u32 flags (1 = mono), f32 level
 * trim, u32 reserved; per sample { u32 byte offset into data, u32 frames, u32 loop start, u32 loop end (0 = none) };
 * per zone zone_t; then the data. Each sample: u32 len0, u32 len1, then one or two bitstreams (left, right; mono has
 * one). A stream is 1024-frame blocks: a header byte (order << 5 | k), then one Rice code per frame (a run of q zeros,
 * a 1, k low bits; 24 zeros = a raw 20-bit value) of the zigzagged residual of a fixed order-0..3 predictor that runs
 * across blocks. Lossless 16-bit. (The codec is Fretty's.) */
typedef struct { const uint8_t *p; size_t n, pos; uint64_t cache; int avail; } br_t;

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

static int decode_channel(const uint8_t *src, size_t len, int16_t *dst, uint32_t frames) {
    br_t b = { src, len, 0, 0, 0 };
    int32_t h1 = 0, h2 = 0, h3 = 0;
    int order = 0, k = 0;
    for (uint32_t i = 0; i < frames; i++) {
        br_fill(&b);
        if ((i & 1023) == 0) {
            uint32_t hd = br_take(&b, 8);
            order = (int)(hd >> 5); k = (int)(hd & 31);
            if (order > 3 || k > 20) return -1;
            br_fill(&b);
        }
        int z = b.cache ? __builtin_clzll(b.cache) : 64;
        uint32_t u;
        if (z >= 24) { br_take(&b, 24); u = br_take(&b, 20); }
        else { br_take(&b, z + 1); u = ((uint32_t)z << k) | br_take(&b, k); }
        int32_t r = (int32_t)(u >> 1) ^ -(int32_t)(u & 1);
        int32_t x;
        switch (order) {
        case 0: x = r; break;
        case 1: x = r + h1; break;
        case 2: x = r + 2 * h1 - h2; break;
        default: x = r + 3 * h1 - 3 * h2 + h3; break;
        }
        if (x < -32768 || x > 32767) return -1;
        dst[2 * (size_t)i] = (int16_t)x;
        h3 = h2; h2 = h1; h1 = x;
    }
    return b.pos <= len + 8 ? 0 : -1;
}

static void bank_free_data(bank_t *b) {
    free(b->map);
    if (b->own_tables) { free((void *)b->ent); free((void *)b->zone); }
    free(b->head); free(b->hoff);
    b->map = NULL; b->ent = NULL; b->zone = NULL; b->head = NULL; b->hoff = NULL;
    b->own_tables = 0;
}

/* Memory: before a new instrument loads, give back the ones no Sonix has picked for a while. A retiring bank is
 * unplayable at once (voices on it stop at their next block) and is freed only a second later, so no audio thread
 * can still be reading it. */
#ifndef BANK_IDLE_S
#define BANK_IDLE_S 20.0
#endif
static int lock_unless_shutdown(void) {
    while (pthread_mutex_trylock(&g_bank_mx)) {
        if (__atomic_load_n(&g_shutdown, __ATOMIC_ACQUIRE)) return 0;
        usleep(2000);
    }
    return 1;
}

static void release_idle_banks(int except) {
    for (int i = 0; i < NINST; i++) {
        if (i == except) continue;
        if (!lock_unless_shutdown()) return;
        int go = g_banks[i].state == BANK_READY && __atomic_load_n(&g_bank_users[i], __ATOMIC_ACQUIRE) == 0 &&
                 now_s() - g_bank_idle[i] > BANK_IDLE_S;
        if (go) __atomic_store_n(&g_banks[i].state, BANK_RETIRING, __ATOMIC_RELEASE);
        pthread_mutex_unlock(&g_bank_mx);
        if (!go) continue;
        for (int w = 0; w < 50 && !__atomic_load_n(&g_shutdown, __ATOMIC_ACQUIRE); w++) usleep(20000);
        if (!lock_unless_shutdown()) return;
        if (__atomic_load_n(&g_bank_users[i], __ATOMIC_ACQUIRE) == 0 && !g_banks[i].stop) {
            bank_t *b = &g_banks[i];
            if (b->have_loader) { pthread_join(b->loader, NULL); b->have_loader = 0; }
            bank_free_data(b);
            __atomic_store_n(&b->state, BANK_NONE, __ATOMIC_RELEASE);
        } else __atomic_store_n(&g_banks[i].state, BANK_READY, __ATOMIC_RELEASE);
        pthread_mutex_unlock(&g_bank_mx);
    }
}

static void *loader_main(void *arg) {
    bank_t *b = arg;
    release_idle_banks((int)(b - g_banks));
    FILE *f = fopen(b->path, "rb");
    long size = 0;
    if (!f || fseek(f, 0, SEEK_END) || (size = ftell(f)) < 32 || fseek(f, 0, SEEK_SET)) goto fail;
    b->maplen = (size_t)size;
    b->map = malloc(b->maplen);
    if (!b->map || fread(b->map, 1, b->maplen, f) != b->maplen) goto fail;
    fclose(f); f = NULL;
    {
        const uint8_t *m = b->map;
        uint32_t ver, ns, nz, doff, flags;
        float gain;
        if (memcmp(m, "SNXB", 4)) goto fail;
        memcpy(&ver, m + 4, 4); memcpy(&ns, m + 8, 4); memcpy(&nz, m + 12, 4); memcpy(&doff, m + 16, 4);
        memcpy(&flags, m + 20, 4); memcpy(&gain, m + 24, 4);
        size_t need = 32 + (size_t)ns * sizeof(ent_t) + (size_t)nz * sizeof(zone_t);
        if (ver != 1 || ns == 0 || ns > 65535 || nz == 0 || nz > 4096 || doff != need || doff > b->maplen) goto fail;
        if (!(gain > 0.05f && gain < 20.0f)) gain = 1.0f;
        b->nsamp = ns; b->nzone = nz; b->flags = flags; b->gain = gain;
        b->ent = (const ent_t *)(m + 32);
        b->zone = (const zone_t *)(m + 32 + ns * sizeof(ent_t));
        for (uint32_t z = 0; z < nz; z++)
            if (b->zone[z].smp >= ns || b->zone[z].lo > b->zone[z].hi || b->zone[z].lv > b->zone[z].hv) goto fail;
        size_t total = 0, dlen = b->maplen - doff;
        for (uint32_t i = 0; i < ns; i++) {
            const ent_t *e = &b->ent[i];
            if (e->frames < 8 || e->frames > 60 * 44100 || (size_t)e->off + 8 > dlen) goto fail;
            if (e->loop_e && (e->loop_e > e->frames || e->loop_s + 64 >= e->loop_e)) goto fail;
            total += e->frames + 1;                       /* +1: a guard frame for interpolation */
        }
        b->head = malloc(total * 4);
        b->hoff = malloc(ns * sizeof(uint32_t));
        if (!b->head || !b->hoff) goto fail;
        size_t at = 0;
        for (uint32_t i = 0; i < ns && !b->stop; i++) {
            const ent_t *e = &b->ent[i];
            const uint8_t *src = m + doff + e->off;
            uint32_t l0, l1;
            memcpy(&l0, src, 4); memcpy(&l1, src + 4, 4);
            if ((size_t)e->off + 8 + (size_t)l0 + l1 > dlen) goto fail;
            int16_t *dst = b->head + at * 2;
            if (decode_channel(src + 8, l0, dst, e->frames)) goto fail;
            if (b->flags & FLAG_MONO)
                for (uint32_t k = 0; k < e->frames; k++) dst[2 * (size_t)k + 1] = dst[2 * (size_t)k];
            else if (decode_channel(src + 8 + l0, l1, dst + 1, e->frames)) goto fail;
            /* guard frame: a looped sample continues into its loop start, a one-shot into silence */
            uint32_t g = e->loop_e ? e->loop_s : e->frames;
            dst[2 * (size_t)e->frames] = g < e->frames ? dst[2 * (size_t)g] : 0;
            dst[2 * (size_t)e->frames + 1] = g < e->frames ? dst[2 * (size_t)g + 1] : 0;
            b->hoff[i] = (uint32_t)at;
            at += e->frames + 1;
        }
        if (b->stop) goto fail;
    }
    {   /* keep the small tables and the decoded audio; drop the compressed file */
        ent_t *e = malloc(b->nsamp * sizeof(ent_t));
        zone_t *z = malloc(b->nzone * sizeof(zone_t));
        if (!e || !z) { free(e); free(z); goto fail; }
        memcpy(e, b->ent, b->nsamp * sizeof(ent_t)); memcpy(z, b->zone, b->nzone * sizeof(zone_t));
        b->ent = e; b->zone = z;
        free(b->map); b->map = NULL;
        b->own_tables = 1;
    }
    __atomic_store_n(&b->state, BANK_READY, __ATOMIC_RELEASE);
    return NULL;
fail:
    if (f) fclose(f);
    __atomic_store_n(&b->state, BANK_FAILED, __ATOMIC_RELEASE);
    return NULL;
}

static void banks_attach(const char *data_dir) {
    pthread_mutex_lock(&g_bank_mx);
    if (g_users++ == 0) snprintf(g_data_dir, sizeof g_data_dir, "%s", data_dir ? data_dir : ".");
    pthread_mutex_unlock(&g_bank_mx);
}

static void bank_request(int i) {     /* start loading if nobody has yet (cheap to call every block) */
    if (i < 0 || i >= NINST || bank_state_of(i) != BANK_NONE) return;
    pthread_mutex_lock(&g_bank_mx);
    bank_t *b = &g_banks[i];
    if (b->state == BANK_NONE) {
        if (b->have_loader) pthread_join(b->loader, NULL);
        memset(b, 0, sizeof *b);
        snprintf(b->path, sizeof b->path, "%s/%s.snx", g_data_dir, INST_FILE[i]);
        b->state = BANK_LOADING;
        b->have_loader = !pthread_create(&b->loader, NULL, loader_main, b);
        if (!b->have_loader) b->state = BANK_FAILED;
    }
    pthread_mutex_unlock(&g_bank_mx);
}

static void banks_detach(void) {
    pthread_mutex_lock(&g_bank_mx);
    if (--g_users == 0) {
        __atomic_store_n(&g_shutdown, 1, __ATOMIC_RELEASE);
        for (int i = 0; i < NINST; i++) g_banks[i].stop = 1;
        for (int i = 0; i < NINST; i++) {
            bank_t *b = &g_banks[i];
            if (b->have_loader) pthread_join(b->loader, NULL);
            bank_free_data(b);
            memset(b, 0, sizeof *b);
            g_bank_users[i] = 0;
        }
        __atomic_store_n(&g_shutdown, 0, __ATOMIC_RELEASE);
    }
    pthread_mutex_unlock(&g_bank_mx);
}

/* ---------- parameters (order = vst/params.json, the VST index) ---------- */
/* VOLUME is parameter 0 on purpose: MPC's host sets parameter 0 to the far end and back on insert and on every STOP
 * (mpc-vst-plugins docs/NOTES.md), which must not change the sound. */
#define NLAYER 4
enum {
    P_VOLUME, P_BROWSE, P_EDIT, P_LAYER_A, P_LAYER_B, P_LAYER_C, P_LAYER_D, P_INST_A, P_INST_B, P_INST_C, P_INST_D,
    P_CUTOFF, P_RESO, P_ENVAMT, P_ATTACK, P_DECAY, P_SUSTAIN, P_RELEASE, P_LFO_RATE, P_LFO_DEPTH,
    P_CHORUS, P_DELAY, P_REVERB, P_TUNE, P_A4, P_THEME, P_FAV, P_PREV, P_NEXT, P_THEME_NEXT,
    P_CAT0, P_CAT_LAST = P_CAT0 + NCAT - 1,
    P_CATEGORY, P_WAVE, P_MIDI_LED, P_LFO_LED, P_CPU, P_LOAD,
    P_LCD_NAME, P_LCD_CAT, P_LCD_TOP, P_LCD_A, P_LCD_B, P_LCD_C, P_LCD_D, P_LCD_LINE, P_FAV_LO, P_FAV_HI, NP
};
static const char *PKEY[NP] = {
    "volume", "browse", "edit_layer", "layer_a", "layer_b", "layer_c", "layer_d", "inst_a", "inst_b", "inst_c", "inst_d",
    "cutoff", "resonance", "env_amt", "attack", "decay", "sustain", "release", "lfo_rate", "lfo_depth",
    "chorus", "delay", "reverb", "tune", "a4_ref", "theme", "fav", "prev", "next", "theme_next",
    "cat_piano", "cat_keys", "cat_pad", "cat_strings", "cat_brass", "cat_choir", "cat_lead", "cat_pluck", "cat_bass",
    "cat_bells", "cat_synth", "cat_fx",
    "category", "wave", "midi_led", "lfo_led", "cpu", "load_state",
    "lcd_name", "lcd_cat", "lcd_top", "lcd_a", "lcd_b", "lcd_c", "lcd_d", "lcd_line", "fav_lo", "fav_hi"
};
static float PDEF[NP];
static void init_defaults(void) {
    static int done;
    if (done) return;
    PDEF[P_VOLUME] = 0.8f; PDEF[P_LAYER_A] = 1;
    PDEF[P_INST_A] = 0; PDEF[P_INST_B] = 7; PDEF[P_INST_C] = 8; PDEF[P_INST_D] = 11;   /* piano, pad, strings, aah choir */
    PDEF[P_CUTOFF] = 1.0f; PDEF[P_ENVAMT] = 0.5f; PDEF[P_DECAY] = 0.5f; PDEF[P_SUSTAIN] = 1.0f; PDEF[P_RELEASE] = 0.35f;
    PDEF[P_LFO_RATE] = 0.5f; PDEF[P_REVERB] = 0.15f; PDEF[P_A4] = 440;
    done = 1;
}
/* knobs whose move the LCD's bottom line reports for a moment */
static int is_knob(int i) { return i == P_VOLUME || (i >= P_CUTOFF && i <= P_A4); }

static float clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
/* envelope times: 1 ms .. 10 s on a log scale (attack), 5 ms .. 10 s (decay, release) */
static float time_of(float p, float lo) { return lo * powf(10.0f / lo, clamp01(p)); }
static float lfo_hz(float p) { return 0.05f * powf(400.0f, clamp01(p)); }
static float cutoff_hz(float p) { return 20.0f * powf(1000.0f, clamp01(p)); }      /* 20 Hz .. 20 kHz */

/* ---------- voices ---------- */
enum { ENV_OFF, ENV_ATTACK, ENV_DECAY, ENV_SUSTAIN, ENV_RELEASE, ENV_KILL };
#define POLY 16                     /* notes that sound at once (each plays on every layer that is on) */
#define MAX_V 80                    /* voice slots: 16 notes x 4 layers plus room for stolen notes to fade */

typedef struct {
    int stage, bank, layer, key, held, sustained;
    uint32_t note_id;
    uint32_t smp, frames, loop_s, loop_e;
    double pos;
    float semis, gain, env;
    float ic1[2], ic2[2];           /* filter state, per channel */
} voice_t;

typedef struct { uint8_t st, d1, d2; } ev_t;

#define DLY_LEN 65536               /* delay line: 1.49 s */
typedef struct {
    float p[NP];
    float prev_mom[NP];             /* momentary buttons act on the press only */
    voice_t v[MAX_V];
    ev_t ev[MAX_EV]; int nev;
    int pedal;
    float bend;
    float vol_smooth;
    uint32_t note_counter;
    int8_t held_bank[NINST];        /* banks this instance holds a "use" on */
    float lfo_ph;
    /* effects */
    float *cho; int cho_w; float cho_ph;
    float *dly; int dly_w; float dly_t, dly_lp[2];
    float *rv; int rv_comb[8], rv_len_c[8], rv_ap[4], rv_len_a[4]; float rv_store[8];
    float bpm;                      /* host tempo (vst.json HAS_LFO_BPM sends "lfo_bpm"); 0 = not known */
    /* what the faceplate shows */
    int rev;                        /* display_rev: bumped when something the page shows changed */
    double clock;                   /* seconds rendered */
    int touch;  double touch_t;     /* the knob moved last, and when (its value shows on the LCD for a moment) */
    int touch_shown;
    double midi_t, wave_t, cpu_t;   /* when the MIDI LED lit, the wave picture changed, the CPU meter was set */
    double cpu_acc; int cpu_n;
    int sounding;
} inst_t;

static int find_key(const char *k) {
    for (int i = 0; i < NP; i++) if (!strcmp(k, PKEY[i])) return i;
    return -1;
}
static int ival(const inst_t *s, int i, int lo, int hi) {
    int x = (int)lroundf(s->p[i]);
    return x < lo ? lo : x > hi ? hi : x;
}
static int layer_on(const inst_t *s, int l) { return s->p[P_LAYER_A + l] > 0.5f; }
static int layer_inst(const inst_t *s, int l) { return ival(s, P_INST_A + l, 0, NINST - 1); }
static int edit_layer(const inst_t *s) { return ival(s, P_EDIT, 0, NLAYER - 1); }

/* memory: this instance holds a use on each bank one of its layers that is on has picked */
static void sync_banks(inst_t *s) {
    int want[NINST] = { 0 };
    for (int l = 0; l < NLAYER; l++) if (layer_on(s, l)) want[layer_inst(s, l)] = 1;
    for (int b = 0; b < NINST; b++) {
        if (want[b] && !s->held_bank[b]) { __atomic_add_fetch(&g_bank_users[b], 1, __ATOMIC_ACQ_REL); s->held_bank[b] = 1; }
        else if (!want[b] && s->held_bank[b]) {
            if (__atomic_sub_fetch(&g_bank_users[b], 1, __ATOMIC_ACQ_REL) == 0) g_bank_idle[b] = now_s();
            s->held_bank[b] = 0;
        }
        if (want[b]) bank_request(b);
    }
}

static int fav_get(const inst_t *s, int inst) {
    int lo = ival(s, P_FAV_LO, 0, 16383), hi = ival(s, P_FAV_HI, 0, 16383);
    return inst < 14 ? (lo >> inst) & 1 : (hi >> (inst - 14)) & 1;
}
static void fav_set(inst_t *s, int inst, int on) {
    int lo = ival(s, P_FAV_LO, 0, 16383), hi = ival(s, P_FAV_HI, 0, 16383);
    if (inst < 14) lo = on ? lo | (1 << inst) : lo & ~(1 << inst);
    else hi = on ? hi | (1 << (inst - 14)) : hi & ~(1 << (inst - 14));
    s->p[P_FAV_LO] = (float)lo; s->p[P_FAV_HI] = (float)hi;
}

/* the derived values the page reads back: the edit layer's instrument, its category and favourite mark */
static void refresh_derived(inst_t *s) {
    int inst = layer_inst(s, edit_layer(s));
    s->p[P_BROWSE] = (float)inst;
    s->p[P_CATEGORY] = (float)INST_CAT[inst];
    s->p[P_FAV] = (float)fav_get(s, inst);
    s->rev++;
}

static void set_edit_inst(inst_t *s, int inst) {
    if (inst < 0) inst = 0;
    if (inst >= NINST) inst = NINST - 1;
    s->p[P_INST_A + edit_layer(s)] = (float)inst;
    sync_banks(s);
    refresh_derived(s);
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

/* polyphony counts notes, not voices: a note on four layers is one note. Over POLY notes, the oldest note (a
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
    if (n < POLY) return;
    int pick = -1;
    for (int k = 0; k < n; k++) if (rel[k] && (pick < 0 || ids[k] < ids[pick])) pick = k;
    if (pick < 0) for (int k = 0; k < n; k++) if (pick < 0 || ids[k] < ids[pick]) pick = k;
    for (int i = 0; i < MAX_V; i++)
        if (s->v[i].stage != ENV_OFF && s->v[i].note_id == ids[pick]) s->v[i].stage = ENV_KILL;
}

static voice_t *free_voice(inst_t *s) {
    voice_t *f = NULL;
    for (int i = 0; i < MAX_V; i++) if (s->v[i].stage == ENV_OFF) return &s->v[i];
    for (int i = 0; i < MAX_V; i++)                    /* all busy: the oldest fading voice */
        if (s->v[i].stage == ENV_KILL && (!f || s->v[i].note_id < f->note_id)) f = &s->v[i];
    if (!f) for (int i = 0; i < MAX_V; i++) if (!f || s->v[i].note_id < f->note_id) f = &s->v[i];
    return f;
}

/* the LCD waveform picture for a note: a shape per kind of sound, small or large by velocity (1..8; 0 = idle) */
static int wave_frame(int cat, int vel) {
    int shape;
    switch (cat) {
    case 0: case 7: case 9: shape = 0; break;            /* piano, pluck, bells: a decaying ring */
    case 1: shape = 1; break;                             /* keys: rounded */
    case 8: shape = 2; break;                             /* bass: sine */
    default: shape = 3; break;                            /* pads, strings, brass, choir, lead, synth, fx: rich */
    }
    return 1 + shape * 2 + (vel >= 90);
}

static void note_on(inst_t *s, int key, int vel) {
    int layers_on = 0;
    for (int l = 0; l < NLAYER; l++) layers_on += layer_on(s, l);
    if (!layers_on) return;
    make_room(s);
    uint32_t id = ++s->note_counter;
    float lg = 1.0f / sqrtf((float)layers_on);           /* stacked layers keep about the level of one */
    int any = 0;
    for (int l = 0; l < NLAYER; l++) {
        if (!layer_on(s, l)) continue;
        int bi = layer_inst(s, l);
        if (bank_state_of(bi) != BANK_READY) continue;    /* still loading: that layer is silent */
        const bank_t *b = &g_banks[bi];
        const zone_t *z = find_zone(b, key, vel);
        if (!z) continue;
        for (int i = 0; i < MAX_V; i++)                   /* the same key again on this layer: the old note rings out */
            if (s->v[i].stage != ENV_OFF && s->v[i].layer == l && s->v[i].key == key && s->v[i].stage < ENV_RELEASE) {
                s->v[i].stage = ENV_RELEASE; s->v[i].held = 0; s->v[i].sustained = 0;
            }
        voice_t *v = free_voice(s);
        const ent_t *e = &b->ent[z->smp];
        memset(v, 0, sizeof *v);
        v->stage = ENV_ATTACK;
        v->bank = bi; v->layer = l; v->key = key; v->held = 1; v->note_id = id;
        v->smp = z->smp; v->frames = e->frames; v->loop_s = e->loop_s; v->loop_e = e->loop_e;
        v->semis = (float)(key - z->root) + z->tune * 0.01f;
        float vn = vel / 127.0f;
        v->gain = b->gain * lg * (0.18f + 0.82f * powf(vn, 1.6f));
        any = 1;
    }
    if (!any) return;
    /* faceplate: the MIDI LED lights; the LCD shows this kind of wave (changed at most ~6 times a second) */
    if (s->p[P_MIDI_LED] < 0.5f) { s->p[P_MIDI_LED] = 1; s->rev++; }
    s->midi_t = s->clock;
    int wf = wave_frame(INST_CAT[layer_inst(s, edit_layer(s))], vel);
    if (wf != (int)s->p[P_WAVE] && s->clock - s->wave_t > 0.16) { s->p[P_WAVE] = (float)wf; s->wave_t = s->clock; s->rev++; }
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
        if (e->d1 == 64) {
            int down = e->d2 >= 64;
            if (!down && s->pedal)
                for (int i = 0; i < MAX_V; i++)
                    if (s->v[i].sustained) { s->v[i].sustained = 0; if (s->v[i].stage < ENV_RELEASE) s->v[i].stage = ENV_RELEASE; }
            s->pedal = down;
        } else if (e->d1 == 120) all_off(s, 1);
        else if (e->d1 == 123) all_off(s, 0);
    } else if (type == 0xE0) {
        int pb = ((e->d2 & 127) << 7 | (e->d1 & 127)) - 8192;
        s->bend = pb / 8192.0f * 2.0f;
    }
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

static void reverb_frame(inst_t *s, float in, float *ol, float *orr) {
    float *buf = s->rv;
    float fb = 0.82f, damp = 0.32f;
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

/* ---------- engine entry points ---------- */
static void *eng_create(const char *data_dir) {
    init_defaults();
    inst_t *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    for (int i = 0; i < NP; i++) s->p[i] = PDEF[i];
    s->vol_smooth = -1;
    s->touch = -1;
    s->cho = calloc(2 * 2048, sizeof(float));
    s->dly = calloc(2 * DLY_LEN, sizeof(float));
    if (!s->cho || !s->dly || !reverb_init(s)) { free(s->cho); free(s->dly); free(s->rv); free(s); return NULL; }
    banks_attach(data_dir);
    sync_banks(s);
    refresh_derived(s);
    return s;
}

static void eng_destroy(void *inst) {
    inst_t *s = inst;
    for (int l = 0; l < NLAYER; l++) s->p[P_LAYER_A + l] = 0;
    sync_banks(s);
    banks_detach();
    free(s->cho); free(s->dly); free(s->rv);
    free(s);
}

static void eng_midi(void *inst, const uint8_t *msg, int len) {
    inst_t *s = inst;
    if (len < 1 || s->nev >= MAX_EV) return;
    ev_t e = { msg[0], len > 1 ? msg[1] : 0, len > 2 ? msg[2] : 0 };
    s->ev[s->nev++] = e;
}

/* step through the instruments of the edit layer's category, wrapping */
static void step_in_category(inst_t *s, int dir) {
    int cur = layer_inst(s, edit_layer(s)), cat = INST_CAT[cur];
    for (int k = 1; k <= NINST; k++) {
        int i = ((cur + dir * k) % NINST + NINST) % NINST;
        if (INST_CAT[i] == cat) { set_edit_inst(s, i); return; }
    }
}

static void eng_set_param(void *inst, const char *key, const char *val) {
    inst_t *s = inst;
    if (!strcmp(key, "lfo_bpm")) { float b = (float)atof(val); if (b > 20 && b < 400) s->bpm = b; return; }
    int i = find_key(key);
    if (i < 0) return;
    float x = (float)atof(val);
    if (x != x) return;
    if (i >= P_CATEGORY && i <= P_LCD_LINE) return;        /* the engine's own displays: not set from outside */
    if (i == P_PREV || i == P_NEXT || (i >= P_CAT0 && i <= P_CAT_LAST)) {
        int press = x > 0.5f && s->prev_mom[i] <= 0.5f;
        s->prev_mom[i] = x;
        s->p[i] = x;
        if (!press) return;
        if (i == P_PREV) step_in_category(s, -1);
        else if (i == P_NEXT) step_in_category(s, 1);
        else {                                                /* a category: its first instrument */
            int c = i - P_CAT0;
            for (int k = 0; k < NINST; k++) if (INST_CAT[k] == c) { set_edit_inst(s, k); break; }
        }
        return;
    }
    if (i == P_BROWSE) { set_edit_inst(s, (int)lroundf(x)); return; }
    if (i == P_FAV) { fav_set(s, layer_inst(s, edit_layer(s)), x > 0.5f); refresh_derived(s); return; }
    s->p[i] = x;
    if (i == P_EDIT || (i >= P_LAYER_A && i <= P_INST_D) || i == P_FAV_LO || i == P_FAV_HI) {
        if (i >= P_LAYER_A && i <= P_INST_D) sync_banks(s);
        refresh_derived(s);
    }
    if (i == P_THEME || i == P_A4) s->rev++;
    if (is_knob(i)) { s->touch = i; s->touch_t = s->clock; s->touch_shown = 1; s->rev++; }
}

static int fmt_time(float sec, char *buf, int n) {
    return sec < 1.0f ? snprintf(buf, n, "%d ms", (int)lroundf(sec * 1000)) : snprintf(buf, n, "%.2f s", sec);
}

static int fmt_display(const inst_t *s, int i, char *buf, int n) {
    float x = s->p[i];
    switch (i) {
    case P_VOLUME: return x <= 0.001f ? snprintf(buf, n, "-inf dB") : snprintf(buf, n, "%.1f dB", 40.0f * log10f(x));
    case P_CUTOFF: { float hz = cutoff_hz(x); return hz < 1000 ? snprintf(buf, n, "%d Hz", (int)lroundf(hz)) : snprintf(buf, n, "%.1f kHz", hz / 1000); }
    case P_RESO: case P_SUSTAIN: case P_LFO_DEPTH: case P_CHORUS: case P_DELAY: case P_REVERB:
        return snprintf(buf, n, "%d%%", (int)lroundf(clamp01(x) * 100));
    case P_ENVAMT: { int v = (int)lroundf((clamp01(x) - 0.5f) * 200); return snprintf(buf, n, "%s%d%%", v > 0 ? "+" : "", v); }
    case P_ATTACK: return fmt_time(time_of(x, 0.001f), buf, n);
    case P_DECAY: case P_RELEASE: return fmt_time(time_of(x, 0.005f), buf, n);
    case P_LFO_RATE: { float hz = lfo_hz(x); return snprintf(buf, n, hz < 10 ? "%.2f Hz" : "%.1f Hz", hz); }
    case P_TUNE: { int t = (int)lroundf(x); return snprintf(buf, n, "%s%d st", t > 0 ? "+" : "", t); }
    case P_A4: return snprintf(buf, n, "%d Hz", (int)lroundf(x));
    }
    return snprintf(buf, n, "%g", x);
}

static const char *SHORT_NAME[] = { "VOLUME", "", "", "", "", "", "", "", "", "", "", "CUTOFF", "RESONANCE", "ENV AMT",
    "ATTACK", "DECAY", "SUSTAIN", "RELEASE", "LFO RATE", "LFO DEPTH", "CHORUS", "DELAY", "REVERB", "TUNE", "A4 REF" };

static void upcase(char *d, const char *src, int n) {
    int k = 0;
    for (; src[k] && k < n - 1; k++) d[k] = (src[k] >= 'a' && src[k] <= 'z') ? src[k] - 32 : src[k];
    d[k] = 0;
}

static int lcd_text(inst_t *s, int i, char *buf, int n) {
    int el = edit_layer(s), inst = layer_inst(s, el), st = bank_state_of(inst);
    char name[64];
    switch (i) {
    case P_LCD_NAME:
        upcase(name, INST_NAME[inst], sizeof name);
        if (st == BANK_FAILED) return snprintf(buf, n, "SAMPLES MISSING");
        return snprintf(buf, n, "%s%s", name, fav_get(s, inst) ? "  \xe2\x98\x85" : "");
    case P_LCD_CAT: return snprintf(buf, n, "%s", CAT_NAME[INST_CAT[inst]]);
    case P_LCD_TOP:
        return snprintf(buf, n, "A4 %d Hz   LAYER %c   %02d/%02d", ival(s, P_A4, 400, 480), 'A' + el, inst + 1, NINST);
    case P_LCD_A: case P_LCD_B: case P_LCD_C: case P_LCD_D: {
        int l = i - P_LCD_A;
        if (!layer_on(s, l)) return snprintf(buf, n, "%c  ---", 'A' + l);
        upcase(name, INST_NAME[layer_inst(s, l)], sizeof name);
        return snprintf(buf, n, "%c  %s", 'A' + l, name);
    }
    case P_LCD_LINE: {
        if (st == BANK_LOADING) return snprintf(buf, n, "LOADING SAMPLES...");
        if (s->touch >= 0 && s->touch_shown) {
            char v[32];
            fmt_display(s, s->touch, v, sizeof v);
            return snprintf(buf, n, "\xe2\x96\xb8 %s  %s", SHORT_NAME[s->touch], v);
        }
        char c[32], r[32];
        fmt_display(s, P_CUTOFF, c, sizeof c); fmt_display(s, P_RESO, r, sizeof r);
        return snprintf(buf, n, "CUTOFF %s   RES %s   POLY %d", c, r, POLY);
    }
    }
    return 0;
}

static int eng_get_param(void *inst, const char *key, char *buf, int n) {
    inst_t *s = inst;
    if (n <= 0) return 0;
    if (!strcmp(key, "display_rev")) return snprintf(buf, n, "%d", s->rev);
    int i = find_key(key);
    if (i >= P_LCD_NAME && i <= P_LCD_LINE) return lcd_text(s, i, buf, n);
    if (i >= 0) return snprintf(buf, n, "%g", s->p[i]);
    size_t kl = strlen(key);
    if (kl > 8 && !strcmp(key + kl - 8, "_display")) {
        char k2[64];
        if (kl - 8 >= sizeof k2) return 0;
        memcpy(k2, key, kl - 8); k2[kl - 8] = 0;
        i = find_key(k2);
        if (i >= 0 && is_knob(i)) return fmt_display(s, i, buf, n);
    }
    return 0;
}

/* ---------- audio ---------- */
static void eng_render(void *inst, int16_t *out, int frames) {
    inst_t *s = inst;
    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (frames > 128) frames = 128;
    sync_banks(s);                                          /* re-request a bank retired while unused */
    for (int i = 0; i < s->nev; i++) handle_event(s, &s->ev[i]);
    s->nev = 0;

    float a4 = s->p[P_A4];
    if (!(a4 >= 400 && a4 <= 480)) a4 = 440;
    float tune = roundf(s->p[P_TUNE]);
    float pitch_base = a4 / 440.0f;
    float att = time_of(s->p[P_ATTACK], 0.001f), dec = time_of(s->p[P_DECAY], 0.005f);
    float rel = time_of(s->p[P_RELEASE], 0.005f);
    float sus = clamp01(s->p[P_SUSTAIN]);
    float att_step = 1.0f / (att * SR);
    float dec_k = 1.0f - expf(-1.0f / (dec * SR * 0.33f));
    float rel_k = 1.0f - expf(-1.0f / (rel * SR * 0.33f));
    float kill_k = 1.0f - expf(-1.0f / (0.003f * SR * 0.33f));

    /* filter: cutoff knob, +/- 5 octaves of envelope (ENV AMT, centre = none), +/- 2 octaves of LFO */
    float cut_oct = log2f(cutoff_hz(s->p[P_CUTOFF]));
    float env_oct = (clamp01(s->p[P_ENVAMT]) - 0.5f) * 10.0f;
    float depth = clamp01(s->p[P_LFO_DEPTH]);
    s->lfo_ph += lfo_hz(s->p[P_LFO_RATE]) * frames / SR;
    if (s->lfo_ph >= 1.0f) s->lfo_ph -= floorf(s->lfo_ph);
    float lfo_oct = depth * 2.0f * sinf(2.0f * 3.14159265f * s->lfo_ph);
    int filter_on = s->p[P_CUTOFF] < 0.995f || fabsf(env_oct) > 0.05f || depth > 0.001f;
    float k_res = 2.0f - 1.9f * clamp01(s->p[P_RESO]);

    float mix[256];
    memset(mix, 0, sizeof(float) * 2 * frames);
    int sounding = 0, held = 0;
    for (int vi = 0; vi < MAX_V; vi++) {
        voice_t *v = &s->v[vi];
        if (v->stage == ENV_OFF) continue;
        if (bank_state_of(v->bank) != BANK_READY) { v->stage = ENV_OFF; continue; }
        sounding++;
        held |= v->held;
        const bank_t *b = &g_banks[v->bank];
        const int16_t *base = b->head + 2 * (size_t)b->hoff[v->smp];
        double inc = pitch_base * exp2((v->semis + tune + s->bend) / 12.0);
        if (inc > 32) inc = 32;
        float g1 = 0, a1 = 0, a2 = 0, a3 = 0;
        for (int f = 0; f < frames; f++) {
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
            uint32_t ip = (uint32_t)v->pos;
            float fr = (float)(v->pos - ip);
            const int16_t *p0 = base + 2 * (size_t)ip;
            float l = (p0[0] + (p0[2] - p0[0]) * fr) * (1.0f / 32768.0f);
            float r = (p0[1] + (p0[3] - p0[1]) * fr) * (1.0f / 32768.0f);
            if (filter_on) {
                if ((f & 15) == 0) {                          /* coefficients every 16 frames */
                    float oct = cut_oct + env_oct * v->env + lfo_oct;
                    float fc = exp2f(oct);
                    if (fc < 20.0f) fc = 20.0f;
                    if (fc > 18000.0f) fc = 18000.0f;
                    g1 = tanf(3.14159265f * fc / SR);
                    a1 = 1.0f / (1.0f + g1 * (g1 + k_res)); a2 = g1 * a1; a3 = g1 * a2;
                }
                float in[2] = { l, r };
                for (int c = 0; c < 2; c++) {                  /* TPT state-variable low-pass, 12 dB */
                    float v3 = in[c] - v->ic2[c];
                    float v1 = a1 * v->ic1[c] + a2 * v3;
                    float v2 = v->ic2[c] + a2 * v->ic1[c] + a3 * v3;
                    v->ic1[c] = 2 * v1 - v->ic1[c];
                    v->ic2[c] = 2 * v2 - v->ic2[c];
                    in[c] = v2;
                }
                l = in[0]; r = in[1];
            }
            float g = v->env * v->gain;
            mix[2 * f] += l * g;
            mix[2 * f + 1] += r * g;
            v->pos += inc;
            if (v->loop_e) {
                while (v->pos >= v->loop_e) v->pos -= (double)(v->loop_e - v->loop_s);
            } else if (v->pos >= v->frames) { v->stage = ENV_OFF; break; }
        }
    }

    /* ---- effects: chorus -> delay -> reverb -> volume -> soft limit (from Fretty) ---- */
    float chorus = clamp01(s->p[P_CHORUS]), delay = clamp01(s->p[P_DELAY]), space = clamp01(s->p[P_REVERB]);
    float vol = clamp01(s->p[P_VOLUME]);
    float target = vol * vol;
    if (s->vol_smooth < 0) s->vol_smooth = target;
    float vstep = (target - s->vol_smooth) / frames;
    float dry = 1.0f - 0.35f * space, wet = space * 0.9f;
    float cho_rate = 2 * 3.14159265f * 0.6f / SR, cho_depth = 0.0035f * SR * chorus, cho_base = 0.012f * SR;
    float beat = 60.0f / (s->bpm > 0 ? s->bpm : 120.0f);
    float dly_target = 0.75f * beat * SR;                     /* a dotted eighth at the host tempo */
    while (dly_target > DLY_LEN - 4) dly_target *= 0.5f;
    if (s->dly_t <= 0) s->dly_t = dly_target;
    float dly_fb = 0.25f + 0.45f * delay, dly_wet = 0.6f * delay;
    float dly_lpk = 1.0f - expf(-2 * 3.14159265f * 3500.0f / SR);
    for (int i = 0; i < frames; i++) {
        float ch[2] = { mix[2 * i], mix[2 * i + 1] };
        if (chorus > 0.001f) {
            s->cho_ph += cho_rate; if (s->cho_ph > 2 * 3.14159265f) s->cho_ph -= 2 * 3.14159265f;
            int w = s->cho_w;
            s->cho[w] = ch[0]; s->cho[2048 + w] = ch[1];
            for (int c = 0; c < 2; c++) {
                float d = cho_base + cho_depth * (1.0f + (c ? cosf(s->cho_ph) : sinf(s->cho_ph)));
                float rp = (float)w - d;
                while (rp < 0) rp += 2048;
                int r0 = (int)rp; float fr = rp - r0;
                const float *buf = s->cho + c * 2048;
                float dl = buf[r0 & 2047] + (buf[(r0 + 1) & 2047] - buf[r0 & 2047]) * fr;
                ch[c] = ch[c] * (1.0f - 0.3f * chorus) + dl * 0.75f * chorus;
            }
            s->cho_w = (w + 1) & 2047;
        }
        if (delay > 0.004f) {                                 /* ping-pong: repeats walk left and right */
            s->dly_t += 0.0005f * (dly_target - s->dly_t);
            float rp = (float)s->dly_w - s->dly_t;
            while (rp < 0) rp += DLY_LEN;
            int r0 = (int)rp; float fr = rp - r0;
            int r1 = r0 + 1 < DLY_LEN ? r0 + 1 : 0;
            float yl = s->dly[r0] + (s->dly[r1] - s->dly[r0]) * fr;
            float yr = s->dly[DLY_LEN + r0] + (s->dly[DLY_LEN + r1] - s->dly[DLY_LEN + r0]) * fr;
            s->dly_lp[0] += dly_lpk * (yl - s->dly_lp[0]);
            s->dly_lp[1] += dly_lpk * (yr - s->dly_lp[1]);
            s->dly[s->dly_w] = 0.5f * (ch[0] + ch[1]) + dly_fb * s->dly_lp[1];
            s->dly[DLY_LEN + s->dly_w] = dly_fb * s->dly_lp[0];
            if (++s->dly_w >= DLY_LEN) s->dly_w = 0;
            ch[0] += dly_wet * yl; ch[1] += dly_wet * yr;
        } else if (s->dly_lp[0] != 0 || s->dly_lp[1] != 0) {
            memset(s->dly, 0, 2 * DLY_LEN * sizeof(float)); s->dly_lp[0] = s->dly_lp[1] = 0; s->dly_t = dly_target;
        }
        float rl = 0, rr = 0;
        if (space > 0.001f) reverb_frame(s, (ch[0] + ch[1]) * 0.03f, &rl, &rr);
        s->vol_smooth += vstep;
        float o[2] = { (ch[0] * dry + rl * wet) * s->vol_smooth, (ch[1] * dry + rr * wet) * s->vol_smooth };
        for (int c = 0; c < 2; c++) {
            float y = o[c];
            if (y > 0.9f) y = 0.9f + 0.1f * tanhf((y - 0.9f) * 10.0f);
            else if (y < -0.9f) y = -0.9f + 0.1f * tanhf((y + 0.9f) * 10.0f);
            out[2 * i + c] = (int16_t)lrintf(y * 32767.0f);
        }
    }

    /* ---- faceplate state: changed only on events, so MPC's screen thread stays idle between them ---- */
    s->clock += frames / SR;
    if (held) s->midi_t = s->clock;                          /* the MIDI LED stays lit while keys are down */
    if (s->p[P_MIDI_LED] > 0.5f && s->clock - s->midi_t > 0.15) { s->p[P_MIDI_LED] = 0; s->rev++; }
    if (sounding == 0 && s->sounding > 0) s->wave_t = s->clock;
    s->sounding = sounding;
    if (sounding == 0 && s->p[P_WAVE] > 0 && s->clock - s->wave_t > 0.5) { s->p[P_WAVE] = 0; s->rev++; }
    int lfo_led = depth > 0.001f;                            /* lit while the LFO does something (no blinking: */
    if (lfo_led != (int)s->p[P_LFO_LED]) { s->p[P_LFO_LED] = (float)lfo_led; s->rev++; }   /* a repaint per blink costs MPC CPU) */
    int ld = bank_state_of(layer_inst(s, edit_layer(s)));
    int lstate = ld == BANK_READY ? 0 : ld == BANK_FAILED ? 2 : 1;
    if (lstate != (int)s->p[P_LOAD]) { s->p[P_LOAD] = (float)lstate; s->rev++; }
    if (s->touch_shown && s->clock - s->touch_t > 1.5) { s->touch_shown = 0; s->rev++; }
    /* CPU meter: this engine's share of the block's time, averaged, shown at most every 2 s */
    struct timespec t1;
    clock_gettime(CLOCK_MONOTONIC, &t1);
    s->cpu_acc += ((t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9) / (frames / SR);
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
