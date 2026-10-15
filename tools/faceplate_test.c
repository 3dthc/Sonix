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
static double run(double sec);
static void press(const char *k) { set(k, "1"); run(0.2); }      /* one tap on MPC's screen, then a moment */
static float getf(const char *k) { char b[128]; E->get_param(S, k, b, sizeof b); return (float)atof(b); }
static void wheel(const char *k, int clicks) {   /* the data wheel on the last-touched pad: 0.01 per click from its value */
    float v = getf(k);
    for (int c = 0; c < (clicks > 0 ? clicks : -clicks); c++) {
        v += clicks > 0 ? 0.01f : -0.01f; char b[32]; snprintf(b, sizeof b, "%g", v); set(k, b);
        v = getf(k) == 0.5f && (v < 0.2f || v > 0.8f) ? 0.5f : v;   /* MPC reads the recentred value back */
    }
}
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
    CHECK(getf("view") == 0 && getf("look") == 0 && getf("bank_1_on") == 1 && !strcmp(gets("bank_4"), "STRINGS"),
          "BROWSE view, PIANO bank lit, banks listed (\"%s\")", gets("bank_4"));

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
    CHECK(!strcmp(gets("lcd_name"), "SALOON UPRIGHT") && getf("bank_1_on") == 1, "PREV wraps inside PIANO (\"%s\")", gets("lcd_name"));
    set("browse", "501");
    CHECK(!strcmp(gets("lcd_name"), "CONCERT GRAND") && getf("browse") == 500, "a data-wheel click forward: the next piano preset, the control back at rest");
    set("browse", "499"); set("browse", "499");
    CHECK(!strcmp(gets("lcd_name"), "PAD LAYER GRAND"), "two clicks back, still inside PIANO (\"%s\")", gets("lcd_name"));
    char bd[64]; E->get_param(S, "browse_display", bd, sizeof bd);
    CHECK(!strcmp(bd, "Pad Layer Grand"), "the LCD's browse control shows the preset's name (\"%s\")", bd);
    /* LCD pads: a tap is exactly 0 or 1 (MPC flips the button it shows); every one counts, an echo within 150 ms doesn't */
    int p0 = (int)getf("preset");
    set("next", "1"); run(0.2); set("next", "0"); run(0.2); set("next", "1"); run(0.2);
    CHECK((int)getf("preset") == (p0 + 3) % 11, "three taps on NEXT (1, 0, 1) step three presets (%d -> %g)", p0, getf("preset"));
    p0 = (int)getf("preset");
    set("prev", "0"); set("prev", "0"); run(0.2);
    CHECK((int)getf("preset") == (p0 + 10) % 11, "a tap and its echo step once (%d -> %g)", p0, getf("preset"));
    CHECK(getf("next") == 0.5f, "a pad rests at 0.5 after a tap (%g)", getf("next"));
    /* the data wheel after touching a pad browses, whatever the pad is */
    p0 = (int)getf("preset");
    wheel("go_layers", 2);
    CHECK((int)getf("preset") == (p0 + 2) % 11 && getf("view") == 0, "wheel on the LAYERS tab: two presets on, the view stays (%d -> %g)", p0, getf("preset"));
    wheel("go_layers", -1);
    CHECK((int)getf("preset") == (p0 + 1) % 11, "and back one (%g)", getf("preset"));
    wheel("fav_tap", 30);
    CHECK(getf("fav") == 0 && (int)getf("preset") == (p0 + 31) % 11, "30 clicks on the FAV pad: no favourite set, 30 presets on, no tap from recentring (%g)", getf("preset"));
    int before = (int)getf("preset");
    press("bpick_4");
    CHECK(getf("bank_4_on") == 1 && getf("bank_1_on") == 0 && (int)getf("preset") == before,
          "tapping the STRINGS bank lights it and lists its patches, the sound stays");
    CHECK(!strcmp(gets("row_1"), "STRING ENSEMBLE") && !strcmp(gets("row_2"), "SLOW STRINGS") && !strcmp(gets("row_11"), "MACHINE STRINGS"),
          "the patch rows, names only (\"%s\", \"%s\")", gets("row_1"), gets("row_2"));
    press("pick_1");
    CHECK(!strcmp(gets("lcd_name"), "STRING ENSEMBLE") && getf("row_1_on") == 1 && getf("row_2_on") == 0,
          "tapping a patch loads it and lights it (\"%s\")", gets("lcd_name"));
    wheel("pick_1", 1);
    CHECK(getf("row_2_on") == 1 && !strcmp(gets("lcd_name"), "SLOW STRINGS"), "the wheel after tapping a patch: the next one (\"%s\")", gets("lcd_name"));
    press("next");
    CHECK(getf("row_3_on") == 1, "NEXT moves the lit patch (\"%s\")", gets("lcd_name"));
    set("pick_1", "0"); run(0.2);
    CHECK(!strcmp(gets("lcd_name"), "STRING ENSEMBLE"), "a tap that sends 0 counts too (\"%s\")", gets("lcd_name"));
    wheel("bpick_4", 1);
    CHECK(getf("bank_5_on") == 1 && !strcmp(gets("lcd_name"), "STRING ENSEMBLE"), "the wheel after tapping a bank steps banks (BRASS lit), the sound stays");
    press("bpick_7"); set("browse", "501");
    CHECK(PRESET_CAT[(int)getf("preset")] == 6, "the data wheel on the name browses the picked bank (PAD: \"%s\")", gets("lcd_name"));
    press("go_layers");
    CHECK(getf("view") == 1 && getf("look") == 1, "LAYERS tab (look %g)", getf("look"));
    int lb = (int)getf("layer_b"); press("cell_b");
    CHECK((int)getf("layer_b") == !lb, "tapping layer B's cell turns it %s", lb ? "on" : "off");
    press("cell_b");
    press("theme_next"); press("theme_next");
    CHECK(getf("theme") == 2 && getf("look") == 5, "THEME NEXT twice: Studio Grey, look %g", getf("look"));
    press("theme_prev"); press("theme_prev"); press("theme_prev");
    CHECK(getf("theme") == NTHEME - 1 && getf("look") == 2 * NTHEME - 1, "THEME PREV wraps to the last colorway (%g)", getf("theme"));
    set("theme", "0"); press("go_browse");
    CHECK(getf("look") == 0, "back to Midnight, BROWSE");
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

    /* favourites: a list of their own, in pages of 11 */
    press("favs_tap");
    CHECK(getf("favs") == 1 && !strcmp(gets("row_1"), "NO FAVORITES YET") && getf("bank_1_on") == 0, "no favourites yet (\"%s\")", gets("row_1"));
    press("fav_tap");
    CHECK(getf("fav") == 1 && strstr(gets("lcd_name"), "\xe2\x98\x85") != NULL, "FAV marks the preset (\"%s\")", gets("lcd_name"));
    int fw = (int)getf("fav_0") | (int)getf("fav_1") | (int)getf("fav_2") | (int)getf("fav_3") | (int)getf("fav_4") |
             (int)getf("fav_5") | (int)getf("fav_6") | (int)getf("fav_7") | (int)getf("fav_8") | (int)getf("fav_9");
    CHECK(fw != 0, "and stores it in the project");
    CHECK(!strcmp(gets("row_1"), "UPRIGHT BALLAD") && getf("row_1_on") == 1, "it is in the favourites list (\"%s\")", gets("row_1"));
    for (int k = 0; k < 14; k++) { seti("preset", 20 + k * 7); press("fav_tap"); }
    seti("preset", 20 + 13 * 7);
    CHECK(strstr(gets("lcd_top"), "PAGE 2 / 2") != NULL && getf("row_4_on") == 1, "15 favourites: page 2 shows the 15th (\"%s\")", gets("lcd_top"));
    press("next");
    CHECK((int)getf("preset") == up && strstr(gets("lcd_top"), "PAGE 1 / 2"), "NEXT wraps to the first favourite (\"%s\")", gets("lcd_top"));
    wheel("pick_3", -1);
    CHECK((int)getf("preset") == 20 + 13 * 7, "the wheel steps back through the favourites (%g)", getf("preset"));
    press("favs_tap");
    { char bk[24]; snprintf(bk, sizeof bk, "bank_%d_on", PRESET_CAT[(int)getf("preset")] + 1);
      CHECK(getf("favs") == 0 && getf(bk) == 1, "★ FAVORITES again: back to the preset's bank (%s)", bk); }
    for (int k = 0; k < 14; k++) { seti("preset", 20 + k * 7); press("fav_tap"); }
    seti("preset", up); press("fav_tap");
    CHECK(getf("fav") == 0, "favourites cleared");

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
    int ib = (int)getf("inst_b");
    set("inst_b_next", "1"); run(0.2); set("inst_b_next", "0"); run(0.2);
    CHECK((int)getf("inst_b") == (ib + 2) % NINST, "layer B's NEXT: two taps, two sounds on (%d -> %g)", ib, getf("inst_b"));
    press("inst_b_prev"); press("inst_b_prev");
    CHECK((int)getf("inst_b") == ib, "and PREV back (%g)", getf("inst_b"));

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
