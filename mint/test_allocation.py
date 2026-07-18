import frappe

def test_auto_allocation():
    frappe.set_user("Administrator")
    
    # 1. Crear un cliente de prueba
    customer_name = "Cliente Prueba Auto Asignacion"
    if not frappe.db.exists("Customer", customer_name):
        customer = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_group": "All Customer Groups",
            "teritory": "All Territories"
        }).insert(ignore_permissions=True)
    else:
        customer = frappe.get_doc("Customer", customer_name)
        
    print(f"Usando Cliente: {customer.name}")
    
    # 2. Crear Pagos (Anticipos) sin conciliar
    # Pago 1: $10
    pe1 = frappe.get_doc({
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "party_type": "Customer",
        "party": customer.name,
        "paid_amount": 10,
        "received_amount": 10,
        "paid_to": frappe.db.get_value("Account", {"account_type": "Cash", "is_group": 0}),
        "paid_from": frappe.db.get_value("Account", {"account_type": "Receivable", "is_group": 0, "company": frappe.defaults.get_user_default("Company")}),
        "reference_no": "TEST-AUTO-01",
        "reference_date": frappe.utils.today()
    }).insert(ignore_permissions=True)
    pe1.submit()
    print(f"Pago 1 creado y validado: {pe1.name} por $10")
    
    # Pago 2: $5
    pe2 = frappe.get_doc({
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "party_type": "Customer",
        "party": customer.name,
        "paid_amount": 5,
        "received_amount": 5,
        "paid_to": pe1.paid_to,
        "paid_from": pe1.paid_from,
        "reference_no": "TEST-AUTO-02",
        "reference_date": frappe.utils.today()
    }).insert(ignore_permissions=True)
    pe2.submit()
    print(f"Pago 2 creado y validado: {pe2.name} por $5")
    
    # 3. Crear Factura de Venta por $20
    item_code = frappe.db.get_value("Item", {"is_sales_item": 1})
    if not item_code:
        print("No hay items para facturar en la BD.")
        return
        
    si = frappe.get_doc({
        "doctype": "Sales Invoice",
        "customer": customer.name,
        "items": [{
            "item_code": item_code,
            "qty": 1,
            "rate": 20
        }]
    }).insert(ignore_permissions=True)
    
    print(f"Factura de Venta creada (Borrador): {si.name} por ${si.grand_total}")
    
    # Emitir Factura (aqui ocurre la magia)
    si.submit()
    
    si.reload()
    
    print(f"Factura Emitida! Saldo original: ${si.grand_total} | Saldo Pendiente actual: ${si.outstanding_amount}")
    if si.outstanding_amount == (si.grand_total - 15):
        print("¡EXITO! El sistema restó los $15 de los pagos automáticamente.")
    else:
        print("ALGO FALLÓ: El monto pendiente no cuadra con la resta de los anticipos.")
