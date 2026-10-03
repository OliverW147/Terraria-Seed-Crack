/* solve.cu -- CUDA Terraria seed solver.
 *
 * Two-stage search over the full 2^31 seed space:
 *   Stage 1 (cheap, ~40 RNG draws): screenshot observables -- tree style,
 *           forest background, moon phase, dungeon side. Any of these may be
 *           omitted (-1 = unknown) and the filter simply skips it.
 *   Stage 2 (expensive, ~26k RNG draws): the surface height profile clicked
 *           off the screenshot, matched as relative deltas so the absolute Y
 *           never matters, slid across an X window so the position need not
 *           be known either.
 *
 * Stops early once "maxHits" candidates are found (config key, default 100).
 * Set it to 0 to sweep the whole space no matter how many hits turn up.
 *
 * Build: nvcc -O3 -arch=sm_89 -o solve.exe solve.cu
 * Usage: solve.exe <json-config-path>
 * Emits progress lines "P <fraction>" and results "S <seed>" on stdout.
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cuda_runtime.h>
#include <ctime>

#include "tgen_cu.cuh"

#define MAX_PROFILE 256
/* Capacity of the exact-hit list. This is deliberately small: a run that finds
 * a handful of seeds wants them all, and a run that finds two million does not
 * want them at all -- past this point seeds are still COUNTED, and the ranked
 * leaderboard (dTop) keeps the best TOP_N regardless, so nothing useful is
 * lost by refusing to store the rest.
 * The early stop is a separate runtime value (config "maxHits"). */
#define MAX_HITS 1000
#define TPB 64
/* How many near-miss candidates to keep when a scan finds no exact match (or
 * too many), so a failed run still says how close it got. */
#define TOP_N 10

__constant__ int   cWant[MAX_PROFILE];     /* relative heights */
__constant__ int   cCol[MAX_PROFILE];      /* column offset of each sample */
__constant__ int   cSpan;                  /* cCol[last] + 1: window width */
__constant__ int   cTol, cMaxMiss;
__constant__ int   cMinRun;   /* accept on a clean run this long */
__constant__ int   cProfLen;
__constant__ int   cWinLo, cWinHi;         /* X search window */
__constant__ int   cTree[4];               /* forest tree styles seen */
__constant__ int   cTreeCount;             /* how many of cTree are set */
__constant__ int   cBG[8];                 /* forest backgrounds seen, any zone */
__constant__ int   cBGCount;               /* how many of cBG are set */
/* Optional left-to-right zone pins, -1 where the zone is unconstrained. These
 * are a strictly stronger form of the cTree/cBG "appears somewhere" tests. */
__constant__ int   cTreeOrder[4];
__constant__ int   cBGOrder[4];
__constant__ int   cHasTreeOrder, cHasBGOrder;
/* Observed ore tiers: 0 base, 1 alternate, -1 not seen. Four coin flips, so
 * naming all four is a 16x cut, more than any single other stage-1 clue. */
__constant__ int   cOre[4];
__constant__ int   cMoon, cSide, cOcean, cEvil;
/* Biome backdrops are each a SINGLE world-wide roll (unlike the 4-zone forest
 * backgrounds), so they identify the seed regardless of where the player is
 * standing when they see them. */
__constant__ int   cCorrupt, cJungle, cSnow, cHallow, cCrimson, cDesert,
                   cMushroom, cUnderworld;
__constant__ int   cZones;
/* Early-stop threshold. 0 means sweep the whole space however many hits turn
 * up; otherwise the kernel stops contributing once this many are found. */
__constant__ int   cMaxHits;

/* Leaderboard of the best-scoring seeds seen so far, kept alongside the exact
 * hit list. A run that ends with no hits still reports these, which tells the
 * difference between "your clues are slightly off" and "wrong world entirely".
 * Packed as one 64-bit key so a single atomicMin maintains the worst slot:
 * lower is better, and the seed rides in the low bits to break ties. */
__device__ unsigned long long dTop[TOP_N];
__constant__ WorldOpt cOpt;

/* WorldGen.IsBackgroundConsideredTheSame, verbatim: 3/31, 5/51 and 7/71/72/73
 * are each one visual backdrop. Every other style stands alone. */
