"""Small NumPy geometry API used by the n8n Calculation Agent."""

from __future__ import annotations

import os
import re

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel


app = FastAPI(
    title="FastAPI Math Service",
    version="0.3.0",
    description="MVP geometry utilities for the Petroleum Engineering MAS.",
)


class TrajectoryIntersectionResult(BaseModel):
    filename: str
    intersection_md: float
    x: float
    y: float
    z: float


class TrajectoryIntersectionBatchResponse(BaseModel):
    results: list[TrajectoryIntersectionResult]


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eEdD][-+]?\d+)?")


def _decode_text(raw: bytes, filename: str) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError(f"{filename}: file is not valid UTF-8/CP1251 text.")


def _numbers(text: str) -> list[float]:
    return [float(value.replace("D", "E").replace("d", "e")) for value in _NUMBER.findall(text)]


def _parse_dev(text: str) -> np.ndarray:
    """Return standard DEV stations as an ``[MD, X, Y, Z]`` NumPy array."""

    rows: list[list[float]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "!", ";")):
            continue
        fields = re.split(r"[;\t ]+", line)
        if len(fields) < 4 and line.count(",") >= 3:
            fields = [field.strip() for field in line.split(",")]
        try:
            row = [float(field.replace(",", ".").replace("D", "E").replace("d", "e")) for field in fields[:4]]
        except ValueError:
            continue  # ordinary DEV column header
        if len(row) == 4 and np.isfinite(row).all():
            rows.append(row)

    trajectory = np.asarray(rows, dtype=np.float64)
    if trajectory.shape[0] < 2:
        raise ValueError("Trajectory file contains fewer than two valid MD/X/Y/Z stations.")
    if np.any(np.diff(trajectory[:, 0]) <= 0.0):
        raise ValueError("Trajectory MD values must be strictly increasing.")
    return trajectory


def _header_values(lines: list[str], keyword: str) -> list[float] | None:
    for line in lines:
        fields = line.strip().split(maxsplit=1)
        if fields and fields[0].upper() == keyword:
            return _numbers(fields[1]) if len(fields) > 1 else []
    return None


