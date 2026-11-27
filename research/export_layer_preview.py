"""Export an interactive CrownTrace result as a standalone HTML file."""

import argparse
from base64 import b64encode
from io import BytesIO
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TYPES = {".gpkg": "application/geopackage+sqlite3", ".geojson": "application/geo+json",
         ".csv": "text/csv", ".json": "application/json", ".png": "image/png"}


def data_url(payload, mime):
    return f"data:{mime};base64," + b64encode(payload).decode("ascii")


def survey_snapshot(folder):
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    mapped = json.loads((folder / "map.json").read_text(encoding="utf-8"))
    files = ("crowntrace.gpkg", "blocks.csv", "trees.geojson", "crowns.geojson", "blocks.geojson",
             "grid.geojson", "coverage.geojson", "review.geojson")
    downloads = {name: data_url((folder / name).read_bytes(), TYPES[(folder / name).suffix])
                 for name in files if (folder / name).is_file()}
    return {"job": {"name": "MOPAD Site 2 · Peta tajuk", "demo": True}, "summary": summary, "map": mapped,
            "image": data_url((folder / "preview.png").read_bytes(), "image/png"), "downloads": downloads}


def relative_snapshot(folder):
    source = json.loads((folder / "summary.json").read_text(encoding="utf-8"))["mosaic"]
    width, height = source["size_px"]
    photos = [{"name": name, "ring": ring,
               "center": [sum(axis) / len(axis) for axis in zip(*ring, strict=True)]}
              for name, ring in sorted(source["footprints_px"].items())]
    image = BytesIO()
    with Image.open(folder / "Palangka_Mosaik_Relatif.png") as raster:
        if raster.size != (width, height):
            raise ValueError("Pixel dimensions do not match the mosaic footprints.")
        raster.thumbnail((2048, 2048))
        raster.save(image, "WEBP", quality=88)
    filename = "jejak_foto_piksel.json"
    pixels = json.dumps({"coordinates": "image pixels", "size_px": [width, height], "photos": photos}).encode()
    return {"job": {"name": "Palangka Raya · Mosaik relatif", "relative": True},
            "summary": {"width": width, "height": height, "blocks": [], "grid": [], "points": 0, "tile_count": 0},
            "map": {"trees": [], "blocks": [], "photos": photos}, "image": data_url(image.getvalue(), "image/webp"),
            "files": [[filename, "Jejak foto (piksel)"]], "downloads": {filename: data_url(pixels, "application/json")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--relative", action="store_true")
    args = parser.parse_args()
    snapshot = relative_snapshot(args.result) if args.relative else survey_snapshot(args.result)
    html = (ROOT / "web/survey.html").read_text(encoding="utf-8")
    style = (ROOT / "web/survey.css").read_text(encoding="utf-8")
    script = (ROOT / "web/survey.js").read_text(encoding="utf-8")
    data = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    html = html.replace('<link rel="stylesheet" href="/survey.css">', f"<style>\n{style}\n</style>")
    html = html.replace('<script src="/survey.js" defer></script>', "")
    html = html.replace("</body>", f"<script>window.CROWNTRACE_SNAPSHOT={data};\n{script}\n</script>\n</body>")
    with args.output.open("x", encoding="utf-8", newline="\n") as out:
        out.write(html)


if __name__ == "__main__":
    main()
