import frappe
import json
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import get_linked_payments as erpnext_get_linked_payments
from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import check_matching

try:
    import mint.mint.utils.matching
    original_get_matching_rules = mint.mint.utils.matching.get_matching_rules
except ImportError:
    original_get_matching_rules = None

def modify_venezuela_reference(doc, method=None):
    """
    Hook: before_insert en Bank Transaction
    """
    if getattr(doc, "reference_number", None):
        # Esto convierte el Ref N en texto plano y anula cualquier funcion de excel 
        doc.reference_number = str(doc.reference_number).strip()

def custom_get_matching_rules(bank_transaction_doc):
    """
    Parche (Monkey Patch): Filtra reglas de Mint por banco
    """
    if not original_get_matching_rules:
        return []

    all_rules = original_get_matching_rules(bank_transaction_doc)
    filtered = []
    current_bank = bank_transaction_doc.bank_account
    
    for rule in all_rules:
        rule_bank = frappe.db.get_value("Mint Bank Transaction Rule", rule.name, "bank_account")
        
        if not rule_bank or rule_bank == current_bank:
            filtered.append(rule)
            
    return filtered

# Aplicar parche
if original_get_matching_rules:
    mint.mint.utils.matching.get_matching_rules = custom_get_matching_rules 

def apply_format_rule(rule, ref):
    clean_ref = str(ref).strip()
    if rule in ["Duplicar si son 4 dígitos", "Duplicar referencia"]:
        return clean_ref + clean_ref
    elif rule == "Tomar los últimos 8 dígitos":
        return clean_ref[-8:]
    elif rule == "Agregar 6 ceros a la izquierda":
        return "000000" + clean_ref
    elif rule == "Agregar 3 ceros a la izquierda":
        return "000" + clean_ref
    elif rule == "Agregar 7 ceros a la izquierda":
        return "0000000" + clean_ref
    elif rule == "Agregar 1 cero a la izquierda":
        return "0" + clean_ref
    return clean_ref

@frappe.whitelist()
def get_linked_payments(
    bank_transaction_name,
    document_types=None,
    from_date=None,
    to_date=None,
    filter_by_reference_date=None,
    from_reference_date=None,
    to_reference_date=None,
):
    if isinstance(document_types, str):
        document_types = json.loads(document_types)
        
    # 1. Llamar a la logica original de ERPNext (búsqueda normal)
    matches = erpnext_get_linked_payments(
        bank_transaction_name,
        document_types,
        from_date,
        to_date,
        filter_by_reference_date,
        from_reference_date,
        to_reference_date
    )
    if not matches:
        matches = []
        
    transaction = frappe.get_doc("Bank Transaction", bank_transaction_name)
    original_ref = str(transaction.reference_number or "").strip()
    if not original_ref:
        return matches
        
    # Obtener bancos con reglas
    banks_with_rules = frappe.get_all("Bank", filters={"custom_reference_format_rule": ["!=", ""]}, fields=["name", "custom_reference_format_rule"])
    
    # 2. Etiquetar los que ya encontro Frappe
    for match in matches:
        pe_ref = match.get("reference_no") or match.reference_no
        if pe_ref and pe_ref != original_ref:
            for bank in banks_with_rules:
                mod_ref = apply_format_rule(bank.custom_reference_format_rule, original_ref)
                if mod_ref == pe_ref:
                    doctype = match.get("doctype") or match.doctype
                    name = match.get("name") or match.name
                    if doctype == "Payment Entry":
                        banco_origen = frappe.db.get_value("Payment Entry", name, "custom_banco_origen")
                        if banco_origen == bank.name:
                            match["matched_by_rule"] = True
                            break
    
    # 3. Buscar si hay extras (que Frappe omitió)
    for bank in banks_with_rules:
        # Aplicar la regla
        modified_ref = apply_format_rule(bank.custom_reference_format_rule, original_ref)
        if modified_ref == original_ref:
            continue
            
        # Reemplazar temporalmente el numero de referencia en la transaccion (en memoria)
        transaction.reference_number = modified_ref
        
        bank_account = frappe.db.get_values(
            "Bank Account", transaction.bank_account, ["account", "company"], as_dict=True
        )[0]
        
        # Buscar coincidencias con la referencia modificada ignorando las fechas
        new_matches = check_matching(
            bank_account.account,
            bank_account.company,
            transaction,
            document_types,
            None, # Ignorar from_date
            None, # Ignorar to_date
            filter_by_reference_date,
            from_reference_date,
            to_reference_date,
        )
        
        # Restaurar la referencia original
        transaction.reference_number = original_ref
        
        if new_matches:
            for match in new_matches:
                doctype = match.get("doctype") if isinstance(match, dict) else match.doctype
                name = match.get("name") if isinstance(match, dict) else match.name
                
                if doctype == "Payment Entry":
                    banco_origen = frappe.db.get_value("Payment Entry", name, "custom_banco_origen")
                    if banco_origen == bank.name:
                        # Avoid duplicates
                        if not any((m.get("name") or m.name) == name and (m.get("doctype") or m.doctype) == doctype for m in matches):
                            if not isinstance(match, dict):
                                match = match.as_dict()
                            match["matched_by_rule"] = True
                            matches.append(match)
                            
    # Filtrar resultados (Sugerir SOLO si hay coincidencia exacta o al menos 2 coincidencias: monto y fecha)
    filtered_matches = []
    for match in matches:
        m = frappe._dict(match) if isinstance(match, dict) else match
            
        is_exact = False
        if m.get("matched_by_rule"):
            is_exact = True
        elif str(m.get("reference_no") or "").strip() == str(transaction.reference_number or "").strip():
            is_exact = True
            
        if is_exact:
            filtered_matches.append(match)
            continue
            
        # Si no es exacta, debe tener coincidencia de monto y fecha
        match_amount = float(m.get("paid_amount") or m.get("amount") or m.get("allocated_amount") or 0.0)
        txn_amount = float(transaction.unallocated_amount)
        
        match_date = m.get("posting_date") or m.get("date") or m.get("reference_date")
        txn_date = transaction.date
        
        if match_amount == txn_amount and str(match_date) == str(txn_date):
            filtered_matches.append(match)
            
    return filtered_matches

