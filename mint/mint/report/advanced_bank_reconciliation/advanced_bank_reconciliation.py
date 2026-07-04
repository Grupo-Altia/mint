# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _

def execute(filters=None):
    columns, data = get_columns(), get_data(filters)
    
    # Resumen ejecutivo (retornar como chart o report_summary)
    report_summary = get_report_summary(data, filters)
    
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
            'fieldname': 'payment_document',
            'label': _('Documento Vinculado'),
            'fieldtype': 'Data',
            'width': 140
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

def get_data(filters):
    """Obtiene los datos del reporte"""
    # Validar filtros
    if not filters.get('company'):
        frappe.throw(_('La Compañía es obligatoria'))
    if not filters.get('account'):
        frappe.throw(_('La Cuenta Bancaria es obligatoria'))
    
    # 1. Obtener transacciones bancarias
    bank_transactions = get_bank_transactions(filters)
    
    # 2. Obtener vouchers con clearance_date (para cotejo)
    vouchers = get_vouchers_without_clearance(filters)
    
    # 3. Procesar y clasificar cada transacción
    data = []
    for bt in bank_transactions:
        row = process_bank_transaction(bt, filters)
        data.append(row)
    
    # 4. Agregar movimientos "solo en libros" al final
    for voucher in vouchers:
        row = {
            'date': voucher.get('posting_date'),
            'description': voucher.get('name'),
            'reference': '',
            'transaction_type': voucher.get('doctype'),
            'deposit': voucher.get('paid_amount') if voucher.get('payment_type') == 'Receive' else 0,
            'withdrawal': voucher.get('paid_amount') if voucher.get('payment_type') == 'Pay' else 0,
            'currency': voucher.get('currency'),
            'status': 'Solo en Libros',
            'classification': 'Pendiente en Libros',
            'payment_document': voucher.get('doctype'),
            'payment_entry': voucher.get('name'),
            'party': voucher.get('party'),
            'clearance_date': ''
        }
        data.append(row)
    
    return data

def get_bank_transactions(filters):
    """Obtiene Bank Transactions del período filtrado"""
    conditions = [
        "bt.bank_account = %(account)s",
        "bt.date BETWEEN %(from_date)s AND %(to_date)s"
    ]
    
    if not filters.get('include_reconciled'):
        conditions.append("bt.status != 'Reconciled'")
    
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
    # Payment Entries sin clearance_date
    pe_query = """
        SELECT 
            pe.name,
            pe.posting_date,
            pe.payment_type,
            pe.paid_amount,
            IF(pe.payment_type='Receive', pe.paid_to_account_currency, pe.paid_from_account_currency) as currency,
            pe.party,
            pe.party_type,
            'Payment Entry' as doctype
        FROM `tabPayment Entry` pe
        WHERE pe.bank_account = %(account)s
            AND pe.docstatus = 1
            AND (pe.clearance_date IS NULL OR pe.clearance_date = '')
            AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
            AND pe.paid_amount > 0
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
            'Journal Entry' as doctype
        FROM `tabJournal Entry` je
        INNER JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.docstatus = 1
            AND jea.bank_account = %(account)s
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

def process_bank_transaction(bt, filters):
    """Procesa una Bank Transaction y determina su clasificación"""
    row = {
        'date': bt.date,
        'description': bt.description,
        'reference': bt.reference,
        'transaction_type': bt.transaction_type,
        'deposit': bt.deposit,
        'withdrawal': bt.withdrawal,
        'currency': bt.currency,
        'status': bt.status,
        'party': bt.party,
        'payment_document': '',
        'payment_entry': '',
        'clearance_date': '',
        'classification': ''
    }
    
    # Obtener Payment Entries vinculados
    linked_payments = frappe.db.sql("""
        SELECT 
            pe.payment_document,
            pe.payment_entry,
            pe.allocated_amount,
            pe.clearance_date
        FROM `tabBank Transaction Payments` pe
        WHERE pe.parent = %s
    """, bt.name, as_dict=True)
    
    if linked_payments:
        # Tomar el primer pago vinculado (o sumar todos según necesidad)
        first = linked_payments[0]
        row['payment_document'] = first.payment_document
        row['payment_entry'] = first.payment_entry
        row['clearance_date'] = first.clearance_date
        
        # Clasificación
        if bt.status == 'Reconciled':
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
                row['classification'] = 'Cheque en Circulación'
        else:
            row['classification'] = 'Pendiente de Clasificar'
    
    return row

def get_report_summary(data, filters):
    """Calcula el resumen ejecutivo para mostrar en la parte superior"""
    # Inicializar acumuladores
    total_deposits = 0
    total_withdrawals = 0
    deposits_in_transit = 0
    cheques_in_circulation = 0
    abonos_no_registrados = 0
    cargos_bancarios = 0
    
    for row in data:
        if row.get('deposit'):
            total_deposits += row['deposit']
        if row.get('withdrawal'):
            total_withdrawals += row['withdrawal']
        
        classification = row.get('classification', '')
        if classification == 'Depósito en Tránsito':
            deposits_in_transit += row.get('deposit', 0)
        elif classification == 'Cheque en Circulación':
            cheques_in_circulation += row.get('withdrawal', 0)
        elif classification == 'Abono no Registrado':
            abonos_no_registrados += row.get('deposit', 0)
        elif classification == 'Cargo Bancario':
            cargos_bancarios += row.get('withdrawal', 0)
    
    # Obtener saldo contable de la cuenta
    account_balance = get_account_balance(filters)
    
    # Calcular saldos ajustados
    bank_balance = total_deposits - total_withdrawals
    adjusted_bank_balance = bank_balance + deposits_in_transit - cheques_in_circulation
    adjusted_books_balance = account_balance + abonos_no_registrados - cargos_bancarios
    difference = adjusted_bank_balance - adjusted_books_balance
    
    return [
        {
            'label': _('Saldo según Banco'),
            'value': bank_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Depósitos en Tránsito'),
            'value': deposits_in_transit,
            'datatype': 'Currency'
        },
        {
            'label': _('Cheques en Circulación'),
            'value': cheques_in_circulation,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo Banco Ajustado'),
            'value': adjusted_bank_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo según Libros'),
            'value': account_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Abonos no Registrados'),
            'value': abonos_no_registrados,
            'datatype': 'Currency'
        },
        {
            'label': _('Cargos Bancarios'),
            'value': cargos_bancarios,
            'datatype': 'Currency'
        },
        {
            'label': _('Saldo Libros Ajustado'),
            'value': adjusted_books_balance,
            'datatype': 'Currency'
        },
        {
            'label': _('Diferencia'),
            'value': difference,
            'datatype': 'Currency'
        },
        {
            'label': _('¿Cuadra?'),
            'value': '✅ SÍ' if difference == 0 else '❌ REVISAR',
            'datatype': 'Data'
        }
    ]

def get_account_balance(filters):
    """Obtiene el saldo de la cuenta bancaria en el mayor contable"""
    # Obtain GL Account linked to Bank Account
    gl_account = frappe.db.get_value('Bank Account', filters.get('account'), 'account')
    
    if not gl_account:
        return 0
        
    filters_copy = filters.copy()
    filters_copy['gl_account'] = gl_account
    
    query = """
        SELECT SUM(debit) - SUM(credit) as balance
        FROM `tabGL Entry`
        WHERE account = %(gl_account)s
            AND company = %(company)s
            AND posting_date <= %(to_date)s
            AND is_cancelled = 0
    """
    result = frappe.db.sql(query, filters_copy, as_dict=True)
    return result[0].balance if result and result[0].balance else 0
