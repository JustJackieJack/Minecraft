#!/usr/bin/env python3
"""Generuje wlasne grafiki strony: strona/www/scena.svg i tekstura.svg."""
import pathlib
import random

WWW = pathlib.Path(__file__).parent.parent / "www"


def svg(sz, wy, piksele, tlo=None):
    """piksele: {(x, y): kolor}. Sasiednie piksele w wierszu laczy w jeden odcinek."""
    sciezki = {}
    for y in range(wy):
        x = 0
        while x < sz:
            kolor = piksele.get((x, y))
            if kolor is None:
                x += 1
                continue
            start = x
            while x < sz and piksele.get((x, y)) == kolor:
                x += 1
            sciezki.setdefault(kolor, []).append(f"M{start} {y}h{x - start}v1h{start - x}z")
    tresc = "".join(f'<path fill="{k}" d="{"".join(d)}"/>' for k, d in sciezki.items())
    prostokat = f'<rect width="{sz}" height="{wy}" fill="{tlo}"/>' if tlo else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {sz} {wy}" '
            f'width="{sz}" height="{wy}" shape-rendering="crispEdges" '
            f'preserveAspectRatio="xMidYMax slice">{prostokat}{tresc}</svg>\n')


def scena():
    r = random.Random(1109)
    sz, wy = 240, 64
    p = {}
    # Niebo: pasy gradientu i gwiazdy.
    pasy = ["#0d0f24", "#111430", "#16193a", "#1c2046", "#232852"]
    for y in range(wy):
        for x in range(sz):
            drgania = (x * 7 + y * 13) % 5 == 0
            p[(x, y)] = pasy[min(4, (y + (2 if drgania else 0)) // 9)]
    for _ in range(70):
        p[(r.randrange(sz), r.randrange(38))] = r.choice(["#e8e6c9", "#8d90b8", "#5f6490"])
    # Ksiezyc.
    for y in range(-7, 8):
        for x in range(-7, 8):
            if x * x + y * y <= 50:
                kolor = "#d9dbe8" if (x + y) % 7 and (x * 3 - y) % 11 else "#a9adc6"
                p[(186 + x, 14 + y)] = kolor
    # Morze.
    for y in range(46, wy):
        for x in range(sz):
            fala = (x + y * 5 + (y % 2) * 3) % 17 == 0
            p[(x, y)] = "#3a6ea5" if fala else ("#1f4a7a" if y < 54 else "#17395f")

    # Wyspa.
    srodek = 112
    def wierzch(x):
        d = abs(x - srodek) / 62
        return 46 - int(round(15 * max(0, 1 - d * d)))
    for x in range(srodek - 62, srodek + 63):
        g = wierzch(x)
        if g >= 46:
            continue
        for y in range(g, 52):
            brzeg = abs(x - srodek) > 50
            if y == g:
                k = "#e2cf8f" if brzeg else ("#5fa83f" if (x * 3) % 5 else "#4c8f33")
            elif y <= g + 1 and not brzeg:
                k = "#467d2d"
            elif y <= g + 4:
                k = "#d2bb79" if brzeg else ("#7a5536" if (x + y) % 4 else "#65452b")
            else:
                k = "#6f7380" if (x * 5 + y * 3) % 6 else "#585c68"
            p[(x, y)] = k

    def drzewo(x):
        g = wierzch(x)
        for y in range(g - 8, g):
            p[(x, y)] = "#5b3d25"
        for dy in range(-13, -6):
            for dx in range(-4, 5):
                if abs(dx) + abs(dy + 9) <= 5:
                    p[(x + dx, g + dy)] = "#2e6b2a" if (dx * 3 + dy) % 4 else "#3f8a37"

    drzewo(84)
    drzewo(138)
    drzewo(146)

    # Chatka. Okno lezy pod elementem .okno na stronie.
    x0, g = 103, wierzch(112)
    for y in range(g - 10, g):
        for x in range(x0, x0 + 19):
            p[(x, y)] = "#8a6239" if (y % 3 or x % 6 == 0) else "#6d4b2b"
    for i, y in enumerate(range(g - 16, g - 9)):
        for x in range(x0 + 4 - i, x0 + 15 + i):
            p[(x, y)] = "#8f3f33" if (x + y) % 3 else "#74312a"
    for y in range(g - 6, g):
        for x in range(x0 + 3, x0 + 7):
            p[(x, y)] = "#3f2a18"
    for y in range(g - 8, g - 4):
        for x in range(x0 + 11, x0 + 16):
            p[(x, y)] = "#1a1c33"
    (WWW / "scena.svg").write_text(svg(sz, wy, p))
    swiatlo = {(x, y): ("#ffd76a" if x != x0 + 13 and y != g - 6 else "#6d4b2b")
               for y in range(g - 8, g - 4) for x in range(x0 + 11, x0 + 16)}
    (WWW / "okno.svg").write_text(svg(sz, wy, swiatlo))


def tekstura():
    r = random.Random(16)
    n = 32
    kolory = ["#2b2d38", "#2f313d", "#272934", "#33353f"]
    p = {(x, y): r.choices(kolory, [5, 3, 2, 1])[0] for x in range(n) for y in range(n)}
    (WWW / "tekstura.svg").write_text(svg(n, n, p))


if __name__ == "__main__":
    scena()
    tekstura()
