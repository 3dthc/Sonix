/* Sonix: a ROMpler for MPC OS (mpc_engine, see mpc-vst-plugins wrapper/engine.h).
 *
 * Milestone 1, the single-layer sampler: INSTRUMENT picks one of the packed instruments (engine/<name>.snx, built by
 * tools/pack_sonix.py). Each note finds its zone by key and velocity, is repitched from the zone's root (plus the
 * zone's own tuning correction, TUNE in semitones and the A4 REF pitch standard), loops through its sustain loop if
 * it has one, and is shaped by an ADSR envelope. 16 notes of polyphony; sustain pedal and pitch bend (+/-2).
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
#define POLY 16                     /* notes that sound at once */
#define MAX_V 24                    /* voice slots: POLY plus room for stolen notes to fade out */
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
 * (mpc-vst-plugins docs/NOTES.md), which would reload the instrument if INSTRUMENT sat there. */
enum { P_VOLUME, P_INSTRUMENT, P_ATTACK, P_DECAY, P_SUSTAIN, P_RELEASE, P_TUNE, P_A4, NP };
static const char *PKEY[NP] = { "volume", "instrument", "attack", "decay", "sustain", "release", "tune", "a4_ref" };
static const float PDEF[NP] = { 0.8f, 0, 0.0f, 0.5f, 1.0f, 0.35f, 0, 440 };

/* envelope times: 1 ms .. 10 s on a log scale (attack), 5 ms .. 10 s (decay, release) */
static float time_of(float p, float lo) { return lo * powf(10.0f / lo, p < 0 ? 0 : p > 1 ? 1 : p); }

/* ---------- voices ---------- */
enum { ENV_OFF, ENV_ATTACK, ENV_DECAY, ENV_SUSTAIN, ENV_RELEASE, ENV_KILL };

typedef struct {
    int stage, bank, key, held, sustained;
    uint32_t smp, frames, loop_s, loop_e;
    double pos;
    float semis;          /* key - root + the zone's tuning correction */
    float gain, env;
    uint64_t age;
} voice_t;

typedef struct { uint8_t st, d1, d2; } ev_t;

typedef struct {
    float p[NP];
    voice_t v[MAX_V];
    ev_t ev[MAX_EV]; int nev;
    int pedal;
    float bend;           /* semitones */
    float vol_smooth;
    int cur_bank, bank_held;
    uint64_t clock;
} inst_t;

static int find_key(const char *k) {
    for (int i = 0; i < NP; i++) if (!strcmp(k, PKEY[i])) return i;
    return -1;
}

static int inst_index(const inst_t *s) {
    int i = (int)lroundf(s->p[P_INSTRUMENT]);
    return i < 0 ? 0 : i >= NINST ? NINST - 1 : i;
}

static void bank_hold(inst_t *s, int bank) {
    if (s->bank_held == bank) return;
    if (s->bank_held >= 0 && __atomic_sub_fetch(&g_bank_users[s->bank_held], 1, __ATOMIC_ACQ_REL) == 0)
        g_bank_idle[s->bank_held] = now_s();
    if (bank >= 0) __atomic_add_fetch(&g_bank_users[bank], 1, __ATOMIC_ACQ_REL);
    s->bank_held = bank;
}

static const zone_t *find_zone(const bank_t *b, int key, int vel) {
    const zone_t *best = NULL;
    int best_d = 1 << 30;
    for (uint32_t i = 0; i < b->nzone; i++) {
        const zone_t *z = &b->zone[i];
        if (vel < z->lv || vel > z->hv) continue;
        if (key >= z->lo && key <= z->hi) return z;
        int d = abs(key - z->root);                    /* outside every range: the nearest root of this layer */
        if (d < best_d) { best_d = d; best = z; }
    }
    if (best) return best;
    for (uint32_t i = 0; i < b->nzone; i++)            /* no layer for this velocity: any zone, nearest root */
        if (abs(key - b->zone[i].root) < best_d) { best_d = abs(key - b->zone[i].root); best = &b->zone[i]; }
    return best;
}

static voice_t *alloc_voice(inst_t *s) {
    int sounding = 0;
    voice_t *free_v = NULL, *oldest = NULL, *oldest_rel = NULL;
    for (int i = 0; i < MAX_V; i++) {
        voice_t *v = &s->v[i];
        if (v->stage == ENV_OFF) { if (!free_v) free_v = v; continue; }
        if (v->stage == ENV_KILL) continue;
        sounding++;
        if (!oldest || v->age < oldest->age) oldest = v;
        if (v->stage == ENV_RELEASE && (!oldest_rel || v->age < oldest_rel->age)) oldest_rel = v;
    }
    if (sounding >= POLY) {                            /* steal: a released note first, else the oldest */
        voice_t *victim = oldest_rel ? oldest_rel : oldest;
        if (victim) victim->stage = ENV_KILL;          /* fades out in ~3 ms */
    }
    if (free_v) return free_v;
    /* every slot busy (stolen notes still fading): reuse the oldest fading one */
    voice_t *f = NULL;
    for (int i = 0; i < MAX_V; i++)
        if (s->v[i].stage == ENV_KILL && (!f || s->v[i].age < f->age)) f = &s->v[i];
    return f ? f : &s->v[0];
}

