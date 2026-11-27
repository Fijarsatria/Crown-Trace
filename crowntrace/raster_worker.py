"""Isolate GDAL in the Python installation that supplies its native libraries."""
from collections import Counter
import json
from io import BytesIO
import os
from pathlib import Path
from struct import pack
import sys

import numpy as np
from osgeo import gdal, ogr, osr
from PIL import Image
from shapely.geometry import shape, mapping, Point, box
from shapely.ops import transform
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

if __package__:
    from .inference import LABELS
else:
    from inference import LABELS


def spatial_ref(value):
    crs = osr.SpatialReference()
    crs.SetFromUserInput(value)
    crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return crs


def projection(source, target):
    convert = osr.CoordinateTransformation(source, target)

    def project(x, y, z=None):
        # Shapely first calls with vectors and retries with scalar arguments.
        result = convert.TransformPoint(float(x), float(y))
        return result[0], result[1]

    return project


class Raster:
    def __init__(self, path):
        self.ds = gdal.OpenEx(path, gdal.OF_RASTER | gdal.OF_READONLY,
                              allowed_drivers=["GTiff"], sibling_files=[])
        if self.ds is None:
            raise ValueError("Berkas tidak dapat dibaca sebagai GeoTIFF.")
        self.width, self.height = self.ds.RasterXSize, self.ds.RasterYSize
        self.affine = self.ds.GetGeoTransform(can_return_null=True)
        self.crs = self.ds.GetSpatialRef()
        if self.affine is None or self.crs is None:
            raise ValueError("GeoTIFF harus memiliki posisi dan sistem koordinat.")
        inverse = gdal.InvGeoTransform(self.affine)
        if self.width < 32 or self.height < 32 or inverse is None:
            raise ValueError("Ukuran atau transformasi GeoTIFF tidak valid.")
        self.inverse = inverse
        self.crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        self.to_world = projection(self.crs, spatial_ref("EPSG:4326"))
        self.to_raster = projection(spatial_ref("EPSG:4326"), self.crs)
        self.to_area = projection(self.crs, spatial_ref("EPSG:6933"))
        bands = {self.ds.GetRasterBand(i).GetColorInterpretation(): i
                 for i in range(1, self.ds.RasterCount + 1)}
        self.bands = [bands.get(i, None) for i in (gdal.GCI_RedBand, gdal.GCI_GreenBand, gdal.GCI_BlueBand)]
        if not all(self.bands) and self.ds.RasterCount == 3:
            self.bands = [1, 2, 3]
        if not all(self.bands) or any(self.ds.GetRasterBand(i).DataType != gdal.GDT_Byte for i in self.bands):
            raise ValueError("Gunakan GeoTIFF RGB 8-bit.")
        self.alpha = bands.get(gdal.GCI_AlphaBand)
        self.blocks = []
        self.grid = []
        self.cell_area = {}
        self.covered = np.zeros(0, dtype=float)

    def metadata(self):
        authority, code = self.crs.GetAuthorityName(None), self.crs.GetAuthorityCode(None)
        return {"width": self.width, "height": self.height,
                "crs": f"{authority}:{code}" if authority and code else self.crs.GetName(),
                "geotransform": list(self.affine)}

    def coordinates(self, x, y):
        return gdal.ApplyGeoTransform(self.affine, x, y)

    def geometry(self, pixel):
        return transform(self.coordinates, pixel)

    def valid_geo(self, geometry):
        projected = transform(self.to_world, geometry)
        west, south, east, north = projected.bounds
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90) or east - west > 180:
            raise ValueError("Lokasi berada di luar rentang peta atau melintasi antimeridian.")
        if projected.geom_type == "Polygon":
            return orient(projected, sign=1.0)
        return projected

    def meta_blocks(self, features):
        footprint = self.geometry(box(0, 0, self.width, self.height))
        self.valid_geo(footprint)
        specified = features is not None
        if not specified:
            self.blocks.append(("Kebun", footprint))
        else:
            if not features or len(features) > 500:
                raise ValueError("GeoJSON harus berisi 1–500 poligon blok.")
            for index, item in enumerate(features, 1):
                properties = item.get("properties") or {}
                name = str(properties.get("name") or properties.get("nama") or item.get("id") or f"Blok {index}")
                geometry = shape(item["geometry"])
                if geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.is_valid:
                    raise ValueError(f"Batas {name} harus berupa poligon valid dalam WGS84.")
                native = transform(self.to_raster, geometry)
                if not native.is_valid or not native.intersects(footprint) or native.intersection(footprint).area <= 0:
                    raise ValueError(f"Batas {name} berada di luar cakupan GeoTIFF. Gunakan GeoJSON WGS84.")
                if any(name == existing or native.intersection(other).area > native.area * 1e-9
                       for existing, other in self.blocks):
                    raise ValueError("Nama blok harus berbeda dan poligon blok tidak boleh bertumpuk.")
                self.blocks.append((name, native))
        self.areas = [transform(self.to_area, geom).area for _, geom in self.blocks]
        self.block_tree = STRtree([geom for _, geom in self.blocks])
        self.covered = np.zeros(len(self.blocks), dtype=float)
        return len(self.blocks)

    def pixels(self, rect, limit=None):
        x, y, right, bottom = rect
        if not (0 <= x < right <= self.width and 0 <= y < bottom <= self.height):
            raise ValueError("Jendela berada di luar raster.")
        w, h = right - x, bottom - y
        scale = min(1.0, limit / max(w, h)) if limit else 1.0
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        args = x, y, w, h, *size
        rgb = np.stack([self.ds.GetRasterBand(i).ReadAsArray(*args) for i in self.bands], axis=-1)
        valid = np.full((size[1], size[0]), 255, dtype=np.uint8)
        if self.alpha:
            valid = np.minimum(valid, self.ds.GetRasterBand(self.alpha).ReadAsArray(*args))
        nodata = []
        for i in self.bands:
            band = self.ds.GetRasterBand(i)
            flags = band.GetMaskFlags()
            if flags & (gdal.GMF_ALL_VALID | gdal.GMF_ALPHA):
                continue
            mask = band.GetMaskBand().ReadAsArray(*args)
            if flags == gdal.GMF_NODATA:
                nodata.append(mask)
            else:
                valid = np.minimum(valid, mask)
                if flags & gdal.GMF_PER_DATASET:
                    break
        if len(nodata) == len(self.bands):
            valid = np.minimum(valid, np.maximum.reduce(nodata))
        return rgb, valid

    def coverage(self, tile, core, valid):
        x, y, right, bottom = core
        area = self.geometry(box(*core))
        pixels = int(np.count_nonzero(valid))
        total = int(valid.size)
        hectares = transform(self.to_area, area).area / 1e4
        covered = hectares * pixels / total
        self.cell_area[tile] = covered
        self.grid.append(feature(self.valid_geo(area), {
            "tile": tile, "coverage_pct": round(100 * pixels / total, 2), "missing_pixels": total - pixels,
            "area_ha": round(hectares, 6), "covered_ha": round(covered, 6),
        }))
        if pixels == 0:
            return
        unit_area = hectares * 1e4 / total
        for index in self.block_tree.query(area, predicate="intersects"):
            geom = self.blocks[index][1]
            if geom.covers(area):
                self.covered[index] += unit_area * pixels
                continue
            mask = self.rasterize(geom, x, y, right - x, bottom - y)
            self.covered[index] += unit_area * np.count_nonzero(mask & (valid != 0))

    def rasterize(self, geometry, x, y, width, height):
        grid = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
        east, north = self.coordinates(x, y)
        _, a, b, _, d, e = self.affine
        grid.SetGeoTransform((east, a, b, north, d, e))
        grid.SetSpatialRef(self.crs)
        memory = ogr.GetDriverByName("Memory").CreateDataSource("")
        layer = memory.CreateLayer("block", srs=self.crs)
        obj = ogr.Feature(layer.GetLayerDefn())
        obj.SetGeometry(ogr.CreateGeometryFromWkb(geometry.wkb))
        layer.CreateFeature(obj)
        gdal.RasterizeLayer(grid, [1], layer, burn_values=[1])
        return grid.ReadAsArray().astype(bool)

    def point(self, x, y):
        native = Point(self.coordinates(x, y))
        matches = self.block_tree.query(native, predicate="covered_by")
        index = min(matches, default=None)
        return self.valid_geo(native), self.blocks[index][0] if index is not None else None

    def export(self, destination, objects):
        outf = Path(destination)
        points, crowns, review, overlay = [], [], [], []
        visual_counts = Counter()
        cell_counts = Counter()
        blocks_by_label = {name: Counter() for name, _ in self.blocks}
        for obj in objects:
            point, block = self.point(*obj["center"])
            label = obj.get("visual_label", "Unassigned")
            visual_counts[label] += 1
            cell_counts[obj["core_tile"]] += 1
            if block is not None:
                blocks_by_label[block][label] += 1
            props = {"id": obj["id"], "block": block, "score": obj["score"], "visual_label": label,
                     "core_tile": obj["core_tile"]}
            tree = feature(point, props)
            points.append(tree)
            if label in {"Yellow", "Dead"}:
                review.append(tree)
            item = {**props, "center": obj["center"], "tile": obj["tile"],
                    "coordinates": list(point.coords)[0]}
            if "mask" in obj:
                polygon = polygonize(obj["mask"], obj["crop"], obj["center"], obj.get("mask_shape", (64, 64)))
                if polygon is not None:
                    native = self.geometry(polygon)
                    area = round(transform(self.to_area, native).area, 3)
                    item["crown_m2"] = area
                    item["rings"] = mapping(polygon)["coordinates"]
                    crowns.append(feature(self.valid_geo(native), {**props, "crown_m2": area}))
            overlay.append(item)
        blocks = []
        for i, (name, geom) in enumerate(self.blocks):
            covered = min(self.covered[i], self.areas[i])
            counts = blocks_by_label[name]
            total = sum(counts.values())
            props = {"block": name, "area_ha": round(self.areas[i] / 1e4, 4),
                     "covered_ha": round(covered / 1e4, 4),
                     "coverage_pct": round(100 * covered / self.areas[i], 2),
                     "trees": total,
                     "trees_per_covered_ha": round(total / (covered / 1e4), 2) if covered else None}
            props.update({f"visual_{label.lower()}": counts[label] for label in (*LABELS, "Unassigned")})
            blocks.append(feature(self.valid_geo(geom), props))
        for cell in self.grid:
            props = cell["properties"]
            tile = props["tile"]
            count, hectares = cell_counts[tile], self.cell_area[tile]
            props.update(trees=count, trees_per_covered_ha=round(count / hectares, 2) if hectares else None)
        gaps = [cell for cell in self.grid if cell["properties"]["missing_pixels"]]
        layers = {"trees": points, "crowns": crowns, "grid": self.grid, "blocks": blocks,
                  "coverage": gaps, "review": review}
        for name, items in layers.items():
            (outf / f"{name}.geojson").write_text(json.dumps(collection(items), ensure_ascii=False,
                                                           separators=(",", ":")), encoding="utf-8")
        geopackage(outf, layers)
        records = [item["properties"] for item in blocks]
        outlines = [mapping(transform(lambda x, y: gdal.ApplyGeoTransform(self.inverse, x, y), geom))
                    for _, geom in self.blocks]
        (outf / "map.json").write_text(json.dumps({"trees": overlay, "blocks": outlines}, ensure_ascii=False,
                                                 separators=(",", ":")), encoding="utf-8")
        return {"points": len(points), "crowns": len(crowns), "blocks": records,
                "coverage_cells": len(gaps), "review_points": len(review),
                "cell_stats": [cell["properties"] for cell in self.grid],
                "visual_counts": {label: visual_counts[label] for label in (*LABELS, "Unassigned")},
                "outside_blocks": len(points) - sum(row["trees"] for row in records)}

    def image(self, request):
        rect = request["rect"]
        limit = request.get("limit")
        if limit is None and (rect[2] - rect[0]) * (rect[3] - rect[1]) > 2048**2:
            raise ValueError("Jendela pemrosesan terlalu besar.")
        rgb, valid = self.pixels(rect, limit)
        if request.get("core"):
            x, y, _, _ = rect
            lx, ly, rx, ry = request["core"]
            self.coverage(request["tile"], request["core"], valid[ly-y:ry-y, lx-x:rx-x])
        if limit:
            data = BytesIO()
            Image.fromarray(np.dstack((rgb, valid))).save(data, "PNG", compress_level=3)
            return data.getvalue()
        rgb[valid == 0] = 0
        return np.dstack((rgb, valid)).tobytes()