__device__ __forceinline__ int bg_class(int b)
{
    if (b == 31) return 3;
    if (b == 51) return 5;
    if (b >= 71 && b <= 73) return 7;
    return b;
}

__device__ __forceinline__ bool bg_same(int a, int b)
{
    return bg_class(a) == bg_class(b);
}

/* Stage 1: does this seed's pre-terrain state match what was seen on screen?
 * Tree style and background are matched against ANY zone, because the player
 * generally does not know which of the 2-4 zones the screenshot came from. */
__device__ static bool stage1(const PreGen* g)
{
    /* Cheapest possible reject, so it goes first. */
    if (cOre[0] >= 0 && g->oreCopper != cOre[0]) return false;
    if (cOre[1] >= 0 && g->oreIron   != cOre[1]) return false;
    if (cOre[2] >= 0 && g->oreSilver != cOre[2]) return false;
    if (cOre[3] >= 0 && g->oreGold   != cOre[3]) return false;
    if (cMoon >= 0 && g->moon != cMoon) return false;
    if (cSide >= 0 && g->dungeonSide != cSide) return false;
    /* Ocean backdrop is a single world-wide roll, so unlike the forest
     * background it does not depend on which zone the player is standing in. */
    if (cOcean >= 0 && g->oceanBG != cOcean) return false;
    /* crimsonLeft is a POSITION flag (which half holds the evil biome), not
     * the Corruption/Crimson choice, which is a menu option and carries no
     * seed information. */
    if (cEvil >= 0 && g->crimsonLeft != cEvil) return false;
    if (cCorrupt    >= 0 && g->corruptBG    != cCorrupt)    return false;
    if (cJungle     >= 0 && g->jungleBG     != cJungle)     return false;
    if (cSnow       >= 0 && g->snowBG       != cSnow)       return false;
    if (cHallow     >= 0 && g->hallowBG     != cHallow)     return false;
    if (cCrimson    >= 0 && g->crimsonBG    != cCrimson)    return false;
    if (cDesert     >= 0 && g->desertBG     != cDesert)     return false;
    if (cMushroom   >= 0 && g->mushroomBG   != cMushroom)   return false;
    if (cUnderworld >= 0 && g->underworldBG != cUnderworld) return false;

    /* Tree style is matched against any zone; a forest background pick must
     * also occur in some zone. Several backgrounds can be named at once (a
     * large world shows four), and EVERY named one has to appear somewhere,
     * which is far stronger than naming just one.
     * Styles within a group are indistinguishable to the player, and the
     * generator itself treats them as one: WorldGen.IsBackgroundConsideredTheSame
     * defines the groups {3,31}, {5,51} and {7,71,72,73}, and rerolls a zone
     * whose new style is "the same" as the old one. Matching has to use the
     * same classes. Note 4 and 5 are NOT equivalent: they are separate
     * backdrops with different mountain art. */
    /* Tree styles are per-zone too (Main.treeStyle[0..3], split at
     * Main.treeX[]), and RandomizeTreeStyle rerolls until every zone differs,
     * so a player walking the world sees several. As with the backgrounds,
     * EVERY style named has to turn up in some zone, which is a far stronger
     * constraint than naming one. */
    for (int t = 0; t < cTreeCount; t++) {
        int want = cTree[t];
        if (want < 0) continue;
        bool any = false;
        for (int z = 0; z < cZones; z++)
            if (g->treeStyle[z] == want) { any = true; break; }
        if (!any) return false;
    }
    for (int b = 0; b < cBGCount; b++) {
        int want = cBG[b];
        if (want < 0) continue;
        bool any = false;
        for (int z = 0; z < cZones; z++) {
            if (bg_same(want, g->bg[z])) { any = true; break; }
        }
        if (!any) return false;
    }
    /* Ordered pins. A zone index past this world's zone count cannot be
     * checked (a 4-zone pin means nothing on a small 2-zone world), so it is
     * skipped rather than failed: with size "try all 3" the same config is
     * run against every size. */
    if (cHasTreeOrder) {
        for (int z = 0; z < cZones && z < 4; z++) {
            if (cTreeOrder[z] < 0) continue;
            if (g->treeStyle[z] != cTreeOrder[z]) return false;
        }
    }
    if (cHasBGOrder) {
        for (int z = 0; z < cZones && z < 4; z++) {
            if (cBGOrder[z] < 0) continue;
            if (!bg_same(cBGOrder[z], g->bg[z])) return false;
        }
    }
    return true;
}