# --- PARCHE PARA l10n_ve ---
# l10n_ve bloquea los Submit de Payment Entry si no encuentra el depósito exacto.
# Como alteramos la referencia del Payment Entry (ej. duplicando 1234 -> 12341234),
# l10n_ve no encuentra el depósito (que tiene 1234).
# Aquí le enseñamos a l10n_ve a buscar la referencia original revirtiendo la regla.

try:
    import l10n_ve.overrides.payment_reconciliation
    original_find_matching_deposit = l10n_ve.overrides.payment_reconciliation.find_matching_deposit
except ImportError:
    original_find_matching_deposit = None

if original_find_matching_deposit:
    def custom_find_matching_deposit(doc):
        # 1. Intentar la búsqueda original de l10n_ve
        deposit = original_find_matching_deposit(doc)
        if deposit:
            return deposit
            
        # 2. Si falla, verificar si hay regla de Banco Origen
        banco_origen = getattr(doc, "custom_banco_origen", None)
        if not banco_origen:
            return None
            
        rule = frappe.db.get_value("Bank", banco_origen, "custom_reference_format_rule")
        if not rule:
            return None
            
        ref = str(doc.reference_no or "").strip()
        original_ref = None
        
        # 3. Intentar revertir la regla para hallar la referencia original del banco
        if rule in ["Duplicar si son 4 dígitos", "Duplicar referencia"]:
            half = len(ref) // 2
            if len(ref) % 2 == 0 and ref[:half] == ref[half:]:
                original_ref = ref[:half]
        elif rule == "Agregar 6 ceros a la izquierda" and ref.startswith("000000"):
            original_ref = ref[6:]
        elif rule == "Agregar 3 ceros a la izquierda" and ref.startswith("000"):
            original_ref = ref[3:]
        elif rule == "Agregar 7 ceros a la izquierda" and ref.startswith("0000000"):
            original_ref = ref[7:]
        elif rule == "Agregar 1 cero a la izquierda" and ref.startswith("0"):
            original_ref = ref[1:]
        elif rule == "Tomar los últimos 8 dígitos":
            original_ref = "%" + ref  # Uso de LIKE en SQL
            
        if original_ref:
            # Construir filtros igual que hace l10n_ve pero con la referencia original
            filters = {
                "docstatus": 1,
                "deposit": [">", 0],
                "unallocated_amount": [">", 0.001],
            }
            if "%" in original_ref:
                filters["reference_number"] = ["like", original_ref]
            else:
                filters["reference_number"] = original_ref
                
            paid_currency = (doc.paid_on_currency or "").strip()
            if paid_currency:
                filters["currency"] = paid_currency
                
            bank_account = l10n_ve.overrides.payment_reconciliation._cobro_bank_account(doc)
            if bank_account:
                filters["bank_account"] = bank_account
                
            from l10n_ve.overrides.payment_reconciliation import _first_deposit
            return _first_deposit(filters)
            
        return None

    # Aplicar el parche
    l10n_ve.overrides.payment_reconciliation.find_matching_deposit = custom_find_matching_deposit

