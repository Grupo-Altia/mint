# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import flt

def execute(filters=None):
    columns = get_columns()
    data, report_summary = get_data_and_summary(filters)
    
    return columns, data, None, None, report_summary

def get_columns():
    """Define las columnas del reporte de detalle"""
    return [
        {
            'fieldname': 'date',
            'label': _('Fecha'),
            'fieldtype': 'Date',
            'width': 110
        },
        {
            'fieldname': 'description',
            'label': _('Descripción'),
            'fieldtype': 'Data',
            'width': 250
        },
        {
            'fieldname': 'bank_account',
            'label': _('Cuenta Bancaria'),
            'fieldtype': 'Link',
            'options': 'Bank Account',
            'width': 120
        },
        {
            'fieldname': 'reference',
            'label': _('Referencia'),
            'fieldtype': 'Data',
            'width': 120
        },
        {
            'fieldname': 'transaction_type',
            'label': _('Tipo'),
            'fieldtype': 'Data',
            'width': 100
        },
        {
            'fieldname': 'deposit',
            'label': _('Depósito'),
            'fieldtype': 'Currency',
            'options': 'currency',
            'width': 120
        },
        {
            'fieldname': 'withdrawal',
            'label': _('Retiro'),
            'fieldtype': 'Currency',
            'options': 'currency',
            'width': 120
        },
        {
            'fieldname': 'currency',
            'label': _('Moneda'),
            'fieldtype': 'Link',
            'options': 'Currency',
            'width': 80
        },
        {
            'fieldname': 'status',
            'label': _('Estado'),
            'fieldtype': 'Data',
            'width': 120
        },
        {
            'fieldname': 'classification',
            'label': _('Clasificación'),
            'fieldtype': 'Data',
            'width': 180
        },
        {
            'fieldname': 'payment_document_display',
            'label': _('Documento Vinculado'),
            'fieldtype': 'Data',
            'width': 140
        },
        {
            'fieldname': 'payment_document',
            'label': _('DocType Real'),
            'fieldtype': 'Data',
            'hidden': 1
        },
        {
            'fieldname': 'payment_entry',
            'label': _('N° Documento'),
            'fieldtype': 'Dynamic Link',
            'options': 'payment_document',
            'width': 140
        },
        {
            'fieldname': 'party',
            'label': _('Parte'),
            'fieldtype': 'Data',
            'width': 150
        },
        {
            'fieldname': 'clearance_date',
            'label': _('Fecha Liquidación'),
            'fieldtype': 'Date',
            'width': 120
        }
    ]

def get_data_and_summary(filters):
    """Obtiene los datos del reporte y calcula el resumen"""
    # Validar filtros
    if not filters.get('account') and not filters.get('branch'):
        frappe.throw(_('La Cuenta Bancaria o la Sucursal es obligatoria'))
        
    accounts = []
    if filters.get('account'):
        accounts.append(filters.get('account'))
    else:
        accounts = frappe.db.get_all('Bank Account', filters={'branch_code': filters.get('branch'), 'company': filters.get('company')}, pluck='name')
        
    if not accounts:
        frappe.throw(_('No se encontraron cuentas bancarias asociadas a la sucursal seleccionada.'))
        
    filters['accounts'] = tuple(accounts)
    
    # 1. Obtener transacciones bancarias
    # Siempre obtenemos todas las transacciones del periodo para calcular bien el resumen
    filters_all = filters.copy()
    filters_all['status'] = 'All'
    bank_transactions = get_bank_transactions(filters_all)
    
    # 2. Obtener vouchers con clearance_date (para cotejo)
    vouchers = get_vouchers_without_clearance(filters)
    
    # 3. Precargar Bank Transaction Payments y doc data (para evitar N+1)
    payments_dict = {}
    doc_data_cache = {}
    bt_names = [bt.name for bt in bank_transactions]
    
    if bt_names:
        bt_payments = []
        chunk_size = 500
        for i in range(0, len(bt_names), chunk_size):
            chunk = bt_names[i:i + chunk_size]
            bt_payments.extend(frappe.db.sql("""
                SELECT 
                    parent,
                    payment_document,
                    payment_entry,
                    allocated_amount,
                    clearance_date
                FROM `tabBank Transaction Payments`
                WHERE parent IN %s
                ORDER BY parent, idx ASC
            """, (tuple(chunk),), as_dict=True))
        
        doc_names_by_type = {}
        for p in bt_payments:
            payments_dict.setdefault(p.parent, []).append(p)
            if p.payment_document and p.payment_entry:
                doc_names_by_type.setdefault(p.payment_document, []).append(p.payment_entry)
                
        for doctype, names in doc_names_by_type.items():
            if not names: continue
            meta = frappe.get_meta(doctype)
            for name in set(names):
                doc_dict = frappe._dict({'name': name})
                if meta.has_field('party'):
                    doc_dict.party = frappe.get_cached_value(doctype, name, 'party')
                if meta.has_field('clearance_date'):
                    doc_dict.clearance_date = frappe.get_cached_value(doctype, name, 'clearance_date')
                if meta.has_field('custom_reconciliation_status'):
                    doc_dict.custom_reconciliation_status = frappe.get_cached_value(doctype, name, 'custom_reconciliation_status')
                
                doc_data_cache[f"{doctype}-{name}"] = doc_dict

    # 4. Procesar y clasificar cada transacción
    full_data = []
    for bt in bank_transactions:
        row = process_bank_transaction(bt, filters, payments_dict, doc_data_cache)
        full_data.append(row)
    
    # 5. Agregar movimientos "solo en libros" al final
    for voucher in vouchers:
        row = {
            'date': voucher.get('posting_date'),
            'description': voucher.get('name'),
            'bank_account': voucher.get('bank_account'),
            'reference': '',
            'transaction_type': voucher.get('doctype'),
            'deposit': voucher.get('paid_amount') if voucher.get('payment_type') == 'Receive' else 0,
            'withdrawal': voucher.get('paid_amount') if voucher.get('payment_type') == 'Pay' else 0,
            'currency': voucher.get('currency'),
            'status': 'Solo en Libros',
            'classification': 'Pendiente en Libros',
            'payment_document': voucher.get('doctype'),
            'payment_document_display': _(voucher.get('doctype')),
            'payment_entry': voucher.get('name'),
            'party': voucher.get('party'),
            'clearance_date': ''
        }
        full_data.append(row)
    
    # 6. Calcular el resumen con TODA la información
    report_summary = get_report_summary(full_data, filters)
    
    # 7. Filtrar para la vista según el status
    data = []
    status_filter = filters.get('status', 'Unreconciled')
    for row in full_data:
        is_reconciled = row.get('status') in ['Reconciled', 'Conciliado']
        if status_filter == 'Unreconciled' and is_reconciled:
            continue
        if status_filter == 'Reconciled' and not is_reconciled:
            continue
        data.append(row)
    
    return data, report_summary

