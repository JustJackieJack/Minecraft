#!/usr/bin/env python3
"""Generuje teksture paneli strony: strona/www/tekstura.svg."""
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


def tekstura():
    r = random.Random(16)
    n = 32
    kolory = ["#2b2d38", "#2f313d", "#272934", "#33353f"]
    p = {(x, y): r.choices(kolory, [5, 3, 2, 1])[0] for x in range(n) for y in range(n)}
    (WWW / "tekstura.svg").write_text(svg(n, n, p))


if __name__ == "__main__":
    tekstura()