__global__ __launch_bounds__(TPB) void solveKernel(
    long long base, long long count, int* out, int* outCount)
{
    __shared__ int S[56 * TPB];
    int t = threadIdx.x;
    long long idx = blockIdx.x * (long long)blockDim.x + t;
    if (idx >= count) return;
    if (cMaxHits > 0 && *outCount >= cMaxHits) return;   /* early stop */

    int seed = (int)(base + idx);

    UR r;
    ur_seed(&r, seed);
    PreGen g;
    pregen(&r, &cOpt, &g);
    if (!stage1(&g)) return;

    /* Stage 2: replay the terrain walk. WorldGenerator.RunPass reseeds
     * Main.rand from the world seed before EVERY pass, so TerrainPass starts
     * from a fresh generator rather than continuing the pregen stream. */
    ur_seed(&r, seed);
    const int MXW = cOpt.maxTilesX, MYW = cOpt.maxTilesY;
    const int pad = 5;
    int featureType = FT_Plateau;
    int num2 = 0;
    double num3 = (double)MYW * 0.3;
    num3 *= (double)ur_next2(&r, 90, 110) * 0.005;
    double num4 = num3 + (double)MYW * 0.2;
    num4 *= (double)ur_next2(&r, 90, 110) * 0.01;
    if (cOpt.remix) {
        num4 = (double)MYW * 0.5;
        if (MXW > 2500) num4 = (double)MYW * 0.6;
        num4 *= (double)ur_next2(&r, 95, 106) * 0.01;
    }
    double num9 = (double)MYW * 0.23;
    num2 = g.leftBeachEnd + pad;

    int prev = 0;
    int hist[MAX_PROFILE];
    int hp = 0, filled = 0;
    bool alreadyHit = false;
    /* Best window this seed managed, for the near-miss leaderboard. */
    int bestMiss = 0x7fffffff, bestSum = 0x7fffffff, bestRunSeed = 0;
    for (int i = 0; i < MXW; i++) {
        if (num2 <= 0) {
            featureType = ur_next2(&r, 0, 5);
            num2 = ur_next2(&r, 5, 40);
            if (featureType == FT_Plateau) num2 *= (int)((double)ur_next2(&r, 5, 30) * 0.2);
        }
        num2--;
        if ((double)i > (double)MXW * 0.45 && (double)i < (double)MXW * 0.55 &&
            (featureType == FT_Mountain || featureType == FT_Valley))
            featureType = ur_next2(&r, 0, 3);
        if ((double)i > (double)MXW * 0.48 && (double)i < (double)MXW * 0.52)
            featureType = FT_Plateau;
        num3 += gen_surface_offset(&r, featureType, &cOpt);
        double n10 = 0.17, n11 = 0.26;
        if (cOpt.drunk) { n10 = 0.15; n11 = 0.28; }
        if (world_size(MXW) == 0) n10 += 0.02;
        if (i < g.leftBeachEnd + pad || i > g.rightBeachStart - pad) {
            double lo = (double)MYW * n10;
            if (num3 < lo) num3 = lo;
            if (num3 > num9) num3 = num9;
        } else if (num3 < (double)MYW * n10) { num3 = (double)MYW * n10; num2 = 0; }
        else if (num3 > (double)MYW * n11) { num3 = (double)MYW * n11; num2 = 0; }
        while (ur_next2(&r, 0, 3) == 0) { num4 += (double)ur_next2(&r, -2, 3); }
        if (cOpt.remix) {
            if (MXW > 2500) { if (num4 > (double)MYW * 0.7) num4 -= 1.0; }
            else { if (num4 > (double)MYW * 0.6) num4 -= 1.0; }
        } else {
            if (num4 < num3 + (double)MYW * 0.06) num4 += 1.0;
            if (num4 > num3 + (double)MYW * 0.35) num4 -= 1.0;
        }

        /* Keep a rolling window of recent heights so the profile can be
         * matched with per-point tolerance. Later worldgen passes (grass,
         * biomes, caves) shift the surface by a tile or two, so requiring
         * exact deltas rejects the true seed; validated against two real
         * world files, +-1 tile with a few allowed misses separates the true
         * seed from wrong ones cleanly. */
        int h = (int)num3;
        hist[hp] = h;
        hp = (hp + 1) % cSpan;
        filled = filled < cSpan ? filled + 1 : cSpan;
        /* i is the LAST column of the rolling window, so the window START is
         * i - cProfLen + 1; compare that against the search range. */
        /* The window spans cSpan columns but only cProfLen of them were
         * sampled; cCol[k] says which column each sample came from, so gaps
         * (cave-cut or built-over ground the player skipped) are supported. */
        int wstart = i - cSpan + 1;
        if (wstart >= cWinLo && wstart <= cWinHi && filled >= cSpan) {
            int base0 = hist[hp];
            int miss = 0;
            /* Score every point rather than breaking out at the miss limit:
             * the leaderboard needs a full error profile even for seeds that
             * fail, and the branch cost is dwarfed by the terrain walk. */
            int sumAbs = 0;
            /* Longest contiguous stretch whose offset from the profile stays
             * constant to within the tolerance. Later passes drop features
             * this model does not reproduce (MountainCaves alone raises a
             * ~70 column, ~35 tile mound, and its position cannot be derived
             * without the tile grid), so a correct seed can disagree wildly
             * over part of the window while matching exactly either side of
             * it. Whole-window scoring rejects such a seed; the longest clean
             * run still identifies it. */
            int bestRun = 0, run = 0, runOff = 0;
            for (int k = 0; k < cProfLen; k++) {
                int v = hist[(hp + cCol[k]) % cSpan] - base0;
                int d = v - cWant[k];
                int ad = d < 0 ? -d : d;
                if (ad > cTol) miss++;
                sumAbs += ad;
                if (run > 0 && (d - runOff <= cTol) && (runOff - d <= cTol)) {
                    run++;
                } else {
                    run = 1; runOff = d;
                }
                if (run > bestRun) bestRun = run;
            }
            /* Accept on either criterion: a clean whole-window match, or a
             * long enough unbroken run. cMinRun of 0 disables the run test. */
            bool ok = (miss <= cMaxMiss)
                   || (cMinRun > 0 && bestRun >= cMinRun);
            /* A seed can match at several offsets inside the window; report it
             * once so the candidate list is not flooded by one seed. */
            if (ok && !alreadyHit) {
                alreadyHit = true;
                int p = atomicAdd(outCount, 1);
                /* Past capacity the seed still counts, it just is not stored. */
                if (p < MAX_HITS) out[p] = seed;
            }
            /* Rank by longest clean run first, then misses, then total error:
             * a seed matching 57 columns exactly and disagreeing elsewhere is
             * a better lead than one that is mediocre everywhere. */
            if (bestRun > bestRunSeed
                || (bestRun == bestRunSeed
                    && (miss < bestMiss
                        || (miss == bestMiss && sumAbs < bestSum)))) {
                bestRunSeed = bestRun; bestMiss = miss; bestSum = sumAbs;
            }
        }
        prev = h;
    }

    /* Offer this seed to the leaderboard. The key packs quality into the high
     * bits so plain integer comparison orders it: misses dominate, then total
     * error, then the seed itself to keep the result deterministic regardless
     * of which thread got there first. */
    if (bestMiss != 0x7fffffff) {
        /* Longest run dominates the key, stored inverted so that smaller is
         * still better and one atomicMin keeps the board ordered. */
        int runRank = cProfLen - bestRunSeed;
        if (runRank < 0) runRank = 0;
        unsigned long long key =
              ((unsigned long long)(runRank  & 0xFFFF) << 48)
            | ((unsigned long long)(bestSum  & 0xFFFF) << 32)
            |  (unsigned long long)(unsigned int)seed;
        /* Insert with atomicMin down the slots: whatever this slot evicts is
         * carried to the next one, so the array ends up holding the TOP_N
         * smallest keys in sorted order. atomicMin returns the previous value,
         * which makes each step both a compare and a swap with no lock. Only
         * stage-1 survivors get here, so contention is negligible. */
        for (int i = 0; i < TOP_N && key != ~0ULL; i++) {
            unsigned long long prev_v = atomicMin(&dTop[i], key);
            if (prev_v <= key) continue;   /* slot already better; carry on */
            key = prev_v;                  /* evicted entry moves down */
        }
    }
}

