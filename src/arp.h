/* arp.h: an arpeggiator, pattern player and chord/strum for a note engine. Shared by Tropix and Sonix (3dthc, MIT).
 *
 * The engine routes its note events through arp_note() and calls arp_block() once per render block with the host's
 * song position (HAS_SONG_POS). Steps sit on the host's ppqPos grid (docs/MIDI_TIMING.md of mpc-vst-plugins: place each
 * step from the song position, a block ahead; never from a clock you count yourself), so the arp stays in time with the
 * project across a loop, a locate or a tempo change. With the transport stopped it runs on its own grid at the tempo.
 *
 * Modes: ARP (the held notes, up / down / up-down / as played / random, over 1-4 octaves), PATTERN (a step pattern
 * built on the lowest held note, with chord hits that play every held note) and CHORD (one key plays a chord, with a strum).
 * The engine supplies the sound through one callback: note(ud, key, vel, on). */
#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>

enum { ARP_OFF, ARP_ARP, ARP_PATTERN, ARP_CHORD, ARP_NMODE };
enum { ARP_UP, ARP_DOWN, ARP_UPDOWN, ARP_PLAYED, ARP_RANDOM, ARP_NDIR };
#define ARP_NRATE 8
#define ARP_MAXK 16
#define ARP_MAXSND 24
#define ARP_MAXPEND 24
#define ARP_REST (-128)
#define ARP_CH 100            /* a pattern step that plays every held note (a chord hit) */
#define ARP_NCHORD 8

typedef struct { const char *name; uint8_t len; int8_t step[16]; uint8_t acc[16]; } arp_pat_t;   /* acc: 0 normal, 1 accent, 2 ghost */

/* the rates, in beats: 1/4, 1/8., 1/8, 1/8T, 1/16, 1/16T, 1/32, 1/32T */
static const double ARP_RATE_BEATS[ARP_NRATE] = { 1.0, 0.75, 0.5, 1.0 / 3, 0.25, 1.0 / 6, 0.125, 1.0 / 12 };
static const char *const ARP_RATE_NAME[ARP_NRATE] = { "1/4", "1/8.", "1/8", "1/8T", "1/16", "1/16T", "1/32", "1/32T" };
static const char *const ARP_MODE_NAME[ARP_NMODE] = { "OFF", "ARP", "PATTERN", "CHORD" };
static const char *const ARP_DIR_NAME[ARP_NDIR] = { "UP", "DOWN", "UP-DOWN", "AS PLAYED", "RANDOM" };
static const char *const ARP_CHORD_NAME[ARP_NCHORD] = { "MAJOR", "MINOR", "DOM 7", "MIN 7", "MAJ 7", "SUS 4", "POWER", "MIN 9" };
static const int8_t ARP_CHORD_IV[ARP_NCHORD][6] = {
    { 0, 4, 7, 12, -1, -1 }, { 0, 3, 7, 12, -1, -1 }, { 0, 4, 7, 10, -1, -1 }, { 0, 3, 7, 10, -1, -1 },
    { 0, 4, 7, 11, -1, -1 }, { 0, 5, 7, 12, -1, -1 }, { 0, 7, 12, -1, -1, -1 }, { 0, 3, 7, 10, 14, -1 } };

typedef struct {
    int mode, dir, rate, oct, gate, swing, latch, pat, chord, strum;   /* the settings; gate and swing in %, strum in ms */
} arp_cfg_t;

typedef void (*arp_note_fn)(void *ud, int key, int vel, int on);

typedef struct {
    arp_cfg_t c;
    int prev_mode;
    uint8_t key[ARP_MAXK], vel[ARP_MAXK], down[ARP_MAXK]; int nk;      /* the held notes, in the order played */
    int pedal;
    struct { uint8_t key; double off; } snd[ARP_MAXSND]; int nsnd;     /* sounding notes and when they end (beats) */
    struct { uint8_t key, vel, id; int delay; } pend[ARP_MAXPEND]; int npend;   /* strummed notes still to start (frames) */
    uint8_t cn[128][8]; uint8_t cnum[128];                              /* CHORD: the notes each key started */
    double sent_to, last_len; int have_sent, prev_playing;
    uint32_t rnd;
} arp_t;

static void arp_init(arp_t *a) {
    memset(a, 0, sizeof *a);
    a->c.mode = ARP_OFF; a->c.rate = 4; a->c.oct = 1; a->c.gate = 70; a->c.swing = 50; a->c.chord = 0;
    a->rnd = 12345u;
}

