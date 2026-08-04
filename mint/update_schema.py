import frappe

def run():
    frappe.init(site="api")
    frappe.connect()

    doctype = frappe.get_doc("DocType", "Mint Bank Description Rule")
    fieldnames = [f.fieldname for f in doctype.fields]

    if "match_type" not in fieldnames:
        doctype.append("fields", {
            "fieldname": "match_type",
            "fieldtype": "Select",
            "label": "Tipo de Coincidencia",
            "options": "Exact Match\nStarts With",
            "default": "Exact Match",
            "in_list_view": 1,
            "description": "Indica si la descripción debe coincidir de forma exacta o si aplica a cualquier descripción que comience por este texto.",
            "insert_after": "description_text"
        })

    if "apply_prefix_rule" not in fieldnames:
        doctype.append("fields", {
            "fieldname": "apply_prefix_rule",
            "fieldtype": "Check",
            "label": "Activar Validación de Prefijos",
            "default": "0",
            "description": "Si está activo, bloquea duplicados si la referencia base (sin el prefijo indicado) ya existe. Si está apagado, funciona como lista blanca y permite el duplicado exacto.",
            "insert_after": "match_type"
        })

    if "prefixes_to_strip" not in fieldnames:
        doctype.append("fields", {
            "fieldname": "prefixes_to_strip",
            "fieldtype": "Data",
            "label": "Prefijos a Eliminar",
            "depends_on": "eval:doc.apply_prefix_rule",
            "description": "Separados por comas (ej. 94, 094). El sistema intentará quitar estos prefijos para buscar duplicados base.",
            "insert_after": "apply_prefix_rule"
        })

    doctype.save()
    frappe.db.commit()
    print("Esquema de Mint Bank Description Rule actualizado exitosamente en la base de datos.")

run()