/* ---------- tiny JSON reader (flat objects/arrays of ints only) ---------- */
static const char* find_key(const char* js, const char* key)
{
    static char pat[64];
    snprintf(pat, sizeof pat, "\"%s\"", key);
    const char* p = strstr(js, pat);
    if (!p) return NULL;
    p = strchr(p, ':');
    return p ? p + 1 : NULL;
}
static int json_int(const char* js, const char* key, int dflt)
{
    const char* p = find_key(js, key);
    if (!p) return dflt;
    while (*p == ' ') p++;
    if (!strncmp(p, "null", 4) || !strncmp(p, "true", 4)) return dflt;
    return atoi(p);
}
static int json_array(const char* js, const char* key, int* out, int maxN)
{
    const char* p = find_key(js, key);
    if (!p) return 0;
    p = strchr(p, '[');
    if (!p) return 0;
    p++;
    int n = 0;
    while (*p && *p != ']' && n < maxN) {
        while (*p == ' ' || *p == ',') p++;
        if (*p == ']') break;
        out[n++] = atoi(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
    return n;
}

int main(int argc, char** argv)
{
    if (argc < 2) { fprintf(stderr, "usage: solve <config.json>\n"); return 1; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    static char js[1 << 16];
    size_t n = fread(js, 1, sizeof(js) - 1, f);
    js[n] = 0;
    fclose(f);

    WorldOpt opt;
    memset(&opt, 0, sizeof opt);
    opt.maxTilesX   = json_int(js, "maxTilesX", 8400);
    opt.maxTilesY   = json_int(js, "maxTilesY", 2400);
    opt.getGood     = json_int(js, "ftw", 0);
    opt.drunk       = json_int(js, "drunk", 0);
    opt.remix       = json_int(js, "remix", 0);
    opt.anniversary = json_int(js, "anniversary", 0);
    opt.dontStarve  = json_int(js, "dontStarve", 0);

    int heights[MAX_PROFILE];
    int hn = json_array(js, "heights", heights, MAX_PROFILE);
    if (hn < 2) { fprintf(stderr, "need at least 2 height samples\n"); return 1; }

    /* Relative to the first sample, so absolute Y never matters. */
    int want[MAX_PROFILE];
    for (int i = 0; i < hn; i++) want[i] = heights[i] - heights[0];
    int cols[MAX_PROFILE];
    int cn = json_array(js, "columns", cols, MAX_PROFILE);
    if (cn != hn) {                 /* no explicit columns: assume contiguous */
        for (int i = 0; i < hn; i++) cols[i] = i;
    }
    int span = cols[hn - 1] + 1;
    if (span > MAX_PROFILE) {
        fprintf(stderr, "column span %d exceeds MAX_PROFILE\n", span);
        return 1;
    }
    int tol     = json_int(js, "tol", 1);
    /* Later worldgen passes (grass, biomes, small caves) shift individual
     * columns by a tile or two, so a real profile taken from a live world
     * carries a handful of legitimate offsets. Validated against two real
     * world files: ~1 miss per 12 columns keeps the true seed while staying
     * selective. */
/* Later worldgen passes (surface caves, small holes, biome edges) nudge
     * individual columns by one tile after TerrainPass writes them, so a
     * profile read from a finished world carries scattered +-1 perturbations.
     * Measured on real world files: 134 of 145 mismatch runs are a single
     * column. The true seed still scores 49/53 while the best wrong offset
     * scores 28, so allowing ~20% of points to miss sits comfortably inside
     * that gap. */
    int maxMiss = json_int(js, "maxMiss", hn / 12 + 1);

    int winLo = json_int(js, "winLo", 0);
    int winHi = json_int(js, "winHi", opt.maxTilesX);
    if (winLo < 0) winLo = 0;
    if (winHi > opt.maxTilesX) winHi = opt.maxTilesX;

    int tree[4] = { -1, -1, -1, -1 };
    int treeCount = json_array(js, "treeStyles", tree, 4);
    if (treeCount == 0) {
        /* Older configs carry a single "treeStyle" instead. */
        int one = json_int(js, "treeStyle", -1);
        if (one >= 0) { tree[0] = one; treeCount = 1; }
    }
    for (int i = treeCount; i < 4; i++) tree[i] = -1;
    int bg[8];
    int bgCount = json_array(js, "bgStyles", bg, 8);
    if (bgCount == 0) {
        int one = json_int(js, "bgStyle", -1);
        if (one >= 0) { bg[0] = one; bgCount = 1; }
    }
    for (int i = bgCount; i < 8; i++) bg[i] = -1;

    int treeOrder[4] = { -1, -1, -1, -1 };
    int bgOrder[4]   = { -1, -1, -1, -1 };
    json_array(js, "treeOrder", treeOrder, 4);
    json_array(js, "bgOrder",   bgOrder,   4);
    int ore[4] = { -1, -1, -1, -1 };
    json_array(js, "ores", ore, 4);

    int hasTreeOrder = 0, hasBGOrder = 0;
    for (int i = 0; i < 4; i++) {
        if (treeOrder[i] >= 0) hasTreeOrder = 1;
        if (bgOrder[i]   >= 0) hasBGOrder   = 1;
    }
    int moon  = json_int(js, "moon", -1);
    int side  = json_int(js, "dungeonSide", -1);
    int ocean = json_int(js, "oceanBG", -1);
    int evil  = json_int(js, "evilLeft", -1);
    int corrupt    = json_int(js, "corruptBG", -1);
    int jungle     = json_int(js, "jungleBG", -1);
    int snow       = json_int(js, "snowBG", -1);
    int hallow     = json_int(js, "hallowBG", -1);
    int crimson    = json_int(js, "crimsonBG", -1);
    int desert     = json_int(js, "desertBG", -1);
    int mushroom   = json_int(js, "mushroomBG", -1);
    int underworld = json_int(js, "underworldBG", -1);
    int zones = zone_count(opt.maxTilesX);

    cudaMemcpyToSymbol(cWant, want, sizeof(int) * hn);
    cudaMemcpyToSymbol(cCol, cols, sizeof(int) * hn);
    cudaMemcpyToSymbol(cSpan, &span, sizeof(int));
    /* The run test is a fallback for windows spoiled by a surface feature this
     * model does not reproduce (MountainCaves raises a ~70 column mound whose
     * position needs the tile grid to derive). It only earns its keep on long
     * profiles: on a short one almost any seed can produce a run covering half
     * the window by chance, which floods the candidate list. Require both a
     * long profile and an absolutely long run, and let the caller override. */
    int minRun = json_int(js, "minRun", hn >= 80 ? (hn * 2) / 5 : 0);
    cudaMemcpyToSymbol(cMinRun, &minRun, sizeof(int));
    cudaMemcpyToSymbol(cTol, &tol, sizeof(int));
    cudaMemcpyToSymbol(cMaxMiss, &maxMiss, sizeof(int));
    cudaMemcpyToSymbol(cProfLen, &hn, sizeof(int));
    cudaMemcpyToSymbol(cWinLo, &winLo, sizeof(int));
    cudaMemcpyToSymbol(cWinHi, &winHi, sizeof(int));
    cudaMemcpyToSymbol(cTree, tree, sizeof(tree));
    cudaMemcpyToSymbol(cTreeCount, &treeCount, sizeof(int));
    cudaMemcpyToSymbol(cBG, bg, sizeof(bg));
    cudaMemcpyToSymbol(cBGCount, &bgCount, sizeof(int));
    cudaMemcpyToSymbol(cTreeOrder, treeOrder, sizeof(treeOrder));
    cudaMemcpyToSymbol(cBGOrder, bgOrder, sizeof(bgOrder));
    cudaMemcpyToSymbol(cHasTreeOrder, &hasTreeOrder, sizeof(int));
    cudaMemcpyToSymbol(cHasBGOrder, &hasBGOrder, sizeof(int));
    cudaMemcpyToSymbol(cOre, ore, sizeof(ore));
    cudaMemcpyToSymbol(cMoon, &moon, sizeof(int));
    cudaMemcpyToSymbol(cSide, &side, sizeof(int));
    cudaMemcpyToSymbol(cOcean, &ocean, sizeof(int));
    cudaMemcpyToSymbol(cEvil, &evil, sizeof(int));
    cudaMemcpyToSymbol(cCorrupt, &corrupt, sizeof(int));
    cudaMemcpyToSymbol(cJungle, &jungle, sizeof(int));
    cudaMemcpyToSymbol(cSnow, &snow, sizeof(int));
    cudaMemcpyToSymbol(cHallow, &hallow, sizeof(int));
    cudaMemcpyToSymbol(cCrimson, &crimson, sizeof(int));
    cudaMemcpyToSymbol(cDesert, &desert, sizeof(int));
    cudaMemcpyToSymbol(cMushroom, &mushroom, sizeof(int));
    cudaMemcpyToSymbol(cUnderworld, &underworld, sizeof(int));
    cudaMemcpyToSymbol(cZones, &zones, sizeof(int));
    cudaMemcpyToSymbol(cOpt, &opt, sizeof(WorldOpt));

    /* 0 disables the early stop entirely. Not clamped to MAX_HITS: the stop
     * threshold and the storage capacity are independent, so a full sweep can
     * count millions of hits while storing only the first MAX_HITS. */
    int maxHits = json_int(js, "maxHits", 100);
    if (maxHits < 0) maxHits = 0;
    cudaMemcpyToSymbol(cMaxHits, &maxHits, sizeof(int));

    int *dOut, *dCount;
    cudaMalloc(&dOut, MAX_HITS * sizeof(int));
    cudaMalloc(&dCount, sizeof(int));
    /* All-ones is the "empty" key: worse than any real score, so the first
     * TOP_N candidates fill the board without a special case. */
    {
        unsigned long long emptyTop[TOP_N];
        for (int i = 0; i < TOP_N; i++) emptyTop[i] = ~0ULL;
        cudaMemcpyToSymbol(dTop, emptyTop, sizeof emptyTop);
    }
    cudaMemset(dCount, 0, sizeof(int));

    /* Optional seed range, for verifying a known seed without sweeping the
     * whole space (and hitting the early-stop cap first). */
    long long seedFrom = (long long)json_int(js, "seedFrom", 0);
    long long seedTo   = (long long)json_int(js, "seedTo", 0);
    if (seedTo <= 0) seedTo = 2147483648LL;
    long long total = seedTo;
    long long startSeed = (long long)json_int(js, "startSeed", 0);
    long long chunk = 8LL << 20;
    int hostCount = 0;
    /* Progress is reported on a wall-clock cadence rather than per chunk, so
     * the rate and ETA stay meaningful regardless of how fast chunks run. */
    clock_t t_start = clock();
    double last_report = 0.0;
    int reported = 0;               /* seeds already printed */
    int hits[MAX_HITS];

    printf("R %d %d ftw=%d prof=%d win=%d..%d\n",
           opt.maxTilesX, opt.maxTilesY, opt.getGood, hn, winLo, winHi);
    fflush(stdout);

    for (long long base = seedFrom; base < total; base += chunk) {
        long long c = (base + chunk > total) ? (total - base) : chunk;
        solveKernel<<<(int)((c + TPB - 1) / TPB), TPB>>>(base, c, dOut, dCount);
        cudaDeviceSynchronize();
        cudaError_t err = cudaGetLastError();
        if (err != cudaSuccess) {
            fprintf(stderr, "CUDA error: %s\n", cudaGetErrorString(err));
            return 2;
        }
        cudaMemcpy(&hostCount, dCount, sizeof(int), cudaMemcpyDeviceToHost);
        /* Stream newly found seeds as soon as the chunk that found them ends,
         * so a long run is not silent until it finishes. */
        if (hostCount > reported) {
            int have = hostCount < MAX_HITS ? hostCount : MAX_HITS;
            /* Copy only the new tail: with the buffer this large, copying the
             * whole thing every chunk would dominate the run. */
            if (have > reported) {
                cudaMemcpy(hits + reported, dOut + reported,
                           (have - reported) * sizeof(int),
                           cudaMemcpyDeviceToHost);
            }
            for (int i = reported; i < have; i++) printf("S %d\n", hits[i]);
            reported = have;
            fflush(stdout);
        }
        double done = (double)(base + c - seedFrom);
        double elapsed = (double)(clock() - t_start) / CLOCKS_PER_SEC;
        bool finished = (base + c >= total);
        if (elapsed - last_report >= 1.0 || finished
            || (maxHits > 0 && hostCount >= maxHits)) {
            last_report = elapsed;
            double rate = (elapsed > 0.0) ? done / elapsed : 0.0;
            double eta = (rate > 0.0) ? ((double)(total - seedFrom) - done) / rate : 0.0;
            printf("P %.4f %d %.0f %.0f %.0f\n",
                   done / (double)total, hostCount, rate, elapsed, eta);
            fflush(stdout);
        }
        fflush(stdout);
        if (maxHits > 0 && hostCount >= maxHits) {
            printf("! stopping early: %d candidates\n", hostCount);
            break;
        }
    }

    int show = hostCount < MAX_HITS ? hostCount : MAX_HITS;
    if (show > reported) {
        cudaMemcpy(hits + reported, dOut + reported,
                   (show - reported) * sizeof(int), cudaMemcpyDeviceToHost);
    }
    for (int i = reported; i < show; i++) printf("S %d\n", hits[i]);
    if (hostCount > MAX_HITS) {
        printf("! %d candidates found; listing the first %d, "
               "best-ranked shown below\n", hostCount, MAX_HITS);
    }

    /* Near-miss report. A run that found nothing, or that found so many that
     * the clues clearly did not narrow anything down, still has something
     * useful to say: how close the best seeds came, broken down by how far
     * off each sampled column was. A best seed sitting at +-1 everywhere
     * usually means the clues are right and the tolerance is a touch tight;
     * errors of +-3 and up mean the profile does not belong to this scan. */
    {
        unsigned long long top[TOP_N];
        cudaMemcpyFromSymbol(top, dTop, sizeof top);
        int shown = 0;
        for (int i = 0; i < TOP_N; i++) if (top[i] != ~0ULL) shown++;
        if (shown > 0) {
            printf("B %d\n", shown);
            double* hh = (double*)malloc(sizeof(double) * opt.maxTilesX);
            for (int i = 0; i < shown; i++) {
                int sd = (int)(unsigned int)(top[i] & 0xFFFFFFFFULL);
                int runRank = (int)((top[i] >> 48) & 0xFFFF);
                int bestRun = hn - runRank;
                UR r; ur_seed(&r, sd);
                PreGen g; pregen(&r, &opt, &g);
                terrain_pass_seeded(sd, &opt, &g, hh);
                /* Re-score on the host to recover the error histogram: try
                 * every offset in the window and keep the best one. */
                int bd[5] = {0, 0, 0, 0, 0};   /* counts at |err| 0,1,2,3,4+ */
                int bestScore = 0x7fffffff, bestAt = -1;
                for (int w = winLo; w <= winHi; w++) {
                    if (w < 0 || w + span > opt.maxTilesX) continue;
                    int d0[5] = {0, 0, 0, 0, 0};
                    int base0 = (int)hh[w];
                    int sc = 0;
                    for (int k = 0; k < hn; k++) {
                        int v = (int)hh[w + cols[k]] - base0;
                        int e = v - want[k];
                        if (e < 0) e = -e;
                        d0[e < 4 ? e : 4]++;
                        sc += e;
                    }
                    if (sc < bestScore) {
                        bestScore = sc; bestAt = w;
                        for (int k = 0; k < 5; k++) bd[k] = d0[k];
                    }
                }
                printf("C %d %d %d %d %d %d %d %d\n", sd, bestRun, bestAt,
                       bd[0], bd[1], bd[2], bd[3], bd[4]);
            }
            free(hh);
            fflush(stdout);
        }
    }
    /* D goes last so a reader that treats it as the end-of-run marker has
     * already seen the near-miss block. */
    printf("D %d\n", hostCount);
    return 0;
}
