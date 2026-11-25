/* The arp inside the real Tropix engine: with the host's song position, an ARP / PATTERN / CHORD hold plays notes on the
 * grid and ends them. Counts note starts by watching voices.   cc -O2 -I<mpc-vst-plugins>/wrapper -Isrc tools/arp_engine_test.c -lm -lpthread
 * ./a.out engine */
#include "../src/engine.c"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static int voices(inst_t *s) { int n = 0; for (int i = 0; i < MAX_V; i++) n += s->v[i].stage != ENV_OFF; return n; }

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    const mpc_engine_t *E = mpc_engine();
    void *h = E->create(argv[1]);
    inst_t *s = h;
    int16_t out[256];
    uint8_t on60[3] = { 0x90, 60, 100 }, on64[3] = { 0x90, 64, 100 }, off60[3] = { 0x80, 60, 0 }, off64[3] = { 0x80, 64, 0 };
    E->set_param(h, "lfo_bpm", "120");
    E->set_param(h, "release", "0.05");
    for (int w = 0; w < 400; w++) { E->render(h, out, 128); usleep(2000); }      /* the first sound loads */

    /* ARP, 1/16, held C and E: one note starts every 0.125 s */
    E->set_param(h, "arp_mode", "1");
    E->set_param(h, "arp_rate", "4");
    E->midi(h, on60, 3); E->midi(h, on64, 3);
    int starts = 0, prev_id = (int)s->note_counter;
    double ppq = 0;
    for (int b = 0; b < 700; b++) {                          /* 700 blocks = 4.06 beats at 120 BPM */
        mpc_engine()->song_pos(h, ppq, 120, 1, 128);
        E->render(h, out, 128);
        ppq += 128 * 120 / (60.0 * 44100);
        if ((int)s->note_counter != prev_id) { starts += (int)s->note_counter - prev_id; prev_id = (int)s->note_counter; }
    }
    CHECK(starts >= 15 && starts <= 17, "arp 1/16 over 4 beats: %d note starts (want 16)", starts);
    E->midi(h, off60, 3); E->midi(h, off64, 3);
    for (int b = 0; b < 400; b++) { mpc_engine()->song_pos(h, ppq, 120, 1, 128); E->render(h, out, 128); ppq += 128 * 120 / (60.0 * 44100); }
    CHECK(voices(s) == 0, "%d voices still sounding after the keys were let go", voices(s));

    /* mode off: notes play directly again */
    E->set_param(h, "arp_mode", "0");
    int c0 = (int)s->note_counter;
    E->midi(h, on60, 3); E->render(h, out, 128);
    CHECK((int)s->note_counter == c0 + 1, "mode off: the key did not play directly");
    E->midi(h, off60, 3);

    /* CHORD: one key, four voices (a minor 7) */
    E->set_param(h, "arp_mode", "3"); E->set_param(h, "arp_chord", "3"); E->set_param(h, "arp_strum", "0");
    E->render(h, out, 128);
    c0 = (int)s->note_counter;
    E->midi(h, on60, 3); E->render(h, out, 128);
    CHECK((int)s->note_counter == c0 + 4, "chord: %d notes started (want 4)", (int)s->note_counter - c0);
    E->midi(h, off60, 3);
    for (int b = 0; b < 200; b++) E->render(h, out, 128);
    CHECK(voices(s) == 0, "chord: voices left after key up");

    /* switching the mode with a key held lets go */
    E->set_param(h, "arp_mode", "1");
    E->midi(h, on60, 3);
    for (int b = 0; b < 40; b++) { mpc_engine()->song_pos(h, ppq, 120, 1, 128); E->render(h, out, 128); ppq += 0.0029; }
    E->set_param(h, "arp_mode", "0");
    for (int b = 0; b < 400; b++) E->render(h, out, 128);
    CHECK(voices(s) == 0, "mode change left %d voices", voices(s));

    E->destroy(h);
    printf(fails ? "arp_engine_test: %d FAILED\n" : "arp_engine_test: all passed\n", fails);
    return fails != 0;
}
