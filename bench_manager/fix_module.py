import frappe

def run():
    if not frappe.db.exists("Module Def", "Bench Manager"):
        frappe.get_doc({
            "doctype": "Module Def",
            "module_name": "Bench Manager",
            "app_name": "bench_manager",
            "custom": 0
        }).insert(ignore_permissions=True)
        print("Created Module Def")

    # Ensure page exists - usually bench migrate handles this,
    # but we will just skip manual page import here to avoid KeyError.
    print("Skipped Manual Page Sync")

    if not frappe.db.exists("Workspace", "Bench Manager"):
        doc = frappe.get_doc({
            "doctype": "Workspace",
            "label": "Bench Manager",
            "title": "Bench Manager",
            "module": "Bench Manager",
            "is_standard": 1,
            "public": 1,
            "for_user": "",
            "links": [{"type": "Link", "link_type": "Page", "label": "Bench Dashboard", "link_to": "bench-dashboard"}]
        })
        doc.flags.ignore_links = True
        doc.insert(ignore_permissions=True)
        print("Created Workspace")

    frappe.db.commit()
    print("Done")
