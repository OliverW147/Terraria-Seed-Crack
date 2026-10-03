"""Background layer tables, transcribed from WorldGen.setBG and SetForestBGSet.

Every table here is a literal transcription of the decompiled switch statements
(dec/WorldGen.cs, dec/Main.cs). A -1 slot means the game leaves that parallax
layer empty, which is meaningful: two styles can share art in one slot and be
told apart only by whether another slot is filled.

Terraria draws a surface backdrop as three parallax layers, slot 0 first
(farthest, highest on screen, smallest scale) through slot 2 last (nearest,
lowest, largest). From DrawSurfaceBG_<Biome>:

    slot 0: bgScale 1.25  bgTopY ~ +1500
    slot 1: bgScale 1.31  bgTopY ~ +1750
    slot 2: bgScale 1.34  bgTopY ~ +2000

The ~250px stagger between slots is what makes several rows of trees visible at
once. Compositing the layers bottom-aligned collapses them onto one horizon and
throws that away, which is why previews built that way showed a single row.
"""

# WorldGen.SetForestBGSet: style -> (mountainSet[2], treeSet[3]).
# mountainSet defaults to [7, 8] and each case overrides only what it changes.
FOREST_BG = {
    1:  ([7, 8],     [50, 51, 52]),
    2:  ([7, 8],     [53, 54, 55]),
    3:  ([7, 90],    [91, -1, 92]),
    31: ([7, 90],    [91, -1, 11]),
    4:  ([93, 94],   [-1, -1, -1]),
    5:  ([93, 94],   [-1, -1, 55]),
    51: ([93, 94],   [-1, -1, 11]),
    6:  ([171, 172], [173, -1, -1]),
    7:  ([176, 177], [178, -1, -1]),
    71: ([176, 177], [178, -1, 11]),
    72: ([176, 177], [178, -1, 52]),
    73: ([176, 177], [178, -1, 55]),
    8:  ([179, 180], [184, -1, -1]),
    9:  ([277, 278], [279, -1, -1]),
    10: ([280, 281], [282, -1, -1]),
    11: ([7, 331],   [330, 329, 328]),
    12: ([7, 336],   [335, 334, 333]),
    13: ([7, -1],    [343, 342, 341]),
    0:  ([7, 8],     [9, 10, 11]),
}

# WorldGen.setBG case 3. Snow is the only biome with a separate mountain array;
# snowMntBG defaults to [35, 36] and only some styles override it.
SNOW_BG = {
    0:  ([35, 36],   [37, 38, 39]),
    1:  ([35, 36],   [97, 96, 95]),
    2:  ([98, 99],   [-1, -1, -1]),
    21: ([98, 99],   [95, 96, 97]),
    22: ([98, 99],   [37, 38, 39]),
    3:  ([98, 100],  [-1, -1, -1]),
    31: ([98, 100],  [95, 96, 97]),
    32: ([98, 100],  [37, 38, 39]),
    4:  ([98, 101],  [-1, -1, -1]),
    41: ([98, 101],  [95, 96, 97]),
    42: ([98, 101],  [37, 38, 39]),
    5:  ([-1, -1],   [258, 259, 260]),
    6:  ([-1, -1],   [263, 264, 265]),
    7:  ([269, 270], [267, 266, 268]),
    8:  ([35, 36],   [299, 298, -1]),
}

# WorldGen.setBG cases 1, 2, 4, 5, 6, 8: three-slot sets with no mountain array.
BIOME_BG = {
    "corrupt": {
        0: [12, 13, 14], 1: [56, 57, 58], 2: [211, 212, 213],
        3: [225, 226, 227], 4: [240, 241, 242],
        51: [324, 323, 322], 52: [324, 226, 322]},
    "jungle": {
        0: [15, 16, 17], 1: [59, 60, 61], 2: [222, 223, 224],
        3: [237, 238, 239], 4: [284, 285, 286], 5: [271, 272, 273],
        6: [302, 301, 300]},
    "hallow": {
        0: [29, 30, 31], 1: [102, 103, 104], 2: [219, 220, 221],
        3: [243, 244, 245], 4: [-1, 261, 262], 5: [327, 326, 325]},
    "crimson": {
        0: [43, 44, 45], 1: [105, 106, 107], 2: [174, -1, 175],
        3: [214, 215, 216], 4: [-1, 229, 230], 5: [255, 256, 257],
        6: [339, 338, 337]},
    "mushroom": {
        0: [46, 47, 48], 1: [231, 232, 233], 2: [234, 235, 236],
        3: [287, 288, 289], 4: [321, 320, 319]},
    # desertBackgroundSet.Pure.Set(a, b, c) rather than Main.<biome>BG[i].
    "desert": {
        0: [21, 20, -1], 1: [108, 109, -1], 2: [207, 208, -1],
        3: [217, 218, -1], 4: [248, 249, 250],
        51: [306, 303, -1], 52: [306, 304, -1], 53: [306, 305, -1]},
}

# WorldGen.setBG case 7: a single texture, not a layer set.
OCEAN_BG = {0: 28, 1: 110, 2: 111, 3: 209, 4: 210, 5: 283, 6: 332, 7: 340}

# WorldGen.setBG case 9. These index TextureAssets.Underworld, which loads
# "Backgrounds/Underworld <n>.xnb", a different file set entirely from the
# Background_<n>.xnb every other biome uses. Reading them as Background_<n>
# yields five unrelated images, which is why these previews looked nothing like
# the underworld.
UNDERWORLD_BG = {
    0: [0, 1, 2, 3, 4],
    1: [5, 6, 7, 8, 9],
    2: [10, 11, 12, 13, 9],
}

# DrawUnderworldBackground counts DOWN from layer 4 to 0, so index 0 is drawn
# last and sits in FRONT. Parallax divisor is (index * 2 + 3).
#
# The switch in DrawUnderworldBackgroundLayer keys on the TEXTURE id, not the
# layer index. Vertical offsets, taken from that switch (the ones scaled by
# num2 use num2 = 1.3):
UNDERWORLD_OFFSETS = {
    1: 175, 2: 100, 3: 75, 4: 0, 6: -60, 7: 90, 8: 90, 9: -30,
    10: 325, 11: 130, 12: 26, 13: 26,
}

# Texture ids whose file is a 2x2 sheet of animation frames; only the
# top-left quarter is a single frame. Drawing the whole sheet is what made
# several underworld and lava layers show up as four tiled copies.
UNDERWORLD_ANIM = {1, 6, 7, 8, 13}
