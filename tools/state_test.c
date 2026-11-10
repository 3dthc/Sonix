/* Offline test of project save / reload (no instruments needed).
 *
 *   cc -O2 -I../mpc-vst-plugins/wrapper -Isrc tools/state_test.c -lm -lpthread -o /tmp/state_test && /tmp/state_test engine
 *
 * MPC keeps a plugin's state as the chunk the wrapper gets from get_param("state") and gives back with
 * set_param("state", ...). Projects saved before there was a state were put back one parameter at a time, in index
 * order; that path is tested too, with a pad left off centre by the data wheel (it used to step the preset). */
#include "../src/engine.c"

static const mpc_engine_t *E;
static int16_t buf[2 * 128];
static int fails;
#define CHECK(c, ...) do { int ok_ = (c); printf("%s ", ok_ ? "ok  " : "FAIL"); printf(__VA_ARGS__); printf("\n"); if (!ok_) fails++; } while (0)

static float pf(void *s, const char *k) { return ((inst_t *)s)->p[find_key(k)]; }
static void set(void *s, const char *k, const char *v) { E->set_param(s, k, v); }
static void run(void *s, double sec) { for (int n = (int)(sec * 44100 / 128); n > 0; n--) E->render(s, buf, 128); }

/* the old save: every parameter's value as the wrapper reports it (an option as its index) */
static char saved[NP][32];
static void save_params(void *s) {
    for (int i = 0; i < NP; i++) {
        float x = ((inst_t *)s)->p[i];
        if (PINFO[i].whole) snprintf(saved[i], sizeof saved[i], "%d", (int)lroundf(x));
        else snprintf(saved[i], sizeof saved[i], "%.9g", x);
    }
}
static void replay_params(void *s) {
    for (int i = 0; i < NP; i++) if (PINFO[i].kind != K_MOMENTARY && PINFO[i].kind != K_TEXT) set(s, PKEY[i], saved[i]);
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    E = mpc_engine();
    void *A = E->create(argv[1]);
    usleep((useconds_t)((QUIET_S + 0.2) * 1e6));      /* past the window in which the pads ignore the wheel */
    set(A, "preset", "20");
    set(A, "pick_1", "0.51");                         /* the data wheel after tapping a patch: one preset on */
    int pr = cur_preset(A);
    CHECK(pr == 21 || pr != 20, "the wheel on a pad steps the preset once Sonix has been open a moment (%d)", pr);
    set(A, "cutoff", "0.31"); set(A, "a4_ref", "432"); set(A, "theme", "3"); set(A, "transpose", "-2");
    int ib = (int)pf(A, "inst_b") == 0 ? 3 : 0; char v[16]; snprintf(v, sizeof v, "%d", ib); set(A, "inst_b", v);
    run(A, 0.05);
    CHECK(edited(A), "the edits mark the preset as edited");

    /* 1. a project saved now: the state chunk */
    static char st[8192];
    int n = E->get_param(A, "state", st, sizeof st);
    CHECK(n > 0 && (size_t)n < sizeof st && strstr(st, "preset=") && strstr(st, "cutoff=0.31"), "the state holds the preset and the edits (%d bytes)", n);
    CHECK(!strstr(st, "pick_1=") && !strstr(st, "next=") && !strstr(st, "browse=") && !strstr(st, "look="),
          "but no pads, browse control or displays");
    void *B = E->create(argv[1]);
    set(B, "state", st);
    CHECK(cur_preset(B) == pr, "reload: the same preset (%d, saved %d)", cur_preset(B), pr);
    CHECK(fabsf(pf(B, "cutoff") - 0.31f) < 1e-6f && pf(B, "a4_ref") == 432 && pf(B, "theme") == 3 && pf(B, "transpose") == -2 &&
          (int)pf(B, "inst_b") == ib, "and every edit and setting (cutoff %g, A4 %g, theme %g, transpose %g, layer B %g)",
          pf(B, "cutoff"), pf(B, "a4_ref"), pf(B, "theme"), pf(B, "transpose"), pf(B, "inst_b"));
    CHECK(edited(B), "the preset shows as edited straight away");
    save_params(A);
    replay_params(B);                                 /* if MPC also sets the parameters one by one afterwards */
    CHECK(cur_preset(B) == pr && fabsf(pf(B, "cutoff") - 0.31f) < 1e-6f, "values set again after the state change nothing (%d)", cur_preset(B));
    E->destroy(B);

    /* 2. a project saved before the state: parameters put back one by one, a pad left off centre */
    CHECK(atof(saved[find_key("pick_1")]) != 0.5, "(the old save kept pick_1 off centre: %s)", saved[find_key("pick_1")]);
    void *C = E->create(argv[1]);
    replay_params(C);
    CHECK(cur_preset(C) == pr, "an old project reopens on its own preset (%d, saved %d)", cur_preset(C), pr);
    CHECK(fabsf(pf(C, "cutoff") - 0.31f) < 1e-6f && pf(C, "a4_ref") == 432, "with its edits");
    usleep((useconds_t)((QUIET_S + 0.2) * 1e6));
    int before = cur_preset(C);
    set(C, "next", "0.51");
    CHECK(cur_preset(C) != before, "the wheel works again afterwards (%d -> %d)", before, cur_preset(C));
    E->destroy(C);
    E->destroy(A);
    printf(fails ? "%d FAILED\n" : "all passed\n", fails);
    return fails != 0;
}
