/* Lossless check: decode every engine/<instrument>.snx with the engine's own loader and compare it with the raw 16-bit
 * audio the packer dumped (SONIX_DUMP_RAW=<dir> python3 -I tools/pack_sonix.py writes <dir>/<instrument>.raw).
 *   cc -O2 -I<mpc-vst-plugins>/wrapper -Isrc tools/check_codec.c -lm -lpthread -o check_codec && ./check_codec engine <dir>
 * (Adapted from Fretty's tools/check_codec.c.) */
#include "../src/engine.c"
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    void *s = mpc_engine()->create(argv[1]);
    int bad = 0;
    for (int b = 0; b < NINST; b++) {
        bank_request(b);
        while (bank_state_of(b) == BANK_LOADING) usleep(10000);
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
        int same = got == total;
        size_t at = 0;
        for (uint32_t i = 0; same && i < k->nsamp; i++) {      /* the decoded copy has a guard frame after each sample */
            same = !memcmp(raw + 2 * at, k->head + 2 * (size_t)k->hoff[i], (size_t)k->ent[i].frames * 4);
            at += k->ent[i].frames;
        }
        printf("%s %-16s %9zu frames, %4u samples, trim %.2f: %s\n", same ? "OK  " : "FAIL", INST_FILE[b], total,
               k->nsamp, k->gain, same ? "decode bit-exact" : "differs");
        bad |= !same;
        free(raw);
    }
    mpc_engine()->destroy(s);
    return bad;
}