def reconcile_drafts_with_rules_for_deposit(doc, method=None) -> None:
    """Disparador desde el depósito: intenta conciliar cobros en borrador usando reglas de Banco Origen."""
    ref = str(doc.reference_number or "").strip()
    if not ref or frappe.utils.flt(doc.deposit) <= 0:
        return

    frappe.enqueue(
        "mint.apis.reconciliation.reconcile_drafts_with_rules_job",
        queue="short",
        bank_transaction_name=doc.name,
        reference=ref,
        enqueue_after_commit=True,
    )

def reconcile_drafts_with_rules_job(bank_transaction_name: str, reference: str) -> None:
    """Aprueba los cobros en borrador aplicando las reglas 'hacia adelante' de Banco Origen."""
    # 1. Obtener todos los bancos con reglas
    banks_with_rules = frappe.get_all("Bank", filters={"custom_reference_format_rule": ["!=", ""]}, fields=["name", "custom_reference_format_rule"])
    
    drafts_to_approve = set()
    
    for bank in banks_with_rules:
        # 2. Aplicar la regla a la referencia original del banco (ej. 1234 -> 12341234)
        modified_ref = apply_format_rule(bank.custom_reference_format_rule, reference)
        if modified_ref == reference:
            continue
            
        # 3. Buscar borradores
        drafts = frappe.get_all(
            "Payment Entry",
            filters={
                "docstatus": 0, 
                "payment_type": "Receive", 
                "reference_no": modified_ref,
                "custom_banco_origen": bank.name
            },
            pluck="name",
        )
        for name in drafts:
            drafts_to_approve.add(name)
            
    # 4. Enviar y conciliar
    if not drafts_to_approve:
        return
        
    try:
        from l10n_ve.overrides.payment_reconciliation import reconcile_and_approve
        for name in drafts_to_approve:
            try:
                reconcile_and_approve(name)
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(
                    title=f"Error conciliando cobro {name} (auto Mint)",
                    message=frappe.get_traceback(),
                )
    except ImportError:
        pass


def update_referencia_origen_on_reconcile(doc, method=None) -> None:
    """Si la transaccion bancaria se concilia, calcula la referencia origen y la guarda."""
    if doc.status != "Reconciled" or doc.custom_referencia_origen:
        return

    # Validar si tiene entradas vinculadas
    for row in doc.payment_entries:
        if row.payment_document == "Payment Entry" and row.payment_entry:
            pe_ref = frappe.db.get_value("Payment Entry", row.payment_entry, "reference_no")
            if not pe_ref:
                continue
                
            original_ref = str(doc.reference_number or "").strip()
            if not original_ref:
                continue
                
            # Si coinciden exactamente, no hace falta guardar "origen"
            if pe_ref == original_ref:
                continue

            banks_with_rules = frappe.get_all("Bank", filters={"custom_reference_format_rule": ["!=", ""]}, fields=["name", "custom_reference_format_rule"])
            for bank in banks_with_rules:
                mod_ref = apply_format_rule(bank.custom_reference_format_rule, original_ref)
                if mod_ref == pe_ref:
                    # Update with modified=True to trigger Frappe UI reload
                    doc.db_set("custom_referencia_origen", mod_ref, update_modified=True)
                    return