# Quick start — local research workspace

## 1. Install dependencies

From the cloned repository on Windows, with Python 3 installed:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For GeoTIFF analysis, install QGIS Desktop/GDAL. The code looks for the local QGIS Python launcher or a configured `CROWNTRACE_GDAL_PYTHON`. See [data and model requirements](DATA_AND_MODELS.md).

## 2. Open the workspace

Run `Buka_Riset_WebGIS.cmd`, or:

```powershell
& .\.venv\Scripts\python.exe -m crowntrace.server --port 8767 --open-browser
```

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/). The public clone contains source, **not trained checkpoints or example photographs**. The workspace can show its interface and readiness state, but cannot execute model inference until compatible assets are prepared. A complete local research installation can use `Buka_Riset_WebGIS.cmd --check` to check its assets.

## 3. Analyse imagery (when the model is ready)

1. Name a project (optional) and select one or more overhead JPEG/PNG images or a georeferenced GeoTIFF.
2. Click **Mulai analisis**. The workspace uploads, saves and processes the project. Keep the server terminal open. The browser may be closed after the upload is complete.
3. Open **Pekerjaan tersimpan** to monitor progress or resume. Choose a resulting area to inspect the imagery, crowns, tree points and coverage.
4. Save the rendered map as PNG or print the area report. Technical GIS and model data remain optional exports.

The configured limit is 300 files per project; JPEG/PNG up to 32 MiB/25 MP, GeoTIFF up to 12 GiB with adequate disk and RAM. Actual performance depends on hardware, imagery and available storage. The local service portal is optional (`Buka_WebGIS.cmd`, port 8765); it requires additional assets and is not a production platform. The simulated role buttons work only with a separate local demo database, without a shared password. Real operational sign-in requires operator-created accounts; do not expose the prototype on the internet.

## Scientific testing

Install optional test dependencies:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-optional.txt
& .\.venv\Scripts\python.exe -m pytest -q tests/test_workspace_projects.py tests/test_workspace.py tests/test_lab_batch_map.py
node tests/test_workspace_frontend.cjs
```

The Node test is an offline DOM/API contract test, not a visual browser test. Other model and integration tests require local datasets/checkpoints, and some require QGIS or browser dependencies. The source snapshot does not promise a fully reproducible trained model without the separately prepared research assets.
