#!/usr/bin/env python3

"""
Transform packager-annotated JSON samples from logdetective.com into
the sample_metadata.yaml + log file directory structure expected by
the evaluation harness (validation.py).

Usage: python create_samples.py <directory>

Reads all *.json files from <directory>, creates resulting sample directories in <output_dir>.
"""

import json
import os
import pathlib
import re
import sys
import uuid

import requests
import yaml


# Characters that prevent PyYAML from using block scalar (|) style
ESCAPE_SEQ_REPLACEMENTS = {
    "\t": "    ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
}

LOGDETECTIVE_URL = "https://server-logdetective.apps.cyborg.fio9.p1.openshiftapps.com"


class _CustomDumper(yaml.SafeDumper):
    """YAML dumper that always indents list items."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


def _str_presenter(dumper, data: str):
    """Use block scalar (|) for long strings, inline for short ones."""
    cleaned = []
    for line in data.split("\n"):
        line = line.rstrip()
        for old, new in ESCAPE_SEQ_REPLACEMENTS.items():
            line = line.replace(old, new)
        cleaned.append(line)
    data = "\n".join(cleaned)
    if len(data) > 50:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    data = data.replace("\n", " ").strip()
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _str_presenter, Dumper=_CustomDumper)


def get_project_version(json_dict: dict) -> str:
    """Extract package version from Copr or Koji log files."""
    logs = json_dict["logs"]

    # Copr: version is in builder-live.log or backend.log metadata
    for logname in ("builder-live.log", "backend.log"):
        if logname in logs:
            match = re.search(
                r"'package_version': '(\w+)'",
                logs[logname]["content"],
            )
            if match:
                return match.group(1)

    # Koji: version is embedded in the SRPM filename, catching NVR as groups 1,2,3
    if "build.log" in logs:
        match = re.search(
            r"Wrote: /builddir/build/SRPMS/(.+)-([^-]+)-([^-]+)\.fc[0-9]+\.src\.rpm",
            logs["build.log"]["content"],
        )
        if match:
            return f"{match.group(2)}-{match.group(3)}"

    return ""


def build_notes(json_dict: dict) -> str:
    """Compose the notes field from resolution info and annotated snippets."""
    resolution = str(json_dict["how_to_fix"]).strip().replace("\n\n", "\n")

    parts = [
        "Sample comes from packager-annotated notes submitted to logdetective.com.",
        "Additional text contains more context.",
        "",
        f"Resolution: {resolution}",
        "",
        "Snippets:",
    ]

    for logname, logdata in json_dict["logs"].items():
        for snippet in logdata["snippets"]:
            text = str(snippet["text"]).strip()
            comment = str(snippet.get("user_comment", snippet.get("comment", "")))
            comment = comment.strip().replace("\n\n", "\n")
            parts.append("")
            parts.append(f"In {logname}:\n```\n{text}\n```")
            parts.append(f"=> {comment}")

    return "\n".join(parts) + "\n"


def get_logdetective_analysis(json_dict: dict) -> str:
    payload = {
        "files": [
            {
                "name": logname,
                "content": logdata["content"]
            } for logname, logdata in json_dict["logs"].items()
        ]
    }

    api_response = requests.post(
        f"{LOGDETECTIVE_URL}/analyze",
        json=payload,
        timeout=60,
    )
    api_response.raise_for_status()
    actual_response_data = api_response.json()
    logdetective_explanation = str(actual_response_data["explanation"]["text"])
    return logdetective_explanation.rstrip()


def transform_sample(
    json_path: pathlib.Path,
    output_dir: pathlib.Path,
    log_detective_version: str,
) -> None:
    """Transform a single JSON sample into the eval directory structure."""
    with open(json_path, "r", encoding="utf-8") as f:
        json_dict = json.load(f)

    try:
        package = json_dict["spec_file"]["name"].removesuffix(".spec")
    except TypeError:
        package = json_path.name.removesuffix(".json")
    sample_uuid = str(uuid.uuid4())

    print(f"Preparing sample {sample_uuid} from {package} ...\n")

    analysis_log_line = f"Log Detective (v{log_detective_version}) Analysis: "
    print(analysis_log_line + "-" * (80 - len(analysis_log_line)))
    try:
        analysis = get_logdetective_analysis(json_dict)
    except (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.HTTPError,
    ) as e:
        print(f" => {json_path} skipping, server-related exception: {e}")
        print("-" * 80, "\n")
        return

    print(analysis)
    print("-" * 80, "\n")

    os.makedirs(output_dir / sample_uuid, exist_ok=True)

    # Write individual log files
    for logname, logdata in json_dict["logs"].items():
        with open(output_dir / sample_uuid / logname, "w", encoding="utf-8") as f:
            f.write(logdata["content"])

    issue = str(json_dict["fail_reason"]).rstrip().replace("\n\n", "\n")

    metadata = {
        "source_project_name": package,
        "source_project_version": get_project_version(json_dict),
        "issue": issue + "\n",
        "log_detective_version": log_detective_version,
        "log_detective_analysis": analysis + "\n",
        "log_files": list(json_dict["logs"].keys()),
        "notes": build_notes(json_dict),
    }

    with open(output_dir / sample_uuid / "sample_metadata.yaml", "w", encoding="utf-8") as f:
        yaml.dump(
            metadata,
            f,
            Dumper=_CustomDumper,
            default_flow_style=False,
            allow_unicode=True,
            indent=4,
            sort_keys=False,
        )


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <directory> <output_dir>", file=sys.stderr)
        sys.exit(1)

    directory = pathlib.Path(sys.argv[1])
    output_dir = pathlib.Path(sys.argv[2])
    os.makedirs(output_dir, exist_ok=True)

    print("Getting Log Detective version ...")
    api_response = requests.get(
        f"{LOGDETECTIVE_URL}/version",
        timeout=60,
        headers={},
    )
    api_response.raise_for_status()
    version = api_response.text.strip()

    for json_path in directory.glob("*.json"):
        try:
            transform_sample(json_path, output_dir, version)
        except (TypeError, KeyError, ValueError) as exc:
            print(f" => {json_path} skipping, encountered an error: {exc}")


if __name__ == "__main__":
    main()
