"""Create two smaller demo GeoTIFF windows with the original raster metadata.

Run with QGIS Python after prepare_upload_pack. All three maps share a synthetic
geographic placement of the same MOPAD imagery; they are not independent estates.
"""
from pathlib import Path
import csv
import hashlib
from osgeo import gdal

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "data/UJI_SENDIRI"
SOURCE = ROOT / "data/demo_mopad_mosaic.tif"
FOLDER = PACK / "06_PETA_GEOTIFF"
INDEX = PACK / "INDEX_CITRA.csv"


def main():
    gdal.UseExceptions()
    with INDEX.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = [row for row in reader if not row["file"].startswith(("06_PETA_GEOTIFF/02_", "06_PETA_GEOTIFF/03_"))]
    for name, window in [("02_MOPAD_DEMO_BARAT_1400x1150.tif", [0, 0, 1400, 1150]),
                         ("03_MOPAD_DEMO_TIMUR_1600x1400.tif", [1200, 900, 1600, 1400])]:
        target = FOLDER / name
        raster = gdal.Translate(str(target), str(SOURCE), srcWin=window, format="GTiff",
                                creationOptions=["COMPRESS=LZW", "TILED=YES"])
        assert raster is not None and raster.GetProjection()
        width, height = raster.RasterXSize, raster.RasterYSize
        raster = None
        rows.append(dict(file=f"06_PETA_GEOTIFF/{name}", source="MOPAD registered demonstration crop",
                         original="demo_mopad_mosaic.tif", split="derived demo", width=width, height=height,
                         recommended_mode="Peta kebun (GeoTIFF) + mask pilot", reference="koordinat geografis sintetis",
                         notes="Potongan raster demonstrasi yang sama; gunakan tanpa file batas blok; bukan kebun independen.",
                         sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    with INDEX.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader();writer.writerows(rows)
    print(f"Prepared {len(rows)} image entries including three georeferenced demo maps.")


if __name__ == "__main__":
    main()
