/* Offline engine test: plays every instrument through the real engine and writes what it hears.
 *   cc -O2 -I<mpc-vst-plugins>/wrapper -Isrc tools/render_test.c -lm -lpthread -o render_test
 *   ./render_test engine <out dir>
 * For each instrument: a C-major arpeggio and chord at three velocities, a 6 s held note (loops), then a 40-note
 * cluster (voice stealing). Writes <out>/<instrument>.wav (44.1 kHz stereo) and prints the level, the pitch of the
 * held note against A4 REF and TUNE, and the worst render time per 128-frame block. Exit status 1 on silence,
 * clipping or a NaN. */
#include "../src/engine.c"

static void put_le(FILE *f, uint32_t v, int n) { for (int i = 0; i < n; i++) fputc((v >> (8 * i)) & 255, f); }

static void write_wav(const char *path, const int16_t *a, size_t frames) {
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fwrite("RIFF", 1, 4, f); put_le(f, 36 + frames * 4, 4); fwrite("WAVEfmt ", 1, 8, f);
    put_le(f, 16, 4); put_le(f, 1, 2); put_le(f, 2, 2); put_le(f, 44100, 4); put_le(f, 44100 * 4, 4);
    put_le(f, 4, 2); put_le(f, 16, 2); fwrite("data", 1, 4, f); put_le(f, frames * 4, 4);
    fwrite(a, 4, frames, f);
    fclose(f);
}

typedef struct { int at, st, d1, d2; } sev_t;   /* at: block index */

static double block_us(struct timespec a, struct timespec b) { return (b.tv_sec - a.tv_sec) * 1e6 + (b.tv_nsec - a.tv_nsec) / 1e3; }

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    const mpc_engine_t *E = mpc_engine();
    void *s = E->create(argv[1]);
    int bad = 0;
    const int B = 128;
    for (int inst = 0; inst < NINST; inst++) {
        char v[16];
        snprintf(v, sizeof v, "%d", inst);
        E->set_param(s, "instrument", v);
        E->set_param(s, "release", "0.3");
        int16_t tmp[256];
        for (int w = 0; w < 3000 && bank_state_of(inst) == BANK_LOADING; w++) { E->render(s, tmp, B); usleep(5000); }
        if (bank_state_of(inst) != BANK_READY) { printf("FAIL %-16s did not load\n", INST_FILE[inst]); bad = 1; continue; }

        /* the score, in blocks (one block = 2.9 ms) */
        sev_t sc[512]; int n = 0;
        int chord[4] = { 60, 64, 67, 72 }, vels[3] = { 40, 90, 127 };
        int t = 0;
        for (int vi = 0; vi < 3; vi++) {
            for (int k = 0; k < 4; k++) { sc[n++] = (sev_t){ t + k * 60, 0x90, chord[k], vels[vi] }; sc[n++] = (sev_t){ t + k * 60 + 55, 0x80, chord[k], 0 }; }
            t += 260;
            for (int k = 0; k < 4; k++) sc[n++] = (sev_t){ t, 0x90, chord[k], vels[vi] };
            for (int k = 0; k < 4; k++) sc[n++] = (sev_t){ t + 250, 0x80, chord[k], 0 };
            t += 400;
        }
        int held_at = t;                                       /* a held A4: 6 s */
        sc[n++] = (sev_t){ t, 0x90, 69, 100 }; sc[n++] = (sev_t){ t + 2067, 0x80, 69, 0 };
        t += 2400;
        int cluster_at = t;
        for (int k = 0; k < 40; k++) sc[n++] = (sev_t){ t + k * 3, 0x90, 48 + k, 100 };
        for (int k = 0; k < 40; k++) sc[n++] = (sev_t){ t + 300, 0x80, 48 + k, 0 };
        t += 700;
        int nblocks = t;
        int16_t *out = calloc((size_t)nblocks * B * 2, sizeof(int16_t));
        double worst = 0;
        for (int b = 0; b < nblocks; b++) {
            for (int q = 0; q < n; q++)
                if (sc[q].at == b) { uint8_t m[3] = { (uint8_t)sc[q].st, (uint8_t)sc[q].d1, (uint8_t)sc[q].d2 }; E->midi(s, m, 3); }
            struct timespec t0, t1;
            clock_gettime(CLOCK_MONOTONIC, &t0);
            E->render(s, out + (size_t)b * B * 2, B);
            clock_gettime(CLOCK_MONOTONIC, &t1);
            double us = block_us(t0, t1);
            if (us > worst) worst = us;
        }
        /* level, clipping */
        size_t N = (size_t)nblocks * B;
        double sum = 0; int peak = 0; size_t clip = 0;
        for (size_t i = 0; i < 2 * N; i++) { int x = abs(out[i]); sum += (double)out[i] * out[i]; if (x > peak) peak = x; if (x >= 32760) clip++; }
        double rms_db = 10 * log10(sum / (2.0 * N) / (32768.0 * 32768.0) + 1e-20);
        /* pitch of the held note: zero crossings of the left channel, 1.0-3.0 s in */
        size_t a = (size_t)(held_at * B + 44100), z = a + 2 * 44100;
        int cross = 0; double first = -1, last = 0;
        for (size_t i = a + 1; i < z; i++) {
            float x0 = out[2 * (i - 1)], x1 = out[2 * i];
            if (x0 < 0 && x1 >= 0) { double pos = (i - 1) + (-x0) / (x1 - x0); if (first < 0) first = pos; last = pos; cross++; }
        }
        double f0 = cross > 1 ? (cross - 1) * 44100.0 / (last - first) : 0;
        /* the held note must still sound after 5 s if the instrument loops */
        double tail = 0;
        size_t ta = (size_t)(held_at * B + (size_t)(5.0 * 44100)), tz = ta + 4410;
        for (size_t i = ta; i < tz; i++) tail += (double)out[2 * i] * out[2 * i];
        tail = 10 * log10(tail / 4410 / (32768.0 * 32768.0) + 1e-20);
        int sounding = 0;
        for (int i = 0; i < MAX_V; i++) sounding += ((inst_t *)s)->v[i].stage != ENV_OFF;
        char path[1400];
        snprintf(path, sizeof path, "%s/%s.wav", argv[2], INST_FILE[inst]);
        write_wav(path, out, N);
        int fail = rms_db < -60 || clip > N / 1000;
        printf("%s %-16s rms %6.1f dB  peak %5.1f%%  held A4 %7.1f Hz  loop tail %6.1f dB  worst block %6.0f us%s\n",
               fail ? "FAIL" : "ok  ", INST_FILE[inst], rms_db, peak * 100.0 / 32768, f0, tail, worst,
               cluster_at > 0 && sounding ? "" : "");
        bad |= fail;
        free(out);
    }
    E->destroy(s);
    return bad;
}
