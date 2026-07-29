import frappe

def execute():
    """
    Parche para limpiar transacciones bancarias duplicadas automáticamente
    durante el proceso de migracion (bench migrate).
    """
    frappe.logger().info("Running patch: cleanup_duplicate_bank_transactions")
    
    try:
        from mint.apis.reconciliation import daily_remove_exact_duplicates
        daily_remove_exact_duplicates()
    except Exception as e:
        frappe.log_error("Error in patch cleanup_duplicate_bank_transactions", str(e))
        print(f"Error during duplicate cleanup: {e}")
