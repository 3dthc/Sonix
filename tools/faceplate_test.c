/* Offline test of what the faceplate and MPC drive: presets (prev / next, categories, favourites, the PRESET list
 * parameter), layers (solo, octave, level), tuning, the filter, effects, settings, and the LCD / LED / waveform values
 * the page reads back.
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
static void seti(const char *k, int v) { char b[16]; snprintf(b, sizeof b, "%d", v); set(k, b); }
static void press(const char *k) { set(k, "1"); set(k, "0"); }
static float getf(const char *k) { char b[128]; E->get_param(S, k, b, sizeof b); return (float)atof(b); }
static const char *gets(const char *k) { static char b[4][160]; static int r; r = (r + 1) & 3; b[r][0] = 0; E->get_param(S, k, b[r], sizeof b[r]); return b[r]; }
static void note(int on, int key, int vel) { uint8_t m[3] = { (uint8_t)(on ? 0x90 : 0x80), (uint8_t)key, (uint8_t)vel }; E->midi(S, m, 3); }
static void cc(int n, int v) { uint8_t m[3] = { 0xB0, (uint8_t)n, (uint8_t)v }; E->midi(S, m, 3); }
static int16_t *rec; static size_t rec_n, rec_cap;
static double run(double sec) {               /* render (recording the left channel); returns the RMS */
    int n = (int)(sec * 44100 / 128); double acc = 0;
    for (int b = 0; b < n; b++) {
        E->render(S, buf, 128);
        for (int i = 0; i < 256; i++) acc += (double)buf[i] * buf[i];
        for (int i = 0; i < 128 && rec_n < rec_cap; i++) rec[rec_n++] = buf[2 * i];
    }
    return sqrt(acc / (n * 256.0)) / 32768.0;
}
static double freq_of(double sec) {           /* zero-crossing frequency of the next `sec` seconds */
    rec_n = 0; run(sec);
    int cross = 0; double first = -1, last = 0;
    for (size_t i = 1; i < rec_n; i++)
        if (rec[i - 1] < 0 && rec[i] >= 0) { double p = (i - 1) + (double)-rec[i - 1] / (rec[i] - rec[i - 1]); if (first < 0) first = p; last = p; cross++; }
    return cross > 1 ? (cross - 1) * 44100.0 / (last - first) : 0;
}
static double db(double x) { return 20 * log10(x + 1e-12); }
static int voices_on(int layer) {
    int n = 0;
    for (int i = 0; i < MAX_V; i++) { voice_t *v = &((inst_t *)S)->v[i]; n += v->stage != ENV_OFF && v->stage < ENV_RELEASE && (layer < 0 || v->layer == layer); }
    return n;
}
static int find_preset(const char *name) { for (int i = 0; i < NPRESET; i++) if (!strcmp(PRESET_NAME[i], name)) return i; return -1; }

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    rec_cap = 44100 * 4; rec = malloc(rec_cap * sizeof *rec);
    E = mpc_engine();
    S = E->create(argv[1]);
    for (int w = 0; w < 6000 && __atomic_load_n(&g_loaded, __ATOMIC_ACQUIRE) < NINST; w++) { E->render(S, buf, 128); usleep(2000); }
    int ready = 0;
    for (int i = 0; i < NINST; i++) ready += bank_state_of(i) == BANK_READY;
    CHECK(ready == NINST, "every instrument loaded (%d of %d)", ready, NINST);

    CHECK(!strcmp(gets("lcd_name"), "CONCERT GRAND"), "starts on preset 1 (\"%s\")", gets("lcd_name"));
    CHECK(strstr(gets("lcd_top"), "PIANO") && strstr(gets("lcd_top"), "001 / 128"), "LCD top \"%s\"", gets("lcd_top"));
    CHECK(!strcmp(gets("lcd_ref"), "A4  440 HZ"), "LCD reference \"%s\"", gets("lcd_ref"));
    CHECK(!strcmp(gets("lcd_b"), "\xe2\x80\x94") && !strcmp(gets("lcd_a"), "GRAND PIANO"), "layer cells \"%s\" / \"%s\"", gets("lcd_a"), gets("lcd_b"));
    CHECK(getf("category") == 0, "PIANO lit");

    /* a note: MIDI LED, layer activity, the waveform animating at most every 100 ms, then everything idle again */
    int rev0 = (int)getf("display_rev");
    note(1, 60, 100); double a = run(0.05);
    CHECK(a > 0.005, "a note sounds at once (%.1f dB in the first 50 ms)", db(a));
    CHECK(getf("midi_led") == 1 && getf("act_a") == 1 && getf("act_b") == 0, "MIDI LED and layer A's activity lit");
    int frames_seen = 0, last = -1, changes = 0;
    char line0[160]; snprintf(line0, sizeof line0, "%s", gets("lcd_line"));
    char top0[160]; snprintf(top0, sizeof top0, "%s", gets("lcd_top"));
    for (int t = 0; t < 10; t++) { run(0.1); int w = (int)getf("wave"); if (w > 0) frames_seen++; if (w != last) changes++; last = w; }
    CHECK(frames_seen >= 9 && changes >= 8, "waveform moves while the note sounds (%d of 10 ticks shown, %d changes)", frames_seen, changes);
    CHECK((int)getf("display_rev") > rev0, "display_rev moved");
    CHECK(!strcmp(line0, gets("lcd_line")) && !strcmp(top0, gets("lcd_top")), "LCD text stays put while playing (MPC's PRESET list stays open)");
    note(0, 60, 0); run(2.5);
    CHECK(getf("midi_led") == 0 && getf("wave") == 0 && getf("act_a") == 0, "LED, activity and waveform idle after the note");
    set("anim", "0"); note(1, 60, 100); run(0.5);
    CHECK(getf("wave") == 0, "ANIMATION off: no waveform");
    note(0, 60, 0); run(2.0); set("anim", "2");

    /* preset browsing */
    press("next");
    CHECK(!strcmp(gets("lcd_name"), "VELVET GRAND") && getf("preset") == 1, "NEXT -> preset 2 (\"%s\")", gets("lcd_name"));
    CHECK(getf("layer_b") == 1 && !strcmp(gets("lcd_b"), "WARM PAD"), "its layer B is the warm pad (\"%s\")", gets("lcd_b"));
    press("prev"); press("prev");
    CHECK(getf("preset") == NPRESET - 1 && getf("category") == NCAT - 1, "PREV wraps to the last preset (%g, \"%s\")", getf("preset"), gets("lcd_name"));
    press("cat_strings");
    CHECK(!strcmp(gets("lcd_name"), "STRING ENSEMBLE") && getf("category") == 3, "STRINGS -> its first preset (\"%s\")", gets("lcd_name"));
    note(1, 60, 100); double st = run(0.5); note(0, 60, 0); run(1.5);
    CHECK(st > 0.003, "and it plays at once (%.1f dB)", db(st));
    int up = find_preset("Upright Ballad");
    seti("preset", up);
    CHECK(getf("preset") == up && !strcmp(gets("lcd_name"), "UPRIGHT BALLAD") && !strstr(gets("lcd_top"), "EDITED"),
          "the PRESET list parameter loads a preset (\"%s\")", gets("lcd_name"));
    set("cutoff", "0.5");
    CHECK(strstr(gets("lcd_top"), "EDITED") != NULL, "an edit shows on the LCD (\"%s\")", gets("lcd_top"));
    CHECK(strstr(gets("lcd_line"), "CUTOFF") != NULL, "the knob's value shows for a moment (\"%s\")", gets("lcd_line"));
    run(1.7);
    CHECK(!strcmp(gets("lcd_line"), PRESET_DESC[up]), "then the preset's description (\"%s\")", gets("lcd_line"));
    seti("preset", up);
    CHECK(getf("cutoff") == 0.5f, "re-sending the current preset keeps the edit (a project reload)");

    /* favourites */
    set("fav", "1");
    CHECK(strstr(gets("lcd_name"), "\xe2\x98\x85") != NULL, "FAV marks the preset (\"%s\")", gets("lcd_name"));
    int fw = (int)getf("fav_0") | (int)getf("fav_1") | (int)getf("fav_2") | (int)getf("fav_3") | (int)getf("fav_4") |
             (int)getf("fav_5") | (int)getf("fav_6") | (int)getf("fav_7") | (int)getf("fav_8") | (int)getf("fav_9");
    CHECK(fw != 0, "and stores it in the project");
    press("next");
    CHECK(getf("fav") == 0, "the next preset isn't a favourite");
    set("fav_only", "1"); press("next");
    CHECK(getf("preset") == up, "FAV LIST: NEXT steps to the favourite (%g)", getf("preset"));
    set("fav_only", "0"); set("fav", "0");

    /* layers: solo, octave, A4 reference (on the sub bass: a near-sine) */
    int sub = find_preset("Sub Bass");
    seti("preset", sub);
    note(1, 45, 110); run(0.3); double f1 = freq_of(1.0); note(0, 45, 0); run(0.5);
    CHECK(fabs(f1 - 110) < 1.0, "A2 plays 110 Hz (%.2f)", f1);
    set("octave_a", "1");
    note(1, 45, 110); run(0.3); double f2 = freq_of(1.0); note(0, 45, 0); run(0.5);
    CHECK(fabs(f2 - 220) < 2.0, "LAYER OCTAVE +1: 220 Hz (%.2f)", f2);
    set("a4_ref", "432");
    note(1, 45, 110); run(0.3); double f3 = freq_of(1.0); note(0, 45, 0); run(0.5);
    CHECK(fabs(f3 - 216) < 2.0, "A4 REF 432: 216 Hz (%.2f)", f3);
    CHECK(!strcmp(gets("lcd_ref"), "A4  432 HZ"), "on the LCD (\"%s\")", gets("lcd_ref"));
    set("a4_ref", "440"); set("transpose", "12");
    note(1, 45, 110); run(0.3); double f4 = freq_of(1.0); note(0, 45, 0); run(0.5);
    CHECK(fabs(f4 - 440) < 4.0, "TRANSPOSE +12 on top: 440 Hz (%.2f)", f4);
    set("transpose", "0");
    cc(1, 127);
    note(1, 45, 110); run(0.3);
    double fmin = 1e9, fmax = 0;
    for (int k = 0; k < 8; k++) { double f = freq_of(0.05); if (f < fmin) fmin = f; if (f > fmax) fmax = f; }
    note(0, 45, 0); run(0.5); cc(1, 0);
    CHECK(fmax / fmin > 1.01, "MOD WHEEL: vibrato (%.1f..%.1f Hz)", fmin, fmax);

    seti("preset", find_preset("Velvet Grand"));
    set("solo_b", "1");
    note(1, 60, 100); E->render(S, buf, 128);
    CHECK(voices_on(1) == 1 && voices_on(-1) == 1, "SOLO B: only layer B plays (%d voices)", voices_on(-1));
    CHECK(strstr(gets("lcat_a"), "MUTED") && strstr(gets("lcat_b"), "SOLO"), "the layer page shows it (\"%s\", \"%s\")", gets("lcat_a"), gets("lcat_b"));
    note(0, 60, 0); run(1.5); set("solo_b", "0");
    note(1, 60, 100); E->render(S, buf, 128);
    CHECK(voices_on(-1) == 3, "all three layers again (%d voices)", voices_on(-1));
    note(0, 60, 0); run(2.0);
    CHECK(!strcmp(gets("lname_b"), "WARM PAD") && !strcmp(gets("lcat_b"), "PAD"), "layer B's mini LCD (\"%s\", \"%s\")", gets("lcat_b"), gets("lname_b"));

    /* polyphony */
    set("polyphony", "4");
    for (int k = 0; k < 8; k++) note(1, 48 + k, 100);
    E->render(S, buf, 128);
    int notes = 0; uint32_t ids[MAX_V];
    for (int i = 0; i < MAX_V; i++) {
        voice_t *v = &((inst_t *)S)->v[i];
        if (v->stage == ENV_OFF || v->stage == ENV_KILL) continue;
        int k; for (k = 0; k < notes && ids[k] != v->note_id; k++) ;
        if (k == notes) ids[notes++] = v->note_id;
    }
    CHECK(notes == 4, "POLYPHONY 4: four notes sound (%d)", notes);
    for (int k = 0; k < 8; k++) note(0, 48 + k, 0);
    run(2.0); set("polyphony", "16");

    /* velocity curve */
    seti("preset", find_preset("Sub Bass"));
    set("vel_curve", "0"); note(1, 45, 40); double soft = run(0.4); note(0, 45, 0); run(0.5);
    set("vel_curve", "2"); note(1, 45, 40); double hard = run(0.4); note(0, 45, 0); run(0.5);
    set("vel_curve", "1");
    CHECK(soft > hard * 1.5, "VELOCITY SOFT plays a gentle note louder than HARD (%.1f vs %.1f dB)", db(soft), db(hard));

    /* filter and effects */
    seti("preset", find_preset("Supersaw Lead"));
    set("delay", "0"); set("reverb", "0");
    set("cutoff", "1"); note(1, 72, 120); double open = run(0.6); note(0, 72, 0); run(1.0);
    set("cutoff", "0.35"); note(1, 72, 120); double shut = run(0.6); note(0, 72, 0); run(1.0);
    CHECK(shut < open * 0.6, "CUTOFF closed darkens the lead (%.1f dB -> %.1f dB)", db(open), db(shut));
    set("cutoff", "1");
    set("lfo_amp", "0.8"); run(0.05);
    CHECK(getf("lfo_led") == 1, "LFO LED lit while the LFO does something");
    set("lfo_amp", "0");
    set("release", "0");
    note(1, 72, 110); run(0.3); note(0, 72, 0); run(0.2); double dry_tail = run(0.6);
    set("delay", "0.6"); set("reverb", "0.5");
    note(1, 72, 110); run(0.3); note(0, 72, 0); run(0.2); double wet_tail = run(0.6);
    CHECK(wet_tail > dry_tail * 4 + 1e-4, "delay and reverb leave a tail (%.1f dB vs %.1f dB)", db(wet_tail), db(dry_tail));

    /* knob readouts */
    char b2[64];
    E->get_param(S, "level_a_display", b2, sizeof b2);
    CHECK(strstr(b2, "dB") != NULL, "a layer level reads in dB (\"%s\")", b2);
    E->get_param(S, "delay_tone_display", b2, sizeof b2);
    CHECK(strstr(b2, "Hz") != NULL, "delay tone reads in Hz (\"%s\")", b2);

    E->destroy(S);
    printf(fails ? "%d FAILED\n" : "all passed\n", fails);
    return fails;
}
