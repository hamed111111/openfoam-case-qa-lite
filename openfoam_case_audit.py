#!/usr/bin/env python3
"""Static, dependency-free QA scan for OpenFOAM case folders.

This tool does not run OpenFOAM or certify the physics. It catches common
handover defects before expensive solver time or client review.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    file: str = ""
    evidence: str = ""


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*?$", "", text, flags=re.M)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scalar(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s+([^;]+);", strip_comments(text))
    return match.group(1).strip() if match else None


def number(text: str, key: str) -> float | None:
    value = scalar(text, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def named_blocks(text: str) -> list[tuple[str, str]]:
    """Return top-level-ish `name { ... }` blocks using balanced braces."""
    clean = strip_comments(text)
    blocks: list[tuple[str, str]] = []
    pattern = re.compile(r"(?m)^\s*([A-Za-z0-9_.:+\-]+)\s*\{")
    for match in pattern.finditer(clean):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(clean)):
            char = clean[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((match.group(1), clean[start + 1 : index]))
                    break
    return blocks


class Audit:
    def __init__(self, case: Path) -> None:
        self.case = case.resolve()
        self.findings: list[Finding] = []
        self.metadata: dict[str, object] = {}
        self.patches: list[str] = []

    def add(self, severity: str, code: str, message: str, path: Path | None = None, evidence: str = "") -> None:
        file = ""
        if path:
            try:
                file = str(path.resolve().relative_to(self.case))
            except ValueError:
                file = str(path)
        self.findings.append(Finding(severity, code, message, file, evidence[:240]))

    def require(self, rel: str, kind: str = "file") -> Path | None:
        path = self.case / rel
        exists = path.is_dir() if kind == "dir" else path.is_file()
        if not exists:
            self.add("critical", "STRUCTURE_MISSING", f"Required {kind} is missing: {rel}", path)
            return None
        return path

    def run(self) -> dict[str, object]:
        if not self.case.is_dir():
            raise SystemExit(f"Case directory does not exist: {self.case}")

        control = self.require("system/controlDict")
        schemes = self.require("system/fvSchemes")
        solution = self.require("system/fvSolution")
        boundary = self.require("constant/polyMesh/boundary")
        zero = self.require("0", "dir")

        if control:
            self.check_control(control)
        if schemes:
            self.check_schemes(schemes)
        if solution:
            self.check_solution(solution)
        if boundary:
            self.check_boundary(boundary)
        if zero:
            self.check_fields(zero)
        self.check_properties()
        self.check_includes()

        counts = {level: sum(f.severity == level for f in self.findings) for level in ("critical", "warning", "info")}
        score = max(0, 100 - 20 * counts["critical"] - 6 * counts["warning"] - counts["info"])
        status = "BLOCK" if counts["critical"] else ("REVIEW" if counts["warning"] else "PASS_STATIC")
        return {
            "tool": "OpenFOAM Case QA Lite",
            "version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "case": str(self.case),
            "status": status,
            "static_readiness_score": score,
            "counts": counts,
            "metadata": self.metadata,
            "findings": [asdict(f) for f in self.findings],
            "limitations": [
                "Static scan only; OpenFOAM is not executed.",
                "A clean scan does not validate mesh quality, convergence, physics, units, or results.",
                "Dictionary includes and custom code may require manual review.",
            ],
        }

    def check_control(self, path: Path) -> None:
        text = read_text(path)
        keys = ("application", "startFrom", "startTime", "stopAt", "endTime", "deltaT", "writeControl", "writeInterval")
        self.metadata.update({key: scalar(text, key) for key in keys if scalar(text, key) is not None})
        for key in ("application", "endTime", "deltaT", "writeControl", "writeInterval"):
            if scalar(text, key) is None:
                self.add("critical", "CONTROL_REQUIRED", f"controlDict has no explicit `{key}` entry.", path)
        start = number(text, "startTime") or 0.0
        end = number(text, "endTime")
        delta = number(text, "deltaT")
        interval = number(text, "writeInterval")
        if end is not None and end <= start:
            self.add("critical", "TIME_RANGE", "endTime must be greater than startTime.", path, f"start={start}, end={end}")
        if delta is not None and delta <= 0:
            self.add("critical", "DELTA_T", "deltaT must be positive.", path, str(delta))
        if interval is not None and interval <= 0:
            self.add("critical", "WRITE_INTERVAL", "writeInterval must be positive.", path, str(interval))
        if "adjustTimeStep" in text and scalar(text, "maxCo") is None:
            self.add("warning", "COURANT_UNBOUNDED", "adjustTimeStep appears configured but maxCo is not explicit.", path)
        if scalar(text, "runTimeModifiable") is None:
            self.add("info", "RUNTIME_POLICY", "runTimeModifiable is not explicit; document the intended runtime-edit policy.", path)

    def check_schemes(self, path: Path) -> None:
        text = strip_comments(read_text(path))
        for section in ("gradSchemes", "divSchemes", "laplacianSchemes", "interpolationSchemes", "snGradSchemes"):
            if not re.search(rf"(?m)^\s*{section}\s*\{{", text):
                self.add("warning", "SCHEME_SECTION", f"No `{section}` section detected.", path)
        div_block = next((body for name, body in named_blocks(text) if name == "divSchemes"), "")
        if div_block and not re.search(r"(?m)^\s*default\s+none\s*;", div_block):
            self.add("warning", "DIV_DEFAULT", "Consider `default none;` in divSchemes to prevent unintended discretization.", path)

    def check_solution(self, path: Path) -> None:
        text = strip_comments(read_text(path))
        if not re.search(r"(?m)^\s*solvers\s*\{", text):
            self.add("critical", "SOLVERS_MISSING", "fvSolution has no solvers block.", path)
        algorithm = next((name for name in ("PIMPLE", "SIMPLE", "PISO") if re.search(rf"(?m)^\s*{name}\s*\{{", text)), None)
        if not algorithm:
            self.add("warning", "ALGORITHM_MISSING", "No PIMPLE, SIMPLE, or PISO control block detected.", path)
        else:
            self.metadata["algorithm"] = algorithm
        if "residualControl" not in text:
            self.add("warning", "RESIDUAL_CONTROL", "No residualControl block detected; define and document convergence criteria.", path)
        if "relaxationFactors" not in text and algorithm == "SIMPLE":
            self.add("warning", "RELAXATION", "SIMPLE case has no explicit relaxationFactors block.", path)

    def check_boundary(self, path: Path) -> None:
        text = strip_comments(read_text(path))
        candidates = []
        for name, body in named_blocks(text):
            if re.search(r"(?m)^\s*type\s+[^;]+;", body):
                candidates.append((name, scalar(body, "type") or "unknown", number(body, "nFaces")))
        self.patches = [name for name, _, _ in candidates]
        self.metadata["patch_count"] = len(candidates)
        self.metadata["patches"] = [{"name": n, "type": t, "nFaces": f} for n, t, f in candidates]
        if not candidates:
            self.add("critical", "BOUNDARY_PARSE", "No boundary patches could be parsed.", path)
        for name, patch_type, faces in candidates:
            if faces is not None and faces <= 0:
                self.add("warning", "EMPTY_PATCH", f"Patch `{name}` has no faces.", path)
            if name == "defaultFaces" and faces and faces > 0:
                self.add("warning", "DEFAULT_FACES", "defaultFaces is non-empty; verify that no intended patch was lost during meshing.", path, str(int(faces)))
            if patch_type == "empty":
                self.add("info", "EMPTY_DIMENSION", f"Patch `{name}` is empty; confirm the case is intentionally 2-D.", path)

    def check_fields(self, zero: Path) -> None:
        files = [p for p in zero.iterdir() if p.is_file() and not p.name.startswith(".")]
        self.metadata["initial_field_count"] = len(files)
        self.metadata["initial_fields"] = sorted(p.name for p in files)
        if not files:
            self.add("critical", "FIELDS_MISSING", "The 0 directory has no initial field files.", zero)
            return
        for path in files:
            text = strip_comments(read_text(path))
            if scalar(text, "dimensions") is None:
                self.add("critical", "DIMENSIONS_MISSING", f"Field `{path.name}` has no dimensions entry.", path)
            if scalar(text, "internalField") is None:
                self.add("critical", "INTERNAL_FIELD", f"Field `{path.name}` has no internalField entry.", path)
            boundary_block = next((body for name, body in named_blocks(text) if name == "boundaryField"), "")
            if not boundary_block:
                self.add("critical", "BOUNDARY_FIELD", f"Field `{path.name}` has no boundaryField block.", path)
                continue
            defined = {name for name, _ in named_blocks(boundary_block)}
            missing = sorted(set(self.patches) - defined)
            if missing:
                self.add("critical", "PATCH_COVERAGE", f"Field `{path.name}` omits mesh patches: {', '.join(missing)}", path)

    def check_properties(self) -> None:
        options = ("constant/transportProperties", "constant/physicalProperties", "constant/thermophysicalProperties")
        found = [rel for rel in options if (self.case / rel).is_file()]
        if not found:
            self.add("warning", "PROPERTIES_UNSEEN", "No common physical-properties dictionary was found; verify custom or region-specific configuration.")
        else:
            self.metadata["property_files"] = found

    def check_includes(self) -> None:
        dictionaries = [p for p in self.case.rglob("*") if p.is_file() and len(p.parts) - len(self.case.parts) <= 3]
        includes = []
        for path in dictionaries:
            try:
                if re.search(r"(?m)^\s*#include", read_text(path)):
                    includes.append(str(path.relative_to(self.case)))
            except OSError:
                continue
        if includes:
            self.metadata["files_with_includes"] = sorted(includes)
            self.add("info", "INCLUDES", "Included dictionaries require manual portability review.", evidence=", ".join(includes))


def render_html(report: dict[str, object]) -> str:
    findings = report["findings"]
    rows = "".join(
        "<tr>"
        f"<td><span class='pill {html.escape(f['severity'])}'>{html.escape(f['severity'].upper())}</span></td>"
        f"<td>{html.escape(f['code'])}</td><td>{html.escape(f['message'])}</td>"
        f"<td>{html.escape(f['file'])}</td><td>{html.escape(f['evidence'])}</td></tr>"
        for f in findings
    ) or "<tr><td colspan='5'>No static findings.</td></tr>"
    meta = html.escape(json.dumps(report["metadata"], indent=2))
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>OpenFOAM Case QA</title>
<style>body{{font:15px system-ui;margin:40px;color:#172033}}h1{{margin-bottom:4px}}.score{{font-size:42px;font-weight:750}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d9dfeb;padding:9px;text-align:left;vertical-align:top}}th{{background:#eef3fa}}.pill{{font-size:11px;font-weight:700;padding:3px 7px;border-radius:12px}}.critical{{background:#ffe0e0;color:#9b111e}}.warning{{background:#fff0c7;color:#744d00}}.info{{background:#ddecff;color:#174d8c}}pre{{background:#f5f7fa;padding:16px;overflow:auto}}small{{color:#59657a}}</style>
</head><body><h1>OpenFOAM Case QA Lite</h1><small>{html.escape(report['case'])}</small>
<p><span class='score'>{report['static_readiness_score']}/100</span> &nbsp; Status: <strong>{report['status']}</strong></p>
<h2>Findings</h2><table><thead><tr><th>Level</th><th>Code</th><th>Finding</th><th>File</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Case metadata</h2><pre>{meta}</pre><h2>Limits</h2><ul>{''.join(f'<li>{html.escape(x)}</li>' for x in report['limitations'])}</ul></body></html>"""


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run static QA checks on an OpenFOAM case folder.")
    parser.add_argument("case", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, help="Write a machine-readable report.")
    parser.add_argument("--html", dest="html_path", type=Path, help="Write a standalone HTML report.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero on warnings as well as critical findings.")
    args = parser.parse_args(argv)
    report = Audit(args.case).run()
    payload = json.dumps(report, indent=2)
    if args.json_path:
        args.json_path.write_text(payload + "\n", encoding="utf-8")
    if args.html_path:
        args.html_path.write_text(render_html(report), encoding="utf-8")
    print(payload)
    critical = report["counts"]["critical"]
    warnings = report["counts"]["warning"]
    return 2 if critical or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
