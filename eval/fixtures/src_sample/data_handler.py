"""Data handling and transformation utilities."""

import csv
import json
from functools import wraps
import warnings


def deprecated(func):
    """Decorator to mark functions as deprecated."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        warnings.warn(
            f"{func.__name__} is deprecated.",
            DeprecationWarning,
            stacklevel=2,
        )
        return func(*args, **kwargs)
    return wrapper


@deprecated
def load_csv_as_dicts(filepath: str) -> list[dict]:
    """Load a CSV file and return list of dictionaries.

    Deprecated: Use load_data() with format='csv' instead.
    """
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def load_data(filepath: str, format: str = "auto") -> list[dict]:
    """Load data from various file formats."""
    if format == "auto":
        format = filepath.rsplit(".", 1)[-1]
    if format == "csv":
        with open(filepath, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    elif format == "json":
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported format: {format}")


@deprecated
def export_to_xml(data: list[dict], filepath: str) -> None:
    """Export data to XML format.

    Deprecated: Use export_data() with format='json' instead. XML support is being removed.
    """
    with open(filepath, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<records>\n')
        for record in data:
            f.write("  <record>\n")
            for key, value in record.items():
                f.write(f"    <{key}>{value}</{key}>\n")
            f.write("  </record>\n")
        f.write("</records>\n")


def export_data(data: list[dict], filepath: str, format: str = "json") -> None:
    """Export data to various file formats."""
    if format == "json":
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    elif format == "csv":
        if not data:
            return
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
    else:
        raise ValueError(f"Unsupported format: {format}")
