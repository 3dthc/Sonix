/* Offline test of src/arp.h: order, gate, timing against the host position (loop wrap, tempo change, locate), chords, latch. */
#include <stdio.h>
#include <stdlib.h>
#include "../src/arp.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

typedef struct { int key, vel, on; double t; } evt_t;
static evt_t ev[4096]; static int nev; static double now;
static void note(void *ud, int key, int vel, int on) { (void)ud; if (nev < 4096) ev[nev++] = (evt_t){ key, vel, on, now }; }

static const arp_pat_t PATS[] = {
    { "t1", 4, { 0, ARP_REST, 7, ARP_CH }, { 1, 0, 0, 2 } },
    { "t2", 8, { ARP_CH, ARP_REST, ARP_REST, ARP_CH, ARP_REST, ARP_REST, ARP_CH, ARP_REST }, { 0 } },
};

static arp_t A;
static void run(double ppq0, double bpm, int blocks, int playing) {
    double ppq = ppq0;
    for (int b = 0; b < blocks; b++) {
        now = ppq;
        arp_block(&A, playing ? ppq : -1, playing ? bpm : 0, playing, 128, bpm, PATS, 2, note, 0);
        ppq += 128 * bpm / (60.0 * 44100.0);
    }
}
static int ons(int key) { int n = 0; for (int i = 0; i < nev; i++) n += ev[i].on && (key < 0 || ev[i].key == key); return n; }
static int offs(int key) { int n = 0; for (int i = 0; i < nev; i++) n += !ev[i].on && (key < 0 || ev[i].key == key); return n; }

int main(void) {
    /* up over two octaves: C E G, then C+12 ... */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.dir = ARP_UP; A.c.oct = 2; A.c.rate = 4;
    arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 64, 100, 1, note, 0); arp_note(&A, 60, 90, 1, note, 0); arp_note(&A, 67, 80, 1, note, 0);
    run(0.0, 120, 700, 1);                                   /* 120 BPM: 700 blocks = 4.06 beats = 16 steps */
    int want[] = { 60, 64, 67, 72, 76, 79, 60, 64, 67, 72, 76, 79 }, j = 0;
    for (int i = 0; i < nev && j < 12; i++) if (ev[i].on) { CHECK(ev[i].key == want[j], "up order %d: got %d want %d", j, ev[i].key, want[j]); j++; }
    CHECK(j == 12, "only %d notes", j);
    CHECK(ons(-1) - offs(-1) <= 1, "on/off balance %d/%d", ons(-1), offs(-1));
    /* the steps are 0.25 beat apart, on the grid, no more than one block early */
    double last = -1; int bad = 0;
    for (int i = 0; i < nev; i++) if (ev[i].on) { if (last >= 0 && fabs((ev[i].t - last) - 0.25) > 0.0125) bad++; last = ev[i].t; }
    CHECK(bad == 0, "%d steps off the 1/16 grid", bad);

    /* a loop wrap: 15.9..16 then back to 0: every 16th once, none doubled or lost */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.dir = ARP_UP; A.c.rate = 4; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0);
    run(14.0, 120, 350, 1);                                  /* to ~16.0 */
    double endp = 14.0 + 350 * 128 * 120 / (60.0 * 44100.0);
    int before = ons(-1);
    run(endp - 16.0 > 0 ? endp - 16.0 : 0.0, 120, 350, 1);   /* wrapped to the start */
    CHECK(before >= 7 && before <= 9, "before wrap %d (want 8)", before);
    CHECK(ons(-1) - before >= 7 && ons(-1) - before <= 9, "after wrap %d (want 8)", ons(-1) - before);

    /* a tempo change that steps the position back a little must not double a step */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.rate = 4; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0);
    run(0.0, 120, 200, 1);
    int n1 = ons(-1);
    run(1.15, 122, 1, 1);                                    /* 1.15 vs the ~1.16 reached: a small step back */
    CHECK(ons(-1) == n1 || ons(-1) == n1 + 1, "a step back doubled steps (%d -> %d)", n1, ons(-1));

    /* gate: notes end before the next step at 50% */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.rate = 4; A.c.gate = 50; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0);
    run(0.0, 120, 100, 1);
    double t_on = -1, t_off = -1;
    for (int i = 0; i < nev; i++) { if (ev[i].on && t_on < 0 && ev[i].t > 0.1) t_on = ev[i].t; if (!ev[i].on && t_on >= 0 && t_off < 0) t_off = ev[i].t; }
    CHECK(t_off > t_on && t_off - t_on < 0.2, "gate 50%%: on %.3f off %.3f", t_on, t_off);

    /* stopped transport: the arp runs on its own at the tempo */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.rate = 2; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0);
    run(0.0, 120, 700, 0);
    CHECK(ons(-1) >= 7 && ons(-1) <= 9, "free-running 1/8 for ~4 beats: %d notes", ons(-1));

    /* latch: keys let go, the arp keeps going; a new key replaces the chord */
    arp_init(&A); A.c.mode = ARP_ARP; A.c.latch = 1; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0); arp_note(&A, 64, 100, 1, note, 0);
    arp_note(&A, 60, 0, 0, note, 0); arp_note(&A, 64, 0, 0, note, 0);
    run(0.0, 120, 400, 1);
    CHECK(ons(60) + ons(64) > 3, "latched arp stopped");
    arp_note(&A, 70, 100, 1, note, 0);
    CHECK(A.nk == 1 && A.key[0] == 70, "new key did not replace the latched chord (nk %d)", A.nk);

    /* sustain pedal holds the chord like latch */
    arp_init(&A); A.c.mode = ARP_ARP; arp_sync(&A, note, 0);
    arp_note(&A, 60, 100, 1, note, 0); arp_pedal(&A, 1); arp_note(&A, 60, 0, 0, note, 0);
    CHECK(A.nk == 1, "pedal did not hold");
    arp_pedal(&A, 0);
    CHECK(A.nk == 0, "pedal up did not release");

    /* pattern: the lowest note plus the step; chord hits play all held notes */
    arp_init(&A); A.c.mode = ARP_PATTERN; A.c.pat = 0; A.c.rate = 4; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0); arp_note(&A, 64, 100, 1, note, 0);
    run(0.0, 120, 180, 1);                                   /* one pass of the 4-step pattern */
    CHECK(ons(60) >= 2 && ons(67) >= 1 && ons(64) >= 1, "pattern notes: 60 x%d 67 x%d 64 x%d", ons(60), ons(67), ons(64));

    /* chord mode with a strum: notes start apart, a key up ends them all */
    arp_init(&A); A.c.mode = ARP_CHORD; A.c.chord = 3; A.c.strum = 30; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0);
    run(0.0, 120, 45, 0);
    CHECK(ons(60) == 1 && ons(63) == 1 && ons(67) == 1 && ons(70) == 1, "minor 7 chord: %d %d %d %d", ons(60), ons(63), ons(67), ons(70));
    arp_note(&A, 60, 0, 0, note, 0);
    CHECK(offs(-1) == 4, "chord key up ended %d notes", offs(-1));

    /* changing the mode lets go of everything */
    arp_init(&A); A.c.mode = ARP_CHORD; arp_sync(&A, note, 0); nev = 0;
    arp_note(&A, 60, 100, 1, note, 0); int o = ons(-1);
    A.c.mode = ARP_OFF; arp_sync(&A, note, 0);
    CHECK(offs(-1) == o, "mode change left %d notes", o - offs(-1));

    printf(fails ? "arp_test: %d FAILED\n" : "arp_test: all passed\n", fails);
    return fails != 0;
}
