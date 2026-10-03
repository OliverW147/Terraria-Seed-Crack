"""Extract tree and forest-background sprites from Terraria's XNB content files.

Writes PNG thumbnails into assets/ so the GUI can show real pictures in the
tree-style and background pickers instead of bare style numbers.

Tree styles come from Tiles_5.xnb (the tree tile sheet). Backdrop styles map to
image ids via WorldGen.setBG and SetForestBGSet; those tables live in bgsets.py,
transcribed from the decompiled switches. This module only turns them into
pictures, reproducing the parallax layering the game draws.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

import xnbread

GAME = r"D:\SteamLibrary\steamapps\common\Terraria\Content\Images"
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")

from bgsets import (FOREST_BG, SNOW_BG, BIOME_BG, OCEAN_BG, UNDERWORLD_BG,
                    UNDERWORLD_OFFSETS, UNDERWORLD_ANIM)

# Tree styles 0-5 are the forest tree variants rolled by RandomizeTreeStyle.
# Style 0 is usually rewritten to 4, but keep all six selectable.
TREE_STYLES = [0, 1, 2, 3, 4, 5]

# Vertical offset of each surface parallax slot, from the bgTopY constants in
# DrawSurfaceBG_<Biome>: slot 0 sits highest, slot 2 lowest, ~250 game px
# apart.
#
# Every layer in a set is a full-height image whose lower half is opaque
# ground, so too small a stagger lets the nearest layer bury the ones behind it
# (style 3 showed only its slot-2 trees, with both mountain layers invisible).
# Too large a one splits them into separate horizontal bands, because the game
# only ever shows about a screen of this stack at once and we are drawing the
# whole thing. These spacings put each layer's scenery just above the next
# one's horizon, which is what the screen actually shows.
MTN_DY = 150
SLOT_DY = (0, 60, 120)
SLOT_SCALE = (1.25, 1.31, 1.34)


def load_xnb(path):
    """Decode an XNB texture into a PIL image, or None if it fails."""
    try:
        raw = xnbread.decompress_xnb(path)
        _readers, fmt, w, h, pixels = xnbread.parse_texture(raw)
        if fmt != 0:
            print("  ! %s uses format %d, not Color" % (os.path.basename(path), fmt))
            return None
        return Image.frombytes("RGBA", (w, h), pixels[:w * h * 4])
    except Exception as exc:  # noqa: BLE001 - want to report and continue
        print("  ! failed %s: %s" % (os.path.basename(path), exc))
        return None


def save_thumb(img, out_path, size=(160, 120)):
    # LANCZOS, not NEAREST: these scenes shrink by about 5x, and nearest
    # sampling keeps one pixel in every 5x5 block, so features thinner than
    # that survive or vanish depending on where the sample lands. Style 7's
    # tree line is a row of 3px pines and dropped out entirely. Averaging
    # keeps them, at the cost of slightly softer rock edges.
    thumb = img.copy()
    thumb.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(thumb, ((size[0] - thumb.width) // 2,
                         (size[1] - thumb.height) // 2))
    canvas.save(out_path)


def load_bg(img_id, folder="Background"):
    """Load Background_<n>.xnb, or an underworld layer, as RGBA."""
    if img_id is None or img_id < 0:
        return None
    if folder == "Underworld":
        src = os.path.join(GAME, "Backgrounds", "Underworld %d.xnb" % img_id)
    else:
        src = os.path.join(GAME, "Background_%d.xnb" % img_id)
    if not os.path.exists(src):
        return None
    img = load_xnb(src)
    return img.convert("RGBA") if img is not None else None


def compose_surface(mtn_ids, tree_ids):
    """Stack a surface backdrop the way DrawSurfaceBG_* does.

    Mountains go down first (mnt[0] behind mnt[1]), then the three tree slots
    in index order, each pushed further down the screen. The vertical stagger
    is the whole point: without it three rows of trees collapse into one and
    styles that differ only in a far slot become indistinguishable.
    """
    # The two mountain textures are a pair drawn at one depth (Main.treeMntBGSet
    # is a single parallax layer), so they share an offset and are excluded from
    # the per-layer stagger -- pushing them apart split them into two floating
    # ranges.
    mountains = []
    for i in (mtn_ids or []):
        img = load_bg(i)
        if img is not None:
            mountains.append(img)
    layers = []
    for slot, i in enumerate(tree_ids or []):
        img = load_bg(i)
        if img is not None:
            layers.append((img, SLOT_DY[slot],
                           SLOT_SCALE[slot] / SLOT_SCALE[0]))
    if not layers and not mountains:
        return None

    scaled = []
    for img, dy, sc in layers:
        if sc != 1.0:
            img = img.resize((max(1, int(img.width * sc)),
                              max(1, int(img.height * sc))), Image.NEAREST)
        scaled.append((img, dy))

    # Flatten the mountain pair into one image, bottom-aligned, and put it at
    # the back of the stack so the stagger treats it as a single layer.
    if mountains:
        mw = min(m.width for m in mountains)
        fit = []
        for m in mountains:
            if m.width != mw:
                k = mw / float(m.width)
                m = m.resize((mw, max(1, int(m.height * k))), Image.LANCZOS)
            fit.append(m)
        mh = max(m.height for m in fit)
        mtn = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        for m in fit:
            for x in range(0, mw, m.width):
                mtn.alpha_composite(m, (x, mh - m.height))
        scaled.insert(0, (mtn, 0))

    # The game tiles each layer horizontally at that layer's own width, so a
    # preview should be about one screen wide, not as wide as the widest
    # texture. Layers within a set can differ 2x in width; sizing the canvas to
    # the widest one and pasting each layer once left the narrow layers
    # covering only half the frame, which is what made these previews look like
    # the same art repeated several times.
    cw = min(i.width for i, _ in scaled)
    # Layers in one set can differ 2x in size. Cropping a wide layer to the
    # frame keeps only its bottom-left corner, which for tall art (style 10's
    # 2048x2048 mountain, its 1561x1541 trees) is the empty ground below the
    # scene -- the previews came out as flat colour washes. Scale the whole
    # layer down to the frame width instead, so all of its art survives at the
    # same relative size as the other layers'.
    fitted = []
    for img, dy in scaled:
        if img.width != cw:
            k = cw / float(img.width)
            img = img.resize((cw, max(1, int(img.height * k))), Image.LANCZOS)
        fitted.append((img, dy))

    # Stagger AFTER the widths are normalised: the offsets are derived from
    # each layer's pixel geometry, so measuring a 2048-wide layer that is about
    # to be halved (style 13's layer 341) gives twice the offset it needs and
    # spreads the set into separate bands.
    #
    # A fixed stagger cannot work either: each layer goes fully opaque at its
    # own depth (layer 92 at 179px from its top, layer 90 at 70px), so an
    # offset that reveals one buries another. Push each layer up by just enough
    # to clear the opaque ground of the layer in front.
    fitted = _stagger_by_horizon(fitted)
    # Where the nearest layer goes solid: everything below is featureless
    # foreground. Kept to bound the crop at the end.
    near_img, near_dy = fitted[-1]
    near_ground = _horizon(near_img)
    # Layers share a horizon and overlap heavily; the nearer ones simply cover
    # the farther ones. Anchor every layer to the bottom and apply the stagger
    # upward, so the canvas stays one scene tall. Letting the stagger extend
    # the canvas instead stacked the layers into separate bands, which read as
    # the same scene repeated down the image.
    # Canvas height follows the tallest layer, so nothing is cut off -- except
    # that one layer can be several times the height of the rest (style 10's
    # mountain is 2048 against a 512 neighbour), which would leave the others
    # in a thin strip at the bottom. Cap it at twice the smallest layer; a
    # layer taller than that keeps its lower portion, which holds the horizon.
    # The stagger lifts the far layers, so the canvas has to be tall enough to
    # hold the highest one or their tops are simply clipped off.
    heights = [i.height for i, _ in fitted]
    base = max(1, min(max(heights), 2 * min(heights)))
    chh = max(base, max(i.height + dy for i, dy in fitted))
    canvas = Image.new("RGBA", (cw, chh), (0, 0, 0, 0))
    for img, dy in fitted:
        if img.height > chh:
            img = img.crop((0, img.height - chh, img.width, img.height))
        y = max(0, chh - img.height - dy)
        # A layer narrower than the frame genuinely does tile.
        for x in range(0, cw, img.width):
            canvas.alpha_composite(img, (x, y))
    # Keep a slice of foreground below the nearest ground line, not all of it.
    # These sets carry hundreds of px of flat grass under the scene, which the
    # uniform-row trim cannot touch because the grass is faintly two-tone. Left
    # in, it takes half the thumbnail and shrinks the scenery past the point
    # where thin sprites (style 7's tree line) survive the downscale.
    ground_y = chh - near_img.height - near_dy + near_ground
    ground_y = max(0, min(chh, ground_y))
    floor = min(chh, ground_y + int(0.25 * ground_y) + 32)
    if floor > 8:
        canvas = canvas.crop((0, 0, cw, floor))
    return _trim_flat_base(canvas)


def _horizon(img):
    """First row where the layer is opaque all the way across.

    Below this the layer is solid ground that hides anything behind it; above
    it is sky and scenery. This is the line the stagger has to clear.
    """
    a = img.split()[3]
    px = a.load()
    w, h = img.size
    step = max(1, w // 64)
    for y in range(h):
        if all(px[x, y] > 200 for x in range(0, w, step)):
            return y
    return h


def _stagger_by_horizon(scaled, gap=0.16, min_gap=40):
    """Offset each layer so the one behind it stays visible.

    Layers arrive far-to-near. Walking from the front backwards, each layer is
    lifted until its own horizon sits clear of the horizon of the layer in
    front, leaving a visible band of it above the nearer ground.

    The gap is a fraction of the layer's height rather than a constant: layers
    vary from 435px to 1300px tall, and a fixed gap that suits one either
    buries a tall layer or flings a short one off into its own band.
    """
    if not scaled:
        return scaled
    out = [None] * len(scaled)
    dy = 0
    prev_h = None
    for idx in range(len(scaled) - 1, -1, -1):
        img, _ = scaled[idx]
        hz = _horizon(img)
        if prev_h is not None:
            want = prev_h + max(min_gap, int(img.height * gap))
            here = img.height - hz - dy
            if here < want:
                dy += want - here
        out[idx] = (img, dy)
        prev_h = img.height - hz - dy
    return out


def _trim_flat_base(canvas, keep=24):
    """Crop a composed backdrop down to the part with scenery in it.

    Layers in a set differ a lot in height and the stagger lifts the far ones,
    so the stack ends up with transparent space above and a featureless band of
    ground below -- both single flat colours across the full width. Neither
    carries information, and both crowd the actual scenery out of a small
    thumbnail, so all but a sliver of each goes.
    """
    rgba = canvas.convert("RGBA")
    px = rgba.load()
    w, h = canvas.size
    step = max(1, w // 64)

    def uniform(row):
        # Sampling beats scanning every pixel and is just as decisive: a real
        # scene row varies somewhere across its width.
        first = px[0, row]
        return all(px[x, row] == first for x in range(0, w, step))

    bottom = h - 1
    while bottom > 0 and uniform(bottom):
        bottom -= 1
    top = 0
    while top < bottom and uniform(top):
        top += 1
    top = max(0, top - keep)
    bottom = min(h, bottom + keep)
    if bottom - top < 8:
        return canvas
    return canvas.crop((0, top, w, bottom))


def extract_backgrounds():
    """Forest backdrops: mountain pair plus up to three staggered tree rows."""
    print("Forest backgrounds:")
    for style, (mtn, trees) in sorted(FOREST_BG.items()):
        canvas = compose_surface(mtn, trees)
        if canvas is None:
            print("  style %-2s: no art" % style)
            continue
        save_thumb(canvas, os.path.join(ASSETS, "bg_%d.png" % style), (200, 140))
        print("  style %-2s <- mnt %s tree %s" % (style, mtn, trees))


def extract_snow_backgrounds():
    """Snow is the one biome with its own mountain array (snowMntBG)."""
    print("Snow backgrounds:")
    for style, (mtn, trees) in sorted(SNOW_BG.items()):
        canvas = compose_surface(mtn, trees)
        if canvas is None:
            print("  style %-2s: no art" % style)
            continue
        save_thumb(canvas, os.path.join(ASSETS, "snow_%d.png" % style),
                   (200, 140))
        print("  style %-2s <- mnt %s tree %s" % (style, mtn, trees))


def extract_underworld():
    """Five layers from Backgrounds/Underworld <n>.xnb, NOT Background_<n>.

    DrawUnderworldBackground walks index 4 down to 0, so layer 0 is drawn last
    and sits in front. Layers 1-4 carry fixed downward offsets.
    """
    print("Underworld backgrounds:")
    for style, ids in sorted(UNDERWORLD_BG.items()):
        layers = []
        for idx in range(4, -1, -1):
            img = load_bg(ids[idx], "Underworld")
            if img is None:
                continue
            # Several of these files are 2x2 animation sheets; the game draws
            # one quarter at a time, so crop to the first frame instead of
            # showing four tiled copies.
            if ids[idx] in UNDERWORLD_ANIM:
                img = img.crop((0, 0, img.width // 2, img.height // 2))
            layers.append((img, UNDERWORLD_OFFSETS.get(ids[idx], 0)))
        if not layers:
            print("  style %-2s: no art" % style)
            continue
        # Frame width comes from the FRONT layer (index 0, drawn last), which
        # is the full-width rock face. Taking the narrowest layer instead would
        # size the frame to things like Underworld 4, a 48px lava-fall strip
        # the game tiles sparsely across the screen.
        cw = layers[-1][0].width
        chh = max(i.height + dy for i, dy in layers)
        canvas = Image.new("RGBA", (cw, chh), (0, 0, 0, 255))
        for img, dy in layers:
            y = min(dy, max(0, chh - img.height))
            step = img.width if img.width >= cw // 4 else cw
            for x in range(0, cw, step):
                canvas.alpha_composite(img, (x, y))
        # The tallest layer is a lava-fall strip that runs well below the rock
        # floor, leaving a few hundred rows of empty canvas. Trimming to the
        # front layer keeps the thumbnail from being scaled down to a sliver.
        front_h = layers[-1][0].height
        canvas = canvas.crop((0, 0, cw, min(chh, front_h + 120)))
        save_thumb(canvas, os.path.join(ASSETS, "underworld_%d.png" % style),
                   (200, 140))
        print("  style %-2s <- Underworld %s" % (style, ids))


def extract_trees():
    print("Tree styles:")
    src = os.path.join(GAME, "Tiles_5.xnb")
    if not os.path.exists(src):
        print("  ! Tiles_5.xnb not found")
        return
    sheet = load_xnb(src)
    if sheet is None:
        return
    print("  sheet %dx%d" % (sheet.width, sheet.height))
    sheet.save(os.path.join(ASSETS, "tree_sheet.png"))
    # The trunk atlas is unrecognisable on its own. What actually distinguishes
    # one forest tree style from another on screen is the canopy, which lives
    # in Tree_Tops_<style>.xnb (styles 0-5 are the forest variants).
    # GetCommonTreeFoliageData remaps the rolled style before indexing the
    # texture: 0->0, 5->10, otherwise 5+n. Tree_Tops_1..5 are BIOME trees
    # (corruption, jungle, snow...) and never appear on plain forest grass.
    remap = {0: 0, 1: 6, 2: 7, 3: 8, 4: 9, 5: 10}
    for style in TREE_STYLES:
        src_top = os.path.join(GAME, "Tree_Tops_%d.xnb" % remap[style])
        if not os.path.exists(src_top):
            print("  style %d: no Tree_Tops file" % style)
            continue
        top = load_xnb(src_top)
        if top is None:
            continue
        # Each file holds three canopy variants side by side; the first is
        # representative enough for a picker thumbnail.
        third = top.width // 3 if top.width >= 3 else top.width
        crop = top.crop((0, 0, third, top.height))
        bbox = crop.getbbox()
        if bbox:
            crop = crop.crop(bbox)
        save_thumb(crop, os.path.join(ASSETS, "tree_%d.png" % style), (150, 150))
        print("  rolled %d -> Tree_Tops_%-2d  %dx%d" % (style, remap[style],
                                                       crop.width, crop.height))


def extract_moons():
    """Moon_0..8 map 1:1 onto moonType, which RandomizeMoonState rolls as
    Next(9). This is the moon STYLE baked into the world, not the nightly
    lunar phase, so any night shows the same sprite."""
    print("Moon styles:")
    for style in range(9):
        src = os.path.join(GAME, "Moon_%d.xnb" % style)
        if not os.path.exists(src):
            print("  style %d: missing" % style)
            continue
        img = load_xnb(src)
        if img is None:
            continue
        # The sheet stacks the lunar phases vertically; the first frame is the
        # full moon, which is the clearest one to identify a style by.
        frame_h = img.height // 8 if img.height >= 8 else img.height
        crop = img.crop((0, 0, img.width, frame_h))
        bbox = crop.getbbox()
        if bbox:
            crop = crop.crop(bbox)
        save_thumb(crop, os.path.join(ASSETS, "moon_%d.png" % style), (110, 110))
        print("  style %d -> Moon_%d  %dx%d" % (style, style, crop.width, crop.height))


def extract_oceans():
    """setBG(7, Next(8)) is one world-wide roll and a single texture, not a
    layer set, so the ocean backdrop identifies a seed from anywhere on the
    coast."""
    print("Ocean backgrounds:")
    for style, img_id in sorted(OCEAN_BG.items()):
        img = load_bg(img_id)
        if img is None:
            print("  style %d: missing Background_%d" % (style, img_id))
            continue
        save_thumb(img, os.path.join(ASSETS, "ocean_%d.png" % style), (200, 140))
        print("  style %d -> Background_%d" % (style, img_id))


def extract_biome_backgrounds():
    """The three-slot biome backdrops, each a single world-wide roll."""
    for biome, styles in BIOME_BG.items():
        print("%s backgrounds:" % biome)
        for style, ids in sorted(styles.items()):
            canvas = compose_surface(None, ids)
            if canvas is None:
                print("  style %-2s: no art" % style)
                continue
            save_thumb(canvas, os.path.join(ASSETS, "%s_%d.png" % (biome, style)),
                       (200, 140))
            print("  style %-2s <- %s" % (style, ids))


def main():
    os.makedirs(ASSETS, exist_ok=True)
    print("Writing to %s\n" % ASSETS)
    extract_backgrounds()
    print()
    extract_trees()
    print()
    extract_moons()
    print()
    extract_oceans()
    print()
    extract_biome_backgrounds()
    print()
    extract_snow_backgrounds()
    print()
    extract_underworld()
    print("\nDone.")


if __name__ == "__main__":
    main()
