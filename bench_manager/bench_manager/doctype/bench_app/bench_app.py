"""Bench App DocType controller."""

# Copyright (c) 2026, Praveen and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BenchApp(Document):
    """Controller for Bench App DocType.

    Represents a Frappe app installed in the bench with git metadata.
    """

    def validate(self):
        """Validate app name format."""
        if self.app_name and not self.app_name.replace("_", "").replace("-", "").isalnum():
            frappe.throw("App name can only contain alphanumeric characters, hyphens, and underscores.")
