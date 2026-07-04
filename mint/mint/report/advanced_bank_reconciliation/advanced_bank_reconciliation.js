// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Advanced Bank Reconciliation"] = {
    "filters": [
        {
            "fieldname":"company",
            "label": __("Compañía"),
            "fieldtype": "Link",
            "options": "Company",
            "reqd": 1,
            "default": frappe.defaults.get_user_default("Company")
        },
        {
            "fieldname":"account",
            "label": __("Cuenta Bancaria"),
            "fieldtype": "Link",
            "options": "Bank Account",
            "reqd": 1,
            "get_query": function() {
                var company = frappe.query_report.get_filter_value('company');
                return {
                    "filters": [
                        ['Bank Account', 'company', '=', company]
                    ]
                };
            }
        },
        {
            "fieldname":"from_date",
            "label": __("Fecha Desde"),
            "fieldtype": "Date",
            "reqd": 1,
            "default": frappe.datetime.month_start()
        },
        {
            "fieldname":"to_date",
            "label": __("Fecha Hasta"),
            "fieldtype": "Date",
            "reqd": 1,
            "default": frappe.datetime.month_end()
        },
        {
            "fieldname":"include_reconciled",
            "label": __("Incluir Conciliados"),
            "fieldtype": "Check",
            "default": 0
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        // Resaltar clasificaciones específicas
        if (column.fieldname == "classification") {
            if (value === "Depósito en Tránsito" || value === "Cheque en Circulación") {
                value = `<span style="color: #ffa00a; font-weight: bold;">${value}</span>`;
            } else if (value === "Abono no Registrado" || value === "Cargo Bancario") {
                value = `<span style="color: #d63031; font-weight: bold;">${value}</span>`;
            } else if (value === "Conciliado") {
                value = `<span style="color: #00b894; font-weight: bold;">${value}</span>`;
            }
        }
        
        return value;
    }
};