static void arp_release_all(arp_t *a, arp_note_fn fn, void *ud) {
    for (int i = 0; i < a->nsnd; i++) fn(ud, a->snd[i].key, 0, 0);
    a->nsnd = 0; a->npend = 0;
    for (int k = 0; k < 128; k++) {
        for (int j = 0; j < a->cnum[k]; j++) fn(ud, a->cn[k][j], 0, 0);
        a->cnum[k] = 0;
    }
}

static void arp_clear(arp_t *a, arp_note_fn fn, void *ud) {
    arp_release_all(a, fn, ud);
    a->nk = 0;
}

/* call at the start of each block, before the events: a change of mode drops everything that was sounding */
static int arp_sync(arp_t *a, arp_note_fn fn, void *ud) {
    if (a->c.mode == a->prev_mode) return 0;
    a->prev_mode = a->c.mode;
    arp_clear(a, fn, ud);
    a->have_sent = 0;
    return 1;
}

static int __attribute__((unused)) arp_active(const arp_t *a) { return a->c.mode != ARP_OFF; }

static void arp_sound(arp_t *a, int key, int vel, double off, arp_note_fn fn, void *ud) {
    for (int i = 0; i < a->nsnd; i++)
        if (a->snd[i].key == key) { fn(ud, key, 0, 0); memmove(&a->snd[i], &a->snd[i + 1], (a->nsnd - i - 1) * sizeof a->snd[0]); a->nsnd--; break; }
    if (a->nsnd >= ARP_MAXSND) {
        fn(ud, a->snd[0].key, 0, 0);
        memmove(&a->snd[0], &a->snd[1], (a->nsnd - 1) * sizeof a->snd[0]); a->nsnd--;
    }
    a->snd[a->nsnd].key = (uint8_t)key; a->snd[a->nsnd].off = off; a->nsnd++;
    fn(ud, key, vel < 1 ? 1 : vel > 127 ? 127 : vel, 1);
}

static void arp_release_due(arp_t *a, double t, arp_note_fn fn, void *ud) {
    for (int i = 0; i < a->nsnd;) {
        if (a->snd[i].off <= t + 1e-9) {
            fn(ud, a->snd[i].key, 0, 0);
            memmove(&a->snd[i], &a->snd[i + 1], (a->nsnd - i - 1) * sizeof a->snd[0]); a->nsnd--;
        } else i++;
    }
}

static int arp_find(const arp_t *a, int key) {
    for (int i = 0; i < a->nk; i++) if (a->key[i] == key) return i;
    return -1;
}

static void arp_remove(arp_t *a, int i) {
    memmove(&a->key[i], &a->key[i + 1], a->nk - i - 1); memmove(&a->vel[i], &a->vel[i + 1], a->nk - i - 1);
    memmove(&a->down[i], &a->down[i + 1], a->nk - i - 1); a->nk--;
}

static int arp_phys(const arp_t *a) { int n = 0; for (int i = 0; i < a->nk; i++) n += a->down[i]; return n; }

/* queue a strummed chord: notes[0..n) start `strum` ms apart (a strum of 0 starts them together) */
static void arp_chord_notes(arp_t *a, const int *notes, int n, int vel, double off, int tag, arp_note_fn fn, void *ud) {
    int step = (int)(a->c.strum * 44.1f);
    for (int i = 0; i < n; i++) {
        int d = i * step;
        if (d == 0) {
            if (tag >= 0) { if (a->cnum[tag] < 8) a->cn[tag][a->cnum[tag]++] = (uint8_t)notes[i]; fn(ud, notes[i], vel, 1); }
            else arp_sound(a, notes[i], vel, off, fn, ud);
        } else if (a->npend < ARP_MAXPEND) {
            a->pend[a->npend].key = (uint8_t)notes[i]; a->pend[a->npend].vel = (uint8_t)vel;
            a->pend[a->npend].id = (uint8_t)(tag >= 0 ? tag + 1 : 0);   /* tag + 1: a CHORD key's note; 0: a pattern note */
            a->pend[a->npend].delay = d; a->npend++;
            (void)off;
        }
    }
}

