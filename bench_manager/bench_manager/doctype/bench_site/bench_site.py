"""Bench Site DocType controller."""

# Copyright (c) 2026, Praveen and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class BenchSite(Document):
    """Controller for Bench Site DocType.

    Represents a Frappe site in the bench with its status and metadata.
    """

    def validate(self):
        """Validate site name format before saving."""
        if self.site_name and ".." in self.site_name:
            frappe.throw("Site name cannot contain '..'")

    def before_save(self):
        """Auto-populate fields before saving."""
        if not self.creation_date:
            self.creation_date = frappe.utils.today()
