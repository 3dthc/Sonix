/* Lossless check: decode every engine/<instrument>.snx with the engine's own streaming decoder and compare it with the
 * raw 16-bit audio the packer dumped (SONIX_DUMP_RAW=<dir> python3 -I tools/pack_sonix.py writes <dir>/<instrument>.raw).
 * Each sample is decoded twice: straight through, and as a voice plays it (voice_fill: a looped sample runs past its
 * loop end twice, through the decoder state saved for the jump back).
 *   cc -O2 -I<mpc-vst-plugins>/wrapper -Isrc tools/check_codec.c -lm -lpthread -o check_codec && ./check_codec engine <dir>
 * (Adapted from Fretty's tools/check_codec.c.) */
#include "../src/engine.c"

static voice_t vt;

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    void *s = mpc_engine()->create(argv[1]);
    for (int w = 0; w < 6000 && __atomic_load_n(&g_loaded, __ATOMIC_ACQUIRE) < NINST; w++) usleep(5000);
    int bad = 0;
    for (int b = 0; b < NINST; b++) {
        if (bank_state_of(b) != BANK_READY) { printf("FAIL %s: did not load\n", INST_FILE[b]); bad = 1; continue; }
        const bank_t *k = &g_banks[b];
        char path[1400];
        snprintf(path, sizeof path, "%s/%s.raw", argv[2], INST_FILE[b]);
        FILE *f = fopen(path, "rb");
        size_t total = 0;
        for (uint32_t i = 0; i < k->nsamp; i++) total += k->ent[i].frames;
        int16_t *raw = malloc(total * 4);
        size_t got = f ? fread(raw, 4, total, f) : 0;
        if (f) fclose(f);
        int same = got == total, loops = 0;
        size_t at = 0;
        int nch = (k->flags & FLAG_MONO) ? 1 : 2;
        for (uint32_t i = 0; same && i < k->nsamp; i++) {
            const ent_t *e = &k->ent[i];
            const sinfo_t *si = &k->si[i];
            for (int c = 0; c < nch && same; c++) {        /* straight through */
                dec_t d; seek_t z = { 0, 0, 0, 0, 0, 0 };
                int16_t ring[RING];
                dec_seek(&d, si->s[c], si->len[c], &z, 0);
                for (uint32_t f0 = 0; f0 < e->frames && same; f0 += RING) {
                    uint32_t n = e->frames - f0 < RING ? e->frames - f0 : RING;
                    if (dec_run(&d, ring, 0, n)) { same = 0; break; }
                    for (uint32_t j = 0; j < n; j++) if (ring[j] != raw[2 * (at + f0 + j) + c]) { same = 0; break; }
                }
            }
            /* as a voice: past the loop end twice */
            memset(&vt, 0, sizeof vt);
            vt.smp = i; vt.nch = nch;
            seek_t z = { 0, 0, 0, 0, 0, 0 };
            for (int c = 0; c < nch; c++) dec_seek(&vt.d[c], si->s[c], si->len[c], &z, 0);
            uint32_t len = e->loop_e ? e->loop_e + 2 * (e->loop_e - e->loop_s) + 100 : e->frames + 50;
            for (uint32_t n = 0; n < len && same; n += 1000) {
                uint32_t need = n + 1000 < len ? n + 1000 : len;
                if (voice_fill(&vt, k, need)) { same = 0; break; }
                for (uint32_t q = n; q < need; q++) {
                    uint32_t src = q;
                    if (e->loop_e) { while (src >= e->loop_e) src -= e->loop_e - e->loop_s; }
                    for (int c = 0; c < 2; c++) {
                        int16_t want = src < e->frames ? raw[2 * (at + src) + c] : 0;
                        if (vt.ring[nch == 1 ? 0 : c][q & (RING - 1)] != want) { same = 0; break; }
                    }
                }
            }
            loops += e->loop_e != 0;
            at += e->frames;
        }
        printf("%s %-16s %9zu frames, %4u samples (%2d looped), %5u Hz%s, trim %.2f: %s\n", same ? "OK  " : "FAIL",
               INST_FILE[b], total, k->nsamp, loops, k->rate, nch == 1 ? " mono" : "", k->gain,
               same ? "bit-exact, loops seamless" : "differs");
        bad |= !same;
        free(raw);
    }
    mpc_engine()->destroy(s);
    return bad;
}