static void note_on(inst_t *s, int key, int vel) {
    int bi = s->cur_bank;
    if (bank_state_of(bi) != BANK_READY) return;       /* still loading: silent */
    const bank_t *b = &g_banks[bi];
    const zone_t *z = find_zone(b, key, vel);
    if (!z) return;
    for (int i = 0; i < MAX_V; i++)                    /* the same key again: let the old note ring out */
        if (s->v[i].stage != ENV_OFF && s->v[i].key == key && s->v[i].stage < ENV_RELEASE) {
            s->v[i].stage = ENV_RELEASE; s->v[i].held = 0; s->v[i].sustained = 0;
        }
    voice_t *v = alloc_voice(s);
    const ent_t *e = &b->ent[z->smp];
    memset(v, 0, sizeof *v);
    v->stage = ENV_ATTACK;
    v->bank = bi; v->key = key; v->held = 1;
    v->smp = z->smp; v->frames = e->frames; v->loop_s = e->loop_s; v->loop_e = e->loop_e;
    v->semis = (float)(key - z->root) + z->tune * 0.01f;
    float vn = vel / 127.0f;
    v->gain = b->gain * (0.18f + 0.82f * powf(vn, 1.6f));
    v->env = 0;
    v->age = ++s->clock;
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

/* ---------- engine entry points ---------- */
static void *eng_create(const char *data_dir) {
    inst_t *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    for (int i = 0; i < NP; i++) s->p[i] = PDEF[i];
    s->bank_held = -1;
    s->vol_smooth = -1;
    banks_attach(data_dir);
    s->cur_bank = inst_index(s);
    bank_hold(s, s->cur_bank);
    bank_request(s->cur_bank);
    return s;
}

static void eng_destroy(void *inst) {
    inst_t *s = inst;
    bank_hold(s, -1);
    banks_detach();
    free(s);
}

static void eng_midi(void *inst, const uint8_t *msg, int len) {
    inst_t *s = inst;
    if (len < 1 || s->nev >= MAX_EV) return;
    ev_t e = { msg[0], len > 1 ? msg[1] : 0, len > 2 ? msg[2] : 0 };
    s->ev[s->nev++] = e;
}

static void eng_set_param(void *inst, const char *key, const char *val) {
    inst_t *s = inst;
    int i = find_key(key);
    if (i < 0) return;
    float x = (float)atof(val);
    if (x != x) return;
    s->p[i] = x;
    if (i == P_INSTRUMENT) {
        s->cur_bank = inst_index(s);
        bank_hold(s, s->cur_bank);
        bank_request(s->cur_bank);
    }
}

static int fmt_time(float sec, char *buf, int n) {
    return sec < 1.0f ? snprintf(buf, n, "%d ms", (int)lroundf(sec * 1000)) : snprintf(buf, n, "%.2f s", sec);
}

static int fmt_display(const inst_t *s, int i, char *buf, int n) {
    float x = s->p[i];
    switch (i) {
    case P_ATTACK: return fmt_time(time_of(x, 0.001f), buf, n);
    case P_DECAY: case P_RELEASE: return fmt_time(time_of(x, 0.005f), buf, n);
    case P_SUSTAIN: return snprintf(buf, n, "%d%%", (int)lroundf(x * 100));
    case P_TUNE: { int t = (int)lroundf(x); return snprintf(buf, n, "%s%d st", t > 0 ? "+" : "", t); }
    case P_A4: return snprintf(buf, n, "%d Hz", (int)lroundf(x));
    case P_VOLUME: {
        if (x <= 0.001f) return snprintf(buf, n, "-inf dB");
        return snprintf(buf, n, "%.1f dB", 40.0f * log10f(x));      /* the knob's gain is x^2 */
    }
    }
    return snprintf(buf, n, "%g", x);
}

static int eng_get_param(void *inst, const char *key, char *buf, int n) {
    inst_t *s = inst;
    if (n <= 0) return 0;
    if (!strcmp(key, "status")) {
        int st = bank_state_of(s->cur_bank);
        return snprintf(buf, n, "%s", st == BANK_READY ? "READY" : st == BANK_FAILED ? "SAMPLES MISSING" : "LOADING...");
    }
    int i = find_key(key);
    if (i >= 0) return snprintf(buf, n, "%g", s->p[i]);
    size_t kl = strlen(key);
    if (kl > 8 && !strcmp(key + kl - 8, "_display")) {
        char k2[64];
        if (kl - 8 >= sizeof k2) return 0;
        memcpy(k2, key, kl - 8); k2[kl - 8] = 0;
        i = find_key(k2);
        if (i == P_INSTRUMENT) return snprintf(buf, n, "%s", INST_NAME[inst_index(s)]);
        if (i >= 0) return fmt_display(s, i, buf, n);
    }
    return 0;
}

static void eng_render(void *inst, int16_t *out, int frames) {
    inst_t *s = inst;
    if (frames > 128) frames = 128;
    bank_request(s->cur_bank);                          /* picked again after it was retired: reload */
    for (int i = 0; i < s->nev; i++) handle_event(s, &s->ev[i]);
    s->nev = 0;

    float a4 = s->p[P_A4];
    if (!(a4 >= 400 && a4 <= 480)) a4 = 440;
    float tune = roundf(s->p[P_TUNE]);
    float pitch_base = a4 / 440.0f;
    float att = time_of(s->p[P_ATTACK], 0.001f), dec = time_of(s->p[P_DECAY], 0.005f);
    float rel = time_of(s->p[P_RELEASE], 0.005f);
    float sus = s->p[P_SUSTAIN] < 0 ? 0 : s->p[P_SUSTAIN] > 1 ? 1 : s->p[P_SUSTAIN];
    float att_step = 1.0f / (att * SR);
    float dec_k = 1.0f - expf(-1.0f / (dec * SR * 0.33f));     /* ~3 time constants = the knob's time */
    float rel_k = 1.0f - expf(-1.0f / (rel * SR * 0.33f));
    float kill_k = 1.0f - expf(-1.0f / (0.003f * SR * 0.33f));

    float mix[256];
    memset(mix, 0, sizeof(float) * 2 * frames);
    for (int vi = 0; vi < MAX_V; vi++) {
        voice_t *v = &s->v[vi];
        if (v->stage == ENV_OFF) continue;
        if (bank_state_of(v->bank) != BANK_READY) { v->stage = ENV_OFF; continue; }   /* bank retired or reloading */
        const bank_t *b = &g_banks[v->bank];
        const int16_t *base = b->head + 2 * (size_t)b->hoff[v->smp];
        double inc = pitch_base * exp2((v->semis + tune + s->bend) / 12.0);
        if (inc > 32) inc = 32;
        for (int f = 0; f < frames; f++) {
            switch (v->stage) {
            case ENV_ATTACK:
                v->env += att_step;
                if (v->env >= 1.0f) { v->env = 1.0f; v->stage = ENV_DECAY; }
                break;
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
            const int16_t *p0 = base + 2 * (size_t)ip;      /* p0[2], p0[3]: the next frame (a guard frame at the end) */
            float l = p0[0] + (p0[2] - p0[0]) * fr;
            float r = p0[1] + (p0[3] - p0[1]) * fr;
            float g = v->env * v->gain * (1.0f / 32768.0f);
            mix[2 * f] += l * g;
            mix[2 * f + 1] += r * g;
            v->pos += inc;
            if (v->loop_e) {
                while (v->pos >= v->loop_e) v->pos -= (double)(v->loop_e - v->loop_s);
            } else if (v->pos >= v->frames) { v->stage = ENV_OFF; break; }
        }
    }

    float vol = s->p[P_VOLUME] < 0 ? 0 : s->p[P_VOLUME] > 1 ? 1 : s->p[P_VOLUME];
    float target = vol * vol;
    if (s->vol_smooth < 0) s->vol_smooth = target;
    float vstep = (target - s->vol_smooth) / frames;
    for (int f = 0; f < frames; f++) {
        s->vol_smooth += vstep;
        for (int c = 0; c < 2; c++) {
            float y = mix[2 * f + c] * s->vol_smooth;
            if (y > 0.9f) y = 0.9f + 0.1f * tanhf((y - 0.9f) * 10.0f);       /* soft knee instead of a hard clip */
            else if (y < -0.9f) y = -0.9f + 0.1f * tanhf((y + 0.9f) * 10.0f);
            out[2 * f + c] = (int16_t)lrintf(y * 32767.0f);
        }
    }
}

static const mpc_engine_t ENGINE = { eng_create, eng_destroy, eng_midi, eng_set_param, eng_get_param, eng_render, NULL, NULL };
const mpc_engine_t *mpc_engine(void) { return &ENGINE; }