def get_bank_transactions(filters):
    """Obtiene Bank Transactions del período filtrado"""
    conditions = [
        "bt.bank_account IN %(accounts)s",
        "bt.date BETWEEN %(from_date)s AND %(to_date)s"
    ]
    
    status_filter = filters.get('status', 'Unreconciled')
    if status_filter == 'Unreconciled':
        conditions.append("bt.status != 'Reconciled'")
    elif status_filter == 'Reconciled':
        conditions.append("bt.status = 'Reconciled'")
    
    where_clause = " AND ".join(conditions)
    
    query = """
        SELECT 
            bt.name,
            bt.date,
            bt.description,
            bt.reference_number as reference,
            bt.transaction_type,
            bt.deposit,
            bt.withdrawal,
            bt.currency,
            bt.status,
            bt.party,
            bt.party_type,
            bt.bank_account,
            bt.allocated_amount,
            bt.unallocated_amount
        FROM `tabBank Transaction` bt
        WHERE {where_clause}
        ORDER BY bt.date ASC
    """.format(where_clause=where_clause)
    
    return frappe.db.sql(query, filters, as_dict=True)

def get_vouchers_without_clearance(filters):
    """Obtiene vouchers (Payment Entry, Journal Entry) sin fecha de liquidación"""
    branch_condition_pe = ""
    if filters.get('branch'):
        branch_condition_pe = " AND pe.branch = %(branch)s"

    # Payment Entries sin clearance_date
    pe_query = f"""
        SELECT 
            pe.name,
            pe.posting_date,
            pe.payment_type,
            pe.paid_amount,
            IF(pe.payment_type='Receive', pe.paid_to_account_currency, pe.paid_from_account_currency) as currency,
            pe.party,
            pe.party_type,
            pe.bank_account,
            'Payment Entry' as doctype
        FROM `tabPayment Entry` pe
        WHERE pe.bank_account IN %(accounts)s
            AND pe.docstatus = 1
            AND (pe.clearance_date IS NULL OR pe.clearance_date = '')
            AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND pe.paid_amount > 0
            {branch_condition_pe}
    """
    
    # Journal Entries sin clearance_date (con cuenta bancaria)
    je_query = """
        SELECT 
            je.name,
            je.posting_date,
            'Pay' as payment_type,
            je.total_amount as paid_amount,
            je.total_amount_currency as currency,
            '' as party,
            '' as party_type,
            jea.bank_account,
            'Journal Entry' as doctype
        FROM `tabJournal Entry` je
        INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.docstatus = 1
            AND jea.bank_account IN %(accounts)s
            AND (je.clearance_date IS NULL OR je.clearance_date = '')
            AND je.posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND je.total_amount > 0
        GROUP BY je.name
    """
    
    pe_results = frappe.db.sql(pe_query, filters, as_dict=True)
    je_results = frappe.db.sql(je_query, filters, as_dict=True)
    
    # Excluir vouchers que ya están vinculados a Bank Transactions
    # (implementar lógica de exclusión según necesidad)
    
    return pe_results + je_results

