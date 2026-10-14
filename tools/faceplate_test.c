/* Offline test of what the faceplate drives: layers, category buttons, PREV / NEXT, the edit layer, favourites,
 * the filter and effects, and the LCD / LED / wave values the page reads back.
 *   cc -O2 -I<mpc-vst-plugins>/wrapper -Isrc tools/faceplate_test.c -lm -lpthread -o faceplate_test
 *   ./faceplate_test engine
 * Exit status = the number of failed checks. */
#include "../src/engine.c"

static int fails;
#define CHECK(c, ...) do { int ok_ = (c); printf("%s ", ok_ ? "ok  " : "FAIL"); printf(__VA_ARGS__); printf("\n"); fails += !ok_; } while (0)

static const mpc_engine_t *E;
static void *S;
static int16_t buf[256];

static void set(const char *k, const char *v) { E->set_param(S, k, v); }
static void press(const char *k) { set(k, "1"); set(k, "0"); }
static float getf(const char *k) { char b[128]; E->get_param(S, k, b, sizeof b); return (float)atof(b); }
static const char *gets(const char *k) { static char b[128]; b[0] = 0; E->get_param(S, k, b, sizeof b); return b; }
static void note(int on, int key, int vel) { uint8_t m[3] = { (uint8_t)(on ? 0x90 : 0x80), (uint8_t)key, (uint8_t)vel }; E->midi(S, m, 3); }
static double run(double sec) {               /* render; returns the RMS of what came out */
    int n = (int)(sec * 44100 / 128); double acc = 0;
    for (int b = 0; b < n; b++) { E->render(S, buf, 128); for (int i = 0; i < 256; i++) acc += (double)buf[i] * buf[i]; }
    return sqrt(acc / (n * 256.0)) / 32768.0;
}
static void wait_loaded(void) {
    for (int w = 0; w < 4000; w++) {
        int busy = 0;
        for (int l = 0; l < NLAYER; l++) if (layer_on(S, l) && bank_state_of(layer_inst(S, l)) == BANK_LOADING) busy = 1;
        if (!busy) return;
        E->render(S, buf, 128); usleep(2000);
    }
}
static double db(double x) { return 20 * log10(x + 1e-12); }

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    E = mpc_engine();
    S = E->create(argv[1]);
    wait_loaded();
    CHECK(!strcmp(gets("lcd_name"), "GRAND PIANO"), "starts on the grand piano (\"%s\")", gets("lcd_name"));
    CHECK(!strcmp(gets("lcd_cat"), "PIANO") && getf("category") == 0, "category PIANO, lit button 0");
    CHECK(!strcmp(gets("lcd_b"), "B  ---"), "layer B off on the LCD (\"%s\")", gets("lcd_b"));

    int rev0 = (int)getf("display_rev");
    note(1, 60, 100); double a = run(0.3);
    CHECK(a > 0.005, "a note sounds (%.1f dB)", db(a));
    CHECK(getf("midi_led") == 1, "MIDI LED lit on a note");
    CHECK(getf("wave") > 0, "the LCD shows a wave (frame %g)", getf("wave"));
    CHECK((int)getf("display_rev") > rev0, "display_rev moved, so the page repaints");
    note(0, 60, 0); run(1.2);
    CHECK(getf("midi_led") == 0, "MIDI LED off again");
    run(1.5);
    CHECK(getf("wave") == 0, "wave back to idle after the notes end");

    /* categories and PREV / NEXT */
    press("cat_keys");
    CHECK(!strcmp(gets("lcd_name"), "FM E-PIANO") && getf("category") == 1, "KEYS -> its first instrument (\"%s\")", gets("lcd_name"));
    press("next");
    CHECK(!strcmp(gets("lcd_name"), "CLAVISYNTH"), "NEXT steps within KEYS (\"%s\")", gets("lcd_name"));
    press("prev"); press("prev");
    CHECK(!strcmp(gets("lcd_name"), "PIPE ORGAN"), "PREV wraps within KEYS (\"%s\")", gets("lcd_name"));
    CHECK(getf("inst_a") == 6, "the edit layer (A) took it (inst_a %g)", getf("inst_a"));
    CHECK(getf("browse") == 6, "browse follows (%g)", getf("browse"));

    /* layers and the edit layer */
    set("layer_b", "1");
    set("edit_layer", "1");
    CHECK(!strcmp(gets("lcd_name"), "WARM PAD"), "edit layer B shows its instrument (\"%s\")", gets("lcd_name"));
    press("cat_strings");
    CHECK(getf("inst_b") == 8 && getf("inst_a") == 6, "a category press changes layer B only");
    CHECK(!strncmp(gets("lcd_b"), "B  STRING", 9), "LCD cell B (\"%s\")", gets("lcd_b"));
    wait_loaded();
    note(1, 60, 100); double two = run(0.8); note(0, 60, 0); run(1.0);
    CHECK(two > 0.005, "organ + strings together sound (%.1f dB)", db(two));
    int voices = 0;
    note(1, 64, 100); E->render(S, buf, 128);
    for (int i = 0; i < MAX_V; i++) voices += ((inst_t *)S)->v[i].stage != ENV_OFF && ((inst_t *)S)->v[i].key == 64;
    CHECK(voices == 2, "one note plays on both layers (%d voices)", voices);
    note(0, 64, 0); run(1.0);

    /* favourites */
    set("fav", "1");
    CHECK(strstr(gets("lcd_name"), "\xe2\x98\x85") != NULL, "FAV marks the instrument (\"%s\")", gets("lcd_name"));
    CHECK(getf("fav_lo") > 0, "and is stored (fav_lo %g)", getf("fav_lo"));
    set("edit_layer", "0");
    CHECK(getf("fav") == 0, "layer A's instrument is not a favourite");
    set("edit_layer", "1");
    CHECK(getf("fav") == 1, "back on B it still is");

    /* knob readout on the LCD */
    set("cutoff", "0.5");
    CHECK(strstr(gets("lcd_line"), "CUTOFF") != NULL, "a moved knob shows on the LCD (\"%s\")", gets("lcd_line"));
    run(1.7);
    CHECK(strstr(gets("lcd_line"), "POLY") != NULL, "then the line goes back (\"%s\")", gets("lcd_line"));

    /* filter: closing it makes a bright sound quieter */
    set("layer_b", "0"); set("edit_layer", "0"); press("cat_lead"); wait_loaded();
    set("cutoff", "1"); note(1, 72, 120); double open = run(0.6); note(0, 72, 0); run(1.5);
    set("cutoff", "0.35"); note(1, 72, 120); double shut = run(0.6); note(0, 72, 0); run(1.5);
    CHECK(shut < open * 0.6, "CUTOFF closed darkens the lead (%.1f dB -> %.1f dB)", db(open), db(shut));
    set("cutoff", "1");
    set("lfo_depth", "0.5"); run(0.05);
    CHECK(getf("lfo_led") == 1, "LFO LED lit while the LFO is on");
    set("lfo_depth", "0");

    /* effects: delay and reverb leave a tail after the note */
    set("reverb", "0"); set("delay", "0"); set("release", "0");
    note(1, 72, 110); run(0.3); note(0, 72, 0); run(0.2); double dry_tail = run(0.6);
    set("delay", "0.6"); set("reverb", "0.5");
    note(1, 72, 110); run(0.3); note(0, 72, 0); run(0.2); double wet_tail = run(0.6);
    CHECK(wet_tail > dry_tail * 4 + 1e-4, "delay and reverb leave a tail (%.1f dB vs %.1f dB)", db(wet_tail), db(dry_tail));

    /* theme and A4 on the LCD */
    set("a4_ref", "432");
    CHECK(strstr(gets("lcd_top"), "A4 432 Hz") != NULL, "A4 REF on the LCD (\"%s\")", gets("lcd_top"));

    E->destroy(S);
    printf(fails ? "%d FAILED\n" : "all passed\n", fails);
    return fails;
}
