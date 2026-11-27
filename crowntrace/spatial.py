"""Decode RGB images and produce WGS84 center points from affine GeoTIFFs."""
from dataclasses import dataclass
from io import BytesIO
import math

from PIL import Image, ImageOps

PIXEL_LIMIT = 9_000_000


@dataclass(frozen=True)
class Georeference:
    affine: tuple
    crs: str
    point_to_wgs84: object

    def location(self, x, y):
        a, b, c, d, e, f = self.affine
        lon, lat = self.point_to_wgs84(a*x + b*y + c, d*x + e*y + f)
        if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Koordinat raster berada di luar WGS84.")
        return [lon, lat]


def wgs84(crs):
    if crs == "EPSG:4326":
        return lambda x, y: (x, y)
    if crs == "EPSG:3857":
        radius = 6378137.0
        return lambda x, y: (math.degrees(x / radius), math.degrees(math.atan(math.sinh(y / radius))))
    from pyproj import Transformer
    from pyproj.exceptions import DataDirError, ProjError
    try:
        return Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    except (DataDirError, ProjError) as error:
        raise ValueError(f"Transformasi {crs} tidak dapat dibaca.") from error


def checked(affine, crs, size):
    a, b, c, d, e, f = affine
    if not all(math.isfinite(value) for value in affine) or abs(a*e - b*d) <= 1e-24:
        raise ValueError("Transformasi affine raster tidak valid.")
    georef = Georeference(affine, crs, wgs84(crs))
    w, h = size
    corners = [georef.location(x, y) for x, y in ((0, 0), (w, 0), (w, h), (0, h))]
    if max(p[0] for p in corners) - min(p[0] for p in corners) > 180:
        raise ValueError("Raster yang melintasi antimeridian belum didukung.")
    return georef, corners


def from_tags(img):
    tags = img.tag_v2
    directory = tags.get(34735, ())
    if len(directory) < 4:
        raise ValueError("GeoTIFF tidak memiliki kode CRS yang didukung.")
    keys = {}
    for index in range(4, min(len(directory), 4 + directory[3] * 4), 4):
        key, location, number, value = directory[index:index + 4]
        if location == 0 and number == 1:
            keys[key] = value
    epsg = keys.get(3072) or keys.get(2048)
    if not epsg or epsg == 32767:
        raise ValueError("CRS GeoTIFF tidak memiliki kode EPSG.")
    if 34264 in tags:
        matrix = tags[34264]
        if len(matrix) != 16 or matrix[12:16] != (0, 0, 0, 1):
            raise ValueError("Model transform GeoTIFF harus affine.")
        a, b, c, d, e, f = (matrix[i] for i in (0, 1, 3, 4, 5, 7))
    elif 33550 in tags and 33922 in tags:
        if len(tags[33922]) != 6:
            raise ValueError("Banyak titik ikat memerlukan pembacaan geospasial tambahan.")
        sx, sy, *_ = tags[33550]
        px, py, _, gx, gy, _ = tags[33922]
        a, b, c, d, e, f = sx, 0, gx - px*sx, 0, -sy, gy + py*sy
    else:
        raise ValueError("Metadata affine tidak tersedia.")
    if keys.get(1025, 1) == 2:
        c -= (a + b) / 2
        f -= (d + e) / 2
    return checked((a, b, c, d, e, f), f"EPSG:{epsg}", img.size)


def read_image(data):
    with Image.open(BytesIO(data)) as source:
        if source.format not in {"JPEG", "PNG", "TIFF"}:
            raise ValueError("Format harus JPEG, PNG, atau TIFF.")
        if min(source.size) < 32 or source.width * source.height > PIXEL_LIMIT:
            raise ValueError("Dimensi citra harus minimal 32 piksel dan maksimal 9 megapiksel.")
        georef, corners, warning = None, None, None
        if source.format == "TIFF":
            if source.tag_v2.get(274, 1) != 1:
                raise ValueError("Orientasi raster TIFF harus TopLeft.")
            if getattr(source, "n_frames", 1) != 1 or source.mode not in {"RGB", "RGBA"}:
                raise ValueError("Unggah GeoTIFF RGB/RGBA 8 bit satu halaman.")
            try:
                georef, corners = from_tags(source)
            except (ValueError, OverflowError, ImportError, IndexError) as error:
                warning = ("CRS ini memerlukan pyproj." if isinstance(error, ImportError) else str(error))
            image = source.convert("RGB")
        else:
            image = ImageOps.exif_transpose(source).convert("RGB")
        return image, georef, corners, warning