def feature(geom, properties):
    return {"type": "Feature", "geometry": mapping(geom), "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def polygonize(encoded, crop, center, mask_shape=(64, 64)):
    from base64 import b64decode
    height, width = mask_shape
    if not (1 <= height <= 2048 and 1 <= width <= 2048):
        raise ValueError("Ukuran mask di luar rentang.")
    packed = b64decode(encoded, validate=True)
    if len(packed) != (height * width + 7) // 8:
        raise ValueError("Ukuran data mask tidak sesuai dengan dimensinya.")
    mask = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), count=height * width).reshape(height, width)
    if not mask.any():
        return None
    x, y, right, bottom = crop
    source = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    source.SetGeoTransform((x, (right - x) / width, 0, y, 0, (bottom - y) / height))
    band = source.GetRasterBand(1)
    band.WriteArray(mask)
    memory = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = memory.CreateLayer("mask", geom_type=ogr.wkbPolygon)
    gdal.Polygonize(band, band, layer, -1)
    polygons = [shape(json.loads(item.GetGeometryRef().ExportToJson())).buffer(0) for item in layer]
    candidates = [poly for geom in polygons
                  for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else (geom,))
                  if not poly.is_empty]
    if not candidates:
        return None
    crown = max(candidates, key=lambda poly: (poly.covers(Point(center)), poly.area))
    return crown.simplify(max(right - x, bottom - y) / 125, preserve_topology=True)