def process_bank_transaction(bt, filters, payments_dict=None, doc_data_cache=None):
    """Procesa una Bank Transaction y determina su clasificación"""
    payments_dict = payments_dict or {}
    doc_data_cache = doc_data_cache or {}
    
    # Traducir estado a español
    estado = bt.status
    estado_map = {
        'Unreconciled': 'No Conciliado',
        'Reconciled': 'Conciliado',
        'Pending': 'Pendiente',
        'Settled': 'Liquidado',
        'Cancelled': 'Cancelado'
    }
    estado_es = estado_map.get(estado, estado)

    # Determinar tipo si está vacío
    tipo = bt.transaction_type
    if not tipo:
        tipo = 'Ingreso' if bt.deposit > 0 else 'Egreso'

    row = {
        'date': bt.date,
        'description': bt.description,
        'bank_account': bt.bank_account,
        'reference': bt.reference,
        'transaction_type': tipo,
        'deposit': bt.deposit,
        'withdrawal': bt.withdrawal,
        'currency': bt.currency,
        'status': estado_es,
        'party': bt.party,
        'payment_document': '',
        'payment_document_display': '',
        'payment_entry': '',
        'clearance_date': '',
        'classification': ''
    }
    
    # Obtener Payment Entries vinculados (usando prefetch si está disponible)
    linked_payments = payments_dict.get(bt.name)
    
    if linked_payments:
        # Tomar el primer pago vinculado
        first = linked_payments[0]
        row['payment_document'] = first.payment_document
        row['payment_document_display'] = _(first.payment_document)
        row['payment_entry'] = first.payment_entry
        row['clearance_date'] = first.clearance_date
        
        # Si la transacción no tiene Parte o fecha, intentar sacarla del documento vinculado
        is_mint_reconciled = False
        doc_data = doc_data_cache.get(f"{first.payment_document}-{first.payment_entry}")
        if doc_data:
            if not row['party'] and doc_data.get('party'):
                row['party'] = doc_data.party
            if not row['clearance_date'] and doc_data.get('clearance_date'):
                row['clearance_date'] = doc_data.clearance_date
            if doc_data.get('custom_reconciliation_status') == 'Conciliado':
                is_mint_reconciled = True
        
        # Clasificación
        if bt.status == 'Reconciled' or row['clearance_date'] or is_mint_reconciled:
            row['status'] = 'Conciliado'
            row['classification'] = 'Conciliado'
    else:
        # Sin Payment Entry vinculado
        if bt.status == 'Reconciled':
            if bt.deposit > 0:
                row['classification'] = 'Abono no Registrado'
            elif bt.withdrawal > 0:
                row['classification'] = 'Cargo Bancario'
        elif bt.status == 'Unreconciled':
            if bt.deposit > 0:
                row['classification'] = 'Depósito en Tránsito'
            elif bt.withdrawal > 0:
                row['classification'] = 'Pago por Conciliar'
        else:
            row['classification'] = 'Pendiente de Clasificar'
    
    return row

def get_mint_bank_balance(accounts, to_date):
    """Obtiene el saldo ingresado en el estado de cuenta de Mint sumado de todas las cuentas"""
    total = 0
    for acc in accounts:
        query = """
            SELECT balance 
            FROM `tabMint Bank Statement Balance`
            WHERE bank_account = %(bank_account)s
              AND date <= %(to_date)s
            ORDER BY date DESC
            LIMIT 1
        """
        result = frappe.db.sql(query, {'bank_account': acc, 'to_date': to_date}, as_dict=True)
        if result:
            total += result[0].balance
    return total