/* A note event from the keyboard. Returns 1 when the arp took it (the engine must not play it itself). */
static int arp_note(arp_t *a, int key, int vel, int on, arp_note_fn fn, void *ud) {
    if (a->c.mode == ARP_OFF) return 0;
    if (a->c.mode == ARP_CHORD) {
        if (on) {
            int notes[8], n = 0;
            const int8_t *iv = ARP_CHORD_IV[a->c.chord % ARP_NCHORD];
            for (int j = 0; j < 6 && iv[j] >= 0; j++) if (key + iv[j] <= 127) notes[n++] = key + iv[j];
            for (int j = 0; j < a->cnum[key]; j++) fn(ud, a->cn[key][j], 0, 0);
            a->cnum[key] = 0;
            arp_chord_notes(a, notes, n, vel, 0, key, fn, ud);
        } else {
            for (int j = 0; j < a->cnum[key]; j++) fn(ud, a->cn[key][j], 0, 0);
            a->cnum[key] = 0;
            for (int i = 0; i < a->npend;)
                if (a->pend[i].id == key + 1) { memmove(&a->pend[i], &a->pend[i + 1], (a->npend - i - 1) * sizeof a->pend[0]); a->npend--; }
                else i++;
        }
        return 1;
    }
    int i = arp_find(a, key);
    if (on) {
        if (a->c.latch && arp_phys(a) == 0) a->nk = 0;                 /* a fresh chord replaces the latched one */
        if (i >= 0) { a->vel[i] = (uint8_t)vel; a->down[i] = 1; return 1; }
        if (a->nk >= ARP_MAXK) arp_remove(a, 0);
        a->key[a->nk] = (uint8_t)key; a->vel[a->nk] = (uint8_t)vel; a->down[a->nk] = 1; a->nk++;
    } else if (i >= 0) {
        if (a->c.latch || a->pedal) a->down[i] = 0;
        else arp_remove(a, i);
    }
    return 1;
}

static void arp_pedal(arp_t *a, int down) {
    a->pedal = down;
    if (!down && !a->c.latch) {
        for (int i = 0; i < a->nk;) { if (!a->down[i]) arp_remove(a, i); else i++; }
    }
}

/* the settings changed: turning LATCH off with no key held lets go */
static void __attribute__((unused)) arp_latch_changed(arp_t *a) {
    if (!a->c.latch && !a->pedal) {
        for (int i = 0; i < a->nk;) { if (!a->down[i]) arp_remove(a, i); else i++; }
    }
}

static void arp_sort(int *v, int n) {
    for (int i = 1; i < n; i++) { int x = v[i], j = i - 1; while (j >= 0 && v[j] > x) { v[j + 1] = v[j]; j--; } v[j + 1] = x; }
}

/* fire step k (at beat t); the notes end at t + gate * L */
static void arp_step(arp_t *a, long k, double t, double L, const arp_pat_t *pats, int npat, arp_note_fn fn, void *ud) {
    if (a->nk == 0) return;
    a->last_len = L * (a->c.gate < 5 ? 5 : a->c.gate > 100 ? 100 : a->c.gate) / 100.0;
    double off = t + a->last_len;
    if (a->c.mode == ARP_ARP) {
        int keys[ARP_MAXK], vels[ARP_MAXK], n = a->nk;
        if (a->c.dir == ARP_PLAYED || a->c.dir == ARP_RANDOM) {
            for (int i = 0; i < n; i++) { keys[i] = a->key[i]; vels[i] = a->vel[i]; }
        } else {
            int idx[ARP_MAXK];
            for (int i = 0; i < n; i++) idx[i] = a->key[i] * 32 + i;     /* sort by key, keeping the velocity's slot */
            arp_sort(idx, n);
            for (int i = 0; i < n; i++) { keys[i] = idx[i] / 32; vels[i] = a->vel[idx[i] % 32]; }
        }
        int oct = a->c.oct < 1 ? 1 : a->c.oct > 4 ? 4 : a->c.oct;
        int m = n * oct;
        long p;
        if (a->c.dir == ARP_RANDOM) { a->rnd = a->rnd * 1664525u + 1013904223u; p = (a->rnd >> 8) % (uint32_t)m; }
        else if (a->c.dir == ARP_DOWN) p = m - 1 - (k % m);
        else if (a->c.dir == ARP_UPDOWN && m > 1) { long per = 2 * m - 2, q = k % per; p = q < m ? q : per - q; }
        else p = k % m;
        int o = (int)(p / n), j = (int)(p % n);
        int key = keys[j] + 12 * o;
        if (key > 127) key -= 12 * ((key - 115) / 12);
        arp_sound(a, key, vels[j], off, fn, ud);
        return;
    }
    if (a->c.mode != ARP_PATTERN || npat < 1) return;
    const arp_pat_t *pt = &pats[a->c.pat % npat];
    int pos = (int)(k % pt->len), st = pt->step[pos];
    if (st == ARP_REST) return;
    int lowest = 127, bvel = 100, keys[ARP_MAXK], n = a->nk;
    for (int i = 0; i < n; i++) { keys[i] = a->key[i]; if (a->key[i] < lowest) { lowest = a->key[i]; bvel = a->vel[i]; } }
    int vel = pt->acc[pos] == 1 ? bvel * 115 / 100 : pt->acc[pos] == 2 ? bvel * 55 / 100 : bvel * 90 / 100;
    if (st == ARP_CH) {
        int notes[ARP_MAXK];
        if (n >= 2) { arp_sort(keys, n); for (int i = 0; i < n; i++) notes[i] = keys[i]; }
        else { n = 3; notes[0] = lowest; notes[1] = lowest + 7; notes[2] = lowest + 12; }
        if (n > 6) n = 6;
        for (int i = 0; i < n; i++) if (notes[i] > 127) notes[i] = 127;
        arp_chord_notes(a, notes, n, vel, off, -1, fn, ud);
        /* a strummed chord's late notes end with the rest of it: their own gate starts when they do */
        return;
    }
    int key = lowest + st;
    if (key < 0) key += 12;
    if (key > 127) key -= 12;
    arp_sound(a, key, vel, off, fn, ud);
}

