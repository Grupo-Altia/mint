import frappe
from frappe.utils import flt

def execute():
    """
    Parche para barrer transacciones bancarias importadas que empiezan en 94 o 094 
    y que tengan una contraparte base idéntica ya registrada, resolviendo colisiones.
    """
    frappe.logger().info("Running patch: cleanup_94_094_duplicates")
    
    try:
        total_processed = 0
        for doctype_type in ["withdrawal", "deposit"]:
            sql = f"""
                SELECT 
                    t1.name as duplicate_name, TRIM(t1.reference_number) as duplicate_ref, 
                    t1.allocated_amount as duplicate_allocated, t1.docstatus as duplicate_docstatus,
                    t2.name as base_name, TRIM(t2.reference_number) as base_ref, 
                    t2.allocated_amount as base_allocated, t2.docstatus as base_docstatus
                FROM `tabBank Transaction` t1
                INNER JOIN `tabBank Transaction` t2 
                    ON (TRIM(t1.reference_number) = CONCAT('94', TRIM(t2.reference_number)) 
                        OR TRIM(t1.reference_number) = CONCAT('094', TRIM(t2.reference_number)))
                    AND t1.bank_account = t2.bank_account
                    AND t1.company = t2.company
                    AND ROUND(t1.{doctype_type}, 2) = ROUND(t2.{doctype_type}, 2)
                    AND t1.date = t2.date
                WHERE t1.{doctype_type} > 0 
                  AND t1.docstatus < 2 
                  AND t2.docstatus < 2
            """
            duplicates = frappe.db.sql(sql, as_dict=True)
            
            processed = 0
            for d in duplicates:
                target_to_delete = None
                
                # Si ambas están asignadas, no tocamos nada para evitar romper vínculos
                if flt(d.duplicate_allocated) >= 0.01 and flt(d.base_allocated) >= 0.01:
                    continue
                
                # Preferimos borrar la que tiene 94/094 si no está asignada
                if flt(d.duplicate_allocated) == 0:
                    target_to_delete = d.duplicate_name
                # Si la 94/094 ya se asignó pero la base NO, borramos la base
                elif flt(d.base_allocated) == 0:
                    target_to_delete = d.base_name
                
                if target_to_delete:
                    try:
                        frappe.db.savepoint("dup_row_94")
                        doc = frappe.get_doc("Bank Transaction", target_to_delete)
                        if doc.docstatus == 1:
                            doc.flags.ignore_permissions = True
                            doc.cancel()
                        frappe.delete_doc("Bank Transaction", target_to_delete, force=1, ignore_permissions=True)
                        frappe.db.commit()
                        processed += 1
                    except Exception as e:
                        frappe.db.rollback(save_point="dup_row_94")
                        frappe.log_error(f"Error borrando duplicado 94/094 {target_to_delete}", str(e))
                        
            print(f"Borrados {processed} duplicados de prefijos 94/094 para {doctype_type}")
            total_processed += processed
            
        print(f"Total de duplicados 94/094 limpiados: {total_processed}")
    except Exception as e:
        frappe.log_error("Error in patch cleanup_94_094_duplicates", str(e))
        print(f"Error durante limpieza 94/094: {e}")