def get_report_summary(data, filters):
    """Calcula el resumen ejecutivo para mostrar en la parte superior"""
    # Inicializar acumuladores
    deposits_in_transit = 0
    pagos_por_conciliar = 0
    abonos_no_registrados = 0
    cargos_bancarios = 0
    total_conciliado = 0
    
    for row in data:
        classification = row.get('classification', '')
        if classification == 'Depósito en Tránsito':
            deposits_in_transit += row.get('deposit', 0)
        elif classification == 'Pago por Conciliar':
            pagos_por_conciliar += row.get('withdrawal', 0)
        elif classification == 'Abono no Registrado':
            abonos_no_registrados += row.get('deposit', 0)
        elif classification == 'Cargo Bancario':
            cargos_bancarios += row.get('withdrawal', 0)
        elif classification == 'Conciliado':
            total_conciliado += row.get('deposit', 0) - row.get('withdrawal', 0)
    
    # Obtener saldo según el banco ingresado en Mint
    bank_balance = get_mint_bank_balance(filters.get('accounts'), filters.get('to_date'))
    
    # Obtener saldo contable de la cuenta
    account_balance = get_account_balance(filters)
    
    # Calcular saldos ajustados
    adjusted_bank_balance = bank_balance + deposits_in_transit - pagos_por_conciliar
    adjusted_books_balance = account_balance + abonos_no_registrados - cargos_bancarios
    difference = adjusted_bank_balance - adjusted_books_balance
    
    def _info(title, text):
        text = text.replace('"', '&quot;')
        return f' <i class="fa fa-info-circle text-muted mint-info-icon" data-title="{title}" data-text="{text}" style="cursor: help;"></i>'

    return [
        {
            'label': _('Saldo según Banco') + _info('Saldo según Banco', 'Saldo registrado en el extracto bancario ingresado en el sistema para la fecha seleccionada. Proviene de los saldos cargados en los estados de cuenta.'),
            'value': bank_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Depósitos en Tránsito') + _info('Depósitos en Tránsito', 'Cobros (ingresos) registrados en el sistema contable, pero que aún no se reflejan en el banco. Proviene de los recibos de pago (ingresos) o asientos contables aún no conciliados.'),
            'value': deposits_in_transit,
            'datatype': 'Currency'
        },
        {
            'label': _('Pagos por Conciliar') + _info('Pagos por Conciliar', 'Pagos (egresos) emitidos en el sistema, pero que el banco aún no ha descontado o procesado. Proviene de los recibos de pago (egresos) o asientos contables aún no conciliados.'),
            'value': pagos_por_conciliar,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo Banco Ajustado') + _info('Saldo Banco Ajustado', 'Saldo del banco + Depósitos en tránsito - Pagos por conciliar. Representa el saldo real proyectado del banco.'),
            'value': adjusted_bank_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo según Libros') + _info('Saldo según Libros', 'Saldo actual contable de la cuenta bancaria en el sistema. Proviene del balance del libro mayor (asientos contables) para esta cuenta.'),
            'value': account_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Abonos no Registrados') + _info('Abonos no Registrados', 'Ingresos que aparecen en el extracto bancario pero que aún no tienen un documento contable en el sistema. Proviene de las transacciones bancarias importadas que no han sido emparejadas.'),
            'value': abonos_no_registrados,
            'datatype': 'Currency'
        },
        {
            'label': _('Cargos Bancarios') + _info('Cargos Bancarios', 'Egresos (ej. comisiones) que aparecen en el extracto bancario pero que aún no han sido registrados en el sistema. Proviene de las transacciones bancarias importadas sin emparejar.'),
            'value': cargos_bancarios,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo Libros Ajustado') + _info('Saldo Libros Ajustado', 'Saldo según libros + Abonos no registrados - Cargos bancarios. Representa el saldo real proyectado en libros.'),
            'value': adjusted_books_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Diferencia') + _info('Diferencia', 'Diferencia entre el Saldo Banco Ajustado y el Saldo Libros Ajustado. Debe ser siempre cero (0).'),
            'value': difference,
            'datatype': 'Currency'
        },
        {
            'label': _('Flujo Conciliado') + _info('Flujo Conciliado', 'Suma neta (ingresos menos egresos) de todas las transacciones marcadas como conciliadas en este período. Proviene de los emparejamientos confirmados entre el extracto bancario y los recibos de pago o asientos.'),
            'value': total_conciliado,
            'datatype': 'Currency'
        },
        {
            'label': _('¿Cuadre?') + _info('¿Cuadre?', 'Indica si la conciliación está perfectamente balanceada.'),
            'value': '✅ CUADRA' if difference == 0 else '❌ REVISAR',
            'datatype': 'Data'
        }
    ]

def get_account_balance(filters):
    """Obtiene el saldo de las cuentas bancarias en el mayor contable"""
    accounts = filters.get('accounts')
    if not accounts:
        if filters.get('account'):
            accounts = (filters.get('account'),)
        else:
            return 0
            
    gl_accounts = frappe.db.sql("SELECT account FROM `tabBank Account` WHERE name IN %s", (accounts,), pluck='account')
    gl_accounts = [a for a in gl_accounts if a]
    
    if not gl_accounts:
        return 0
        
    filters_copy = filters.copy()
    filters_copy['gl_accounts'] = tuple(gl_accounts)
    
    query = """
        SELECT SUM(debit) - SUM(credit) as balance
        FROM `tabGL Entry`
        WHERE account IN %(gl_accounts)s
            AND company = %(company)s
            AND posting_date <= %(to_date)s
            AND is_cancelled = 0
    """
    result = frappe.db.sql(query, filters_copy, as_dict=True)
    return result[0].balance if result and result[0].balance else 0