/* once per render block, after the block's events. ppq < 0 or bpm <= 0: the host gave no position. */
static void arp_block(arp_t *a, double ppq, double bpm, int playing, int frames, double fallback_bpm,
                      const arp_pat_t *pats, int npat, arp_note_fn fn, void *ud) {
    /* strummed notes waiting to start */
    for (int i = 0; i < a->npend;) {
        a->pend[i].delay -= frames;
        if (a->pend[i].delay <= 0) {
            int key = a->pend[i].key, vel = a->pend[i].vel, id = a->pend[i].id;
            if (id) { if (a->cnum[id - 1] < 8) a->cn[id - 1][a->cnum[id - 1]++] = (uint8_t)key; fn(ud, key, vel, 1); }
            else arp_sound(a, key, vel, a->sent_to + a->last_len, fn, ud);
            memmove(&a->pend[i], &a->pend[i + 1], (a->npend - i - 1) * sizeof a->pend[0]); a->npend--;
        } else i++;
    }
    if (a->c.mode != ARP_ARP && a->c.mode != ARP_PATTERN) return;
    double tempo = bpm > 0 ? bpm : fallback_bpm > 0 ? fallback_bpm : 120.0;
    double blk = frames * tempo / (60.0 * 44100.0), start, end;
    int host = playing && ppq >= 0;
    if (host) {
        end = ppq + blk;
        double d = ppq - a->sent_to;
        if (!a->have_sent || !a->prev_playing) start = ppq - 0.002;      /* a start: the step at the playhead plays */
        else if (fabs(d) < 1.0) start = a->sent_to;
        else if (d < 0) start = ppq - 0.008;                              /* a loop wrap or a jump back */
        else start = ppq - 0.002;                                         /* a locate forward */
        if (start < 0) start = 0;
        if (start < a->sent_to && fabs(d) < 1.0) start = a->sent_to;
    } else {
        start = a->have_sent ? a->sent_to : 0;
        end = start + blk;
    }
    a->prev_playing = host;
    double L = ARP_RATE_BEATS[a->c.rate < 0 ? 0 : a->c.rate >= ARP_NRATE ? ARP_NRATE - 1 : a->c.rate];
    double sw = (a->c.swing - 50) / 50.0; if (sw < 0) sw = 0; if (sw > 0.5) sw = 0.5;
    if (end > start) {
        long k0 = (long)floor(start / L) - 1;
        for (long k = k0 < 0 ? 0 : k0;; k++) {
            double t = (k + ((k & 1) ? sw : 0.0)) * L;
            if (t >= end) break;
            if (t < start) continue;
            arp_release_due(a, t, fn, ud);
            arp_step(a, k, t, L, pats, npat, fn, ud);
        }
        arp_release_due(a, end - 1e-9, fn, ud);
    }
    if (!a->have_sent || end > a->sent_to || host) a->sent_to = end;
    a->have_sent = 1;
    /* nothing held and nothing latched: the last notes end with their gate; a stopped transport lets go at once */
    if (a->nk == 0 && a->nsnd) arp_release_due(a, end + 4.0, fn, ud);
}