def geopackage(directory, layers):
    path = str(directory / "crowntrace.gpkg")
    # On Windows SQLite needs room for its journal name as well as the database.
    if os.name == "nt" and len(path) >= 240:
        path = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(path)
    for name, items in layers.items():
        source = ogr.Open(json.dumps(collection(items)))
        if items:
            ds.CopyLayer(source.GetLayer(0), name)
        else:
            ds.CreateLayer(name, spatial_ref("EPSG:4326"),
                           ogr.wkbPoint if name in {"trees", "review"} else ogr.wkbPolygon)
    ds = None


def write(status, payload):
    if not isinstance(payload, bytes):
        payload = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    stream = sys.stdout.buffer
    stream.write(pack("<BQ", status, len(payload)))
    stream.write(payload)
    stream.flush()


def main():
    gdal.UseExceptions()
    ogr.UseExceptions()
    osr.UseExceptions()
    commands = iter(sys.stdin.buffer)
    try:
        raster = Raster(json.loads(next(commands))["path"])
        write(0, raster.metadata())
    except Exception as error:
        write(1, {"detail": str(error)})
        return
    for line in commands:
        try:
            request = json.loads(line)
            method = request["method"]
            if method == "image":
                output = raster.image(request)
            elif method == "blocks":
                output = raster.meta_blocks(request.get("features"))
            elif method == "export":
                objects = json.loads(Path(request["objects"]).read_text(encoding="utf-8"))
                output = raster.export(request["destination"], objects)
            else:
                raise ValueError("Permintaan raster tidak dikenal.")
            write(0, output)
        except Exception as error:
            write(1, {"detail": str(error)})


if __name__ == "__main__":
    main()
