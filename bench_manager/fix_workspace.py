import frappe
import json

def fix():
    ws = frappe.get_doc("Workspace", "Bench Manager")
    
    # Add a shortcut
    ws.shortcuts = []
    ws.append("shortcuts", {
        "label": "Bench Dashboard",
        "type": "Page",
        "link_to": "bench-dashboard",
        "color": "Grey"
    })
    
    # Set the content block to use the shortcut
    content = [
        {
            "id": "header1",
            "type": "header",
            "data": {
                "text": "Bench Operations",
                "level": 4,
                "col": 12
            }
        },
        {
            "id": "shortcut1",
            "type": "shortcut",
            "data": {
                "shortcut_name": "Bench Dashboard",
                "col": 4
            }
        }
    ]
    ws.content = json.dumps(content)
    
    ws.flags.ignore_links = True
    ws.save(ignore_permissions=True)
    frappe.db.commit()
    print("Fixed Workspace")
