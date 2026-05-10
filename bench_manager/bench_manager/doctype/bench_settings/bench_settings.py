"""Bench Settings DocType controller."""

# Copyright (c) 2026, Praveen and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from bench_manager.utils import validate_bench_path


class BenchSettings(Document):
    """Controller for Bench Settings Single DocType.

    Stores global configuration for the Bench Manager app,
    including the bench directory path.
    """

    def validate(self):
        """Validate bench path if manually set."""
        if self.bench_path and not self.auto_detect_path:
            if not validate_bench_path(self.bench_path):
                frappe.throw(
                    "Invalid bench path. The directory must contain "
                    "'apps' and 'sites' subdirectories."
                )