def _normalise_grid(
    values: list[float], rows: int, columns: int, limits: list[float], null_value: float | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    expected = rows * columns
    if (
        rows < 2
        or columns < 2
        or len(limits) < 4
        or limits[0] == limits[1]
        or limits[2] == limits[3]
        or len(values) != expected
    ):
        raise ValueError(
            f"Invalid CPS3 grid: expected a {rows}x{columns} grid ({expected} Z values), got {len(values)}."
        )
    z = np.asarray(values, dtype=np.float64).reshape(rows, columns)
    if null_value is not None:
        z[np.isclose(z, null_value)] = np.nan
    z[np.abs(z) >= 1.0e30] = np.nan
    x = np.linspace(limits[0], limits[1], columns, dtype=np.float64)
    # CPS3/ZMAP stores grid rows north-to-south; expose both axes ascending.
    y = np.linspace(limits[3], limits[2], rows, dtype=np.float64)
    if x[0] > x[-1]:
        x, z = x[::-1], z[:, ::-1]
    if y[0] > y[-1]:
        y, z = y[::-1], z[::-1, :]
    return x, y, z


def _parse_cps3(text: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parse common CPS3 ``FS*`` or ZMAP ``@GRID`` ASCII into NumPy arrays."""

    lines = text.splitlines()
    nrows = _header_values(lines, "FSNROW")
    ncols = _header_values(lines, "FSNCOL")
    limits = _header_values(lines, "FSLIMI")
    if nrows and limits and (len(nrows) >= 2 or ncols):
        rows = int(nrows[0])
        columns = int(nrows[1] if len(nrows) >= 2 else ncols[0])
        marker = next((i for i, line in enumerate(lines) if line.lstrip().startswith("->")), None)
        data_lines = lines[marker + 1 :] if marker is not None else [
            line for line in lines if not line.lstrip().upper().startswith("FS")
        ]
        nulls = _header_values(lines, "FSMISS") or _header_values(lines, "FSNULL")
        return _normalise_grid(
            _numbers("\n".join(data_lines)), rows, columns, limits, nulls[0] if nulls else None
        )

    markers = [i for i, line in enumerate(lines) if line.lstrip().startswith("@")]
    if len(markers) >= 2:  # ZMAP grid, commonly used as CPS3-compatible ASCII
        header = [_numbers(line) for line in lines[markers[0] + 1 : markers[1]]]
        geometry = next(
            (row for row in reversed(header) if len(row) >= 6 and row[0] >= 2 and row[1] >= 2), None
        )
        if geometry:
            null_value = header[0][1] if len(header[0]) >= 2 else None
            limits = [geometry[2], geometry[3], geometry[4], geometry[5]]
            return _normalise_grid(
                _numbers("\n".join(lines[markers[1] + 1 :])),
                int(geometry[0]),
                int(geometry[1]),
                limits,
                null_value,
            )
    raise ValueError(
        "Unsupported CPS3 grid: FSNROW with row/column counts and FSLIMI are required "
        "(a separate FSNCOL header is also supported)."
    )


def _surface_z(
    x_axis: np.ndarray, y_axis: np.ndarray, grid: np.ndarray, x: np.ndarray, y: np.ndarray
) -> np.ndarray:
    """Vectorized bilinear interpolation on a regular CPS3 grid."""

    x, y = np.broadcast_arrays(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
    result = np.full(x.shape, np.nan, dtype=np.float64)
    inside = (x >= x_axis[0]) & (x <= x_axis[-1]) & (y >= y_axis[0]) & (y <= y_axis[-1])
    if not inside.any():
        return result
    ix = np.clip(np.searchsorted(x_axis, x[inside], side="right") - 1, 0, x_axis.size - 2)
    iy = np.clip(np.searchsorted(y_axis, y[inside], side="right") - 1, 0, y_axis.size - 2)
    tx = (x[inside] - x_axis[ix]) / (x_axis[ix + 1] - x_axis[ix])
    ty = (y[inside] - y_axis[iy]) / (y_axis[iy + 1] - y_axis[iy])
    corners = np.stack((grid[iy, ix], grid[iy, ix + 1], grid[iy + 1, ix], grid[iy + 1, ix + 1]))
    interpolated = (
        corners[0] * (1.0 - tx) * (1.0 - ty)
        + corners[1] * tx * (1.0 - ty)
        + corners[2] * (1.0 - tx) * ty
        + corners[3] * tx * ty
    )
    interpolated[~np.isfinite(corners).all(axis=0)] = np.nan
    result[inside] = interpolated
    return result


def _find_intersection(
    filename: str, trajectory: np.ndarray, x_axis: np.ndarray, y_axis: np.ndarray, grid: np.ndarray
) -> TrajectoryIntersectionResult:
    surface = _surface_z(x_axis, y_axis, grid, trajectory[:, 1], trajectory[:, 2])
    delta = trajectory[:, 3] - surface
    exact = np.flatnonzero(np.isfinite(delta) & np.isclose(delta, 0.0, atol=1.0e-8))
    if exact.size:
        md, x, y, z = trajectory[exact[0]]
        return TrajectoryIntersectionResult(filename=filename, intersection_md=md, x=x, y=y, z=z)

    pairs = np.flatnonzero(
        np.isfinite(delta[:-1])
        & np.isfinite(delta[1:])
        & (np.signbit(delta[:-1]) != np.signbit(delta[1:]))
    )
    if not pairs.size:
        raise HTTPException(status_code=404, detail="Trajectory does not intersect the valid CPS3 grid area.")

    start, end = trajectory[pairs[0]], trajectory[pairs[0] + 1]
    low, high = 0.0, 1.0
    low_delta = float(delta[pairs[0]])
    for _ in range(50):
        fraction = (low + high) * 0.5
        point = start + fraction * (end - start)
        current = point[3] - float(_surface_z(x_axis, y_axis, grid, point[1:2], point[2:3])[0])
        if not np.isfinite(current):
            raise HTTPException(status_code=404, detail="Intersection crosses a null area of the CPS3 grid.")
        if np.signbit(current) == np.signbit(low_delta):
            low = fraction
        else:
            high = fraction
    fraction = (low + high) * 0.5
    md, x, y, _ = start + fraction * (end - start)
    z = float(_surface_z(x_axis, y_axis, grid, np.array([x]), np.array([y]))[0])
    return TrajectoryIntersectionResult(filename=filename, intersection_md=md, x=x, y=y, z=z)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "fastapi-math-service"}


@app.post("/api/v1/math/trajectory-intersection", response_model=TrajectoryIntersectionBatchResponse)
async def trajectory_intersection(
    trajectory_files: list[UploadFile] = File(..., description="Well trajectories in .dev text format."),
    surface_file: UploadFile = File(..., description="ASCII CPS3 structural surface/grid."),
) -> TrajectoryIntersectionBatchResponse:
    """Return one first-in-MD surface intersection for every uploaded trajectory."""

    try:
        x_axis, y_axis, grid = _parse_cps3(
            _decode_text(await surface_file.read(), surface_file.filename or "surface.cps3")
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    results: list[TrajectoryIntersectionResult] = []
    for trajectory_file in trajectory_files:
        filename = trajectory_file.filename or "trajectory.dev"
        if filename.startswith("__n8n_unused_"):
            continue
        try:
            trajectory = _parse_dev(_decode_text(await trajectory_file.read(), filename))
            results.append(_find_intersection(filename, trajectory, x_axis, y_axis, grid))
        except HTTPException as exc:
            # Keep a useful per-file diagnostic while preserving the original
            # status (e.g. 404 for a trajectory with no crossing).
            raise HTTPException(status_code=exc.status_code, detail=f"{filename}: {exc.detail}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{filename}: {exc}") from exc
    return TrajectoryIntersectionBatchResponse(results=results)


class AgentTaskBody(BaseModel):
    case_id: str = ""
    task_id: str = ""
    agent_id: str = "calculation_agent"
    objective: str = ""
    handoff_message: str = ""
    inputs: dict = {}
    context: dict = {}
    constraints: dict = {}


ACTIVITY = os.getenv("ACTIVITY_BASE_URL", "").rstrip("/")


def _emit(case_id: str, activity: str, payload: dict) -> None:
    if not case_id or not activity:
        return
    import json
    from urllib.request import Request, urlopen

    try:
        req = Request(
            f"{activity.rstrip('/')}/cases/{case_id}/events",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=8).read()
    except Exception:
        pass


def _read_artifact(inputs: dict, slot: str, case_id: str) -> tuple[str, bytes] | None:
    from pathlib import Path
    from urllib.request import urlopen

    artifacts = inputs.get("artifacts") if isinstance(inputs.get("artifacts"), dict) else {}
    meta = artifacts.get(slot) if isinstance(artifacts.get(slot), dict) else {}
    path = inputs.get(f"{slot}_path") or meta.get("path")
    url = inputs.get(f"{slot}_url")
    activity = str(inputs.get("activity_base_url") or ACTIVITY).rstrip("/")
    artifact_id = meta.get("artifact_id") or slot
    if path and Path(path).is_file():
        return Path(path).name, Path(path).read_bytes()
    if not url and activity and case_id:
        url = f"{activity}/cases/{case_id}/artifacts/{artifact_id}"
    if not url:
        return None
    with urlopen(url, timeout=30) as resp:
        data = resp.read()
    name = str(meta.get("filename") or Path(url).name or slot)
    return name, data


@app.post("/agent/run")
def agent_run(body: AgentTaskBody) -> dict:
    inputs = body.inputs if isinstance(body.inputs, dict) else {}
    activity = str(inputs.get("activity_base_url") or ACTIVITY).rstrip("/")
    _emit(
        body.case_id,
        activity,
        {
            "kind": "agent.accepted",
            "actor": "calculation_agent",
            "agent_id": "calculation_agent",
            "task_id": body.task_id,
            "status_message": "Считаю пересечение поверхности и траектории",
        },
    )
    traj = _read_artifact(inputs, "trajectory", body.case_id)
    surf = _read_artifact(inputs, "surface", body.case_id)
    if traj is None or surf is None:
        result = {
            "task_id": body.task_id,
            "status": "needs_input",
            "message": "Нужны файлы поверхности (CPS3) и траектории (.dev)",
            "data": {},
            "artifacts": {},
            "issues": [{"type": "missing_geometry_files"}],
            "assumptions": [],
            "requests": [
                {
                    "question_id": "Q-geom",
                    "question": "Приложите ASCII CPS3 поверхность и DEV-траекторию",
                    "options": [],
                }
            ],
        }
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.result",
                "actor": "calculation_agent",
                "agent_id": "calculation_agent",
                "task_id": body.task_id,
                "status": "needs_input",
                "status_message": result["message"],
            },
        )
        return result
    try:
        x_axis, y_axis, grid = _parse_cps3(_decode_text(surf[1], surf[0]))
        trajectory = _parse_dev(_decode_text(traj[1], traj[0]))
        hit = _find_intersection(traj[0], trajectory, x_axis, y_axis, grid)
    except HTTPException as exc:
        result = {
            "task_id": body.task_id,
            "status": "failed",
            "message": str(exc.detail),
            "data": {},
            "artifacts": {},
            "issues": [{"type": "geometry_failed", "detail": str(exc.detail)}],
            "assumptions": [],
            "requests": [],
        }
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.failed",
                "actor": "calculation_agent",
                "agent_id": "calculation_agent",
                "task_id": body.task_id,
                "status": "failed",
                "status_message": result["message"],
            },
        )
        return result
    except ValueError as exc:
        result = {
            "task_id": body.task_id,
            "status": "failed",
            "message": str(exc),
            "data": {},
            "artifacts": {},
            "issues": [{"type": "geometry_failed", "detail": str(exc)}],
            "assumptions": [],
            "requests": [],
        }
        _emit(
            body.case_id,
            activity,
            {
                "kind": "agent.failed",
                "actor": "calculation_agent",
                "agent_id": "calculation_agent",
                "task_id": body.task_id,
                "status": "failed",
                "status_message": result["message"],
            },
        )
        return result
    result = {
        "task_id": body.task_id,
        "status": "completed",
        "message": f"Найдено начало перфорации: MD {hit.intersection_md:.1f}",
        "data": {
            "top_perforation_md": hit.intersection_md,
            "x": hit.x,
            "y": hit.y,
            "z": hit.z,
            "filename": hit.filename,
        },
        "artifacts": {},
        "issues": [],
        "assumptions": ["METRIC units; trajectory and surface share CRS and Z convention"],
        "requests": [],
    }
    _emit(
        body.case_id,
        activity,
        {
            "kind": "agent.result",
            "actor": "calculation_agent",
            "agent_id": "calculation_agent",
            "task_id": body.task_id,
            "status": "completed",
            "status_message": result["message"],
        },
    )
    return result
