"""Evaluate validation-selected confidence thresholds using saved detector predictions."""
from argparse import ArgumentParser
from bisect import bisect_left
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import mean


THRESHOLDS = tuple(i / 100 for i in range(5, 91, 5))
TITLES = {
    "full_640": "Gambar penuh 256 px",
    "full_1024": "Gambar penuh 416 px",
    "uniform": "Dua potongan seragam",
    "random": "Dua potongan acak",
    "no_gap": "Dua potongan tanpa celah",
    "gap_only": "Dua potongan berbasis celah",
    "adaptive": "Dua potongan adaptif",
    "exhaustive": "Semua potongan",
}


def read_benchmark(folder, threshold):
    with (folder / "per_image.csv").open(encoding="utf-8", newline="") as source:
        counts = {}
        for row in csv.DictReader(source):
            key = row["split"], row["method"], row["image"]
            if key in counts:
                raise ValueError(f"Baris benchmark ganda: {key}")
            counts[key] = int(row["true"]), int(row["pred"])

    groups = defaultdict(list)
    with (folder / "predictions.jsonl").open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            split, method, image = row["split"], row["method"], row["image"]
            actual, previous = counts.pop((split, method, image))
            scores = sorted(float(d["score"]) for d in row["detections"])
            if len(scores) - bisect_left(scores, threshold) != previous:
                raise ValueError(f"Prediksi dan CSV benchmark tidak cocok untuk {method}, {image}.")
            groups[method, split].append((image, actual, scores))
    if counts:
        raise ValueError("Sebagian citra benchmark tidak memiliki rekaman prediksi.")
    return groups


def errors(images, threshold):
    for image, actual, scores in images:
        predicted = len(scores) - bisect_left(scores, threshold)
        yield image, actual, predicted, predicted - actual


def metrics(images, threshold):
    residuals = [error for _, _, _, error in errors(images, threshold)]
    return mean(map(abs, residuals)), mean(residuals)


def report_text(rows, model, shared):
    chart = [
        "# Uji sensitivitas ambang deteksi",
        "",
        f"Analisis retrospektif memakai prediksi tersimpan dari checkpoint `{model}`.",
        f"Setiap metode mendapat ambang dari nilai MAE terendah pada {rows[0]['val_images']} citra validasi.",
        "Pencarian memakai grid 0,05 hingga 0,90 dengan langkah 0,05.",
        "Jika MAE validasi sama, dipilih ambang yang lebih tinggi. "
        f"Ambang terpilih dievaluasi pada {rows[0]['test_images']} citra uji.",
        f"Pembanding memakai ambang bersama {shared:.2f} dari benchmark utama.",
        "",
        "| Metode | Ambang terpilih | MAE validasi bersama | MAE validasi terpilih | MAE uji bersama | MAE uji terpilih | Bias uji terpilih |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        chart.append(
            f"| {TITLES[row['method']]} | {row['selected_threshold']:.2f} | "
            f"{row['val_mae_shared']:.2f} | {row['val_mae_selected']:.2f} | "
            f"{row['test_mae_shared']:.2f} | {row['test_mae_selected']:.2f} | {row['test_bias_selected']:+.2f} |"
        )
    by_method = {row["method"]: row for row in rows}
    best = min(rows, key=lambda row: row["test_mae_selected"])
    adaptive = by_method["adaptive"]["test_mae_selected"]
    uniform = by_method["uniform"]["test_mae_selected"]
    difference = adaptive - uniform
    comparison = (f"Galat adaptif {abs(difference):.2f} tajuk per citra "
                  f"{'lebih tinggi' if difference > 0 else 'lebih rendah'} dari potongan seragam."
                  if difference else "Dua potongan adaptif dan seragam memberi MAE uji yang sama.")
    chart.extend([
        "",
        f"Setelah ambang ditetapkan per metode, **{TITLES[best['method']]}** mencatat MAE uji terendah, "
        f"**{best['test_mae_selected']:.2f} tajuk per citra**. " + comparison,
        "",
        "Analisis ini dikerjakan setelah hasil utama diketahui. Peringkat berfungsi sebagai bukti eksploratif "
        "dalam MOPAD Site 2. Pengujian konfirmatori perlu memakai kebun atau kelompok terpisah dengan "
        "aturan penalaan yang ditetapkan sebelum membuka hasil uji.",
    ])
    return "\n".join(chart) + "\n"


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=Path("results/expanded_benchmark"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.benchmark / "summary.json").read_text(encoding="utf-8"))
    shared = float(summary["metrics"]["val"]["full_640"]["operating_threshold"])
    groups = read_benchmark(args.benchmark, shared)
    rows, image_rows = [], []
    for method in TITLES:
        validation, test = groups[method, "val"], groups[method, "test"]
        if not validation or not test:
            raise ValueError(f"Metode {method} memerlukan prediksi validasi dan uji.")
        selected = min(THRESHOLDS, key=lambda threshold: (metrics(validation, threshold)[0], -threshold))
        val_mae, val_bias = metrics(validation, selected)
        test_mae, test_bias = metrics(test, selected)
        rows.append(dict(method=method, shared_threshold=shared, selected_threshold=selected,
                         val_images=len(validation), test_images=len(test),
                         val_mae_shared=metrics(validation, shared)[0], val_mae_selected=val_mae,
                         val_bias_selected=val_bias, test_mae_shared=metrics(test, shared)[0],
                         test_mae_selected=test_mae, test_bias_selected=test_bias))
        for split, images in (("val", validation), ("test", test)):
            image_rows.extend((method, split, selected, *row) for row in errors(images, selected))

    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "summary.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (args.output / "per_image.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(("method", "split", "threshold", "image", "true", "pred", "count_error"))
        writer.writerows(image_rows)
    report = report_text(rows, summary["model"], shared)
    (args.output / "Ringkasan.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
