"""Bench Command Log DocType controller."""

# Copyright (c) 2026, Praveen and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class BenchCommandLog(Document):
    """Controller for Bench Command Log DocType.

    Stores audit logs for every bench command executed through the GUI.
    Records are created by the utils.log_command() function and are
    read-only after creation.
    """

    pass
