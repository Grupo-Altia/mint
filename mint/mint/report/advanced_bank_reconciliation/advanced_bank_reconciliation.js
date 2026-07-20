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
            "fieldname":"branch",
            "label": __("Sucursal"),
            "fieldtype": "Link",
            "options": "VE Branch",
            "reqd": 0,
            "get_query": function() {
                var company = frappe.query_report.get_filter_value('company');
                return {
                    "filters": {
                        "company": company
                    }
                };
            }
        },
        {
            "fieldname":"account",
            "label": __("Cuenta Bancaria"),
            "fieldtype": "Link",
            "options": "Bank Account",
            "reqd": 0,
            "get_query": function() {
                var company = frappe.query_report.get_filter_value('company');
                var branch = frappe.query_report.get_filter_value('branch');
                var filters = { 'company': company };
                if (branch) {
                    filters['branch_code'] = branch;
                }
                return { "filters": filters };
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
            "fieldname":"status",
            "label": __("Estado"),
            "fieldtype": "Select",
            "options": "Unreconciled\nReconciled\nAll",
            "default": "Unreconciled"
        }
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        // Resaltar clasificaciones específicas
        if (column.fieldname == "classification") {
            if (value === "Depósito en Tránsito" || value === "Pago por Conciliar") {
                value = `<span style="color: #ffa00a; font-weight: bold;">${value}</span>`;
            } else if (value === "Abono no Registrado" || value === "Cargo Bancario") {
                value = `<span style="color: #d63031; font-weight: bold;">${value}</span>`;
            } else if (value === "Conciliado") {
                value = `<span style="color: #00b894; font-weight: bold;">${value}</span>`;
            }
        }
        
        return value;
    },
    
    "onload": function(report) {
        report.page.set_title(__("Advanced Bank Reconciliation"));
        
        report.page.add_inner_button(__("Descargar PDF"), function() {
            let dialog = frappe.ui.get_print_settings(false, print_settings => {
                let custom_settings = Object.assign({}, print_settings);
                custom_settings.repeat_header_footer = 0;
                report.pdf_report(custom_settings);
            }, "");
            if (dialog) {
                dialog.set_df_property("print_format", "hidden", 1);
            }
        }, __("Exportar"));
        
        report.page.add_inner_button(__("Descargar Excel / CSV"), function() {
            report.export_report();
        }, __("Exportar"));
        
        // --- HoverCard estilo Mint Shadcn UI ---
        let hoverCard = null;

        $(document).on('mouseenter', '.mint-info-icon', function(e) {
            let $el = $(this);
            let title = $el.attr('data-title');
            let text = $el.attr('data-text');

            if ($('.mint-hover-card').length) {
                $('.mint-hover-card').remove();
            }

            hoverCard = $(`
                <div class="mint-hover-card" style="
                    position: absolute;
                    background: white;
                    border: 1px solid #e2e8f0;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
                    border-radius: 8px;
                    padding: 16px;
                    width: 380px;
                    z-index: 10000;
                    text-align: left;
                    color: #0f172a;
                    font-family: inherit;
                    opacity: 0;
                    transition: opacity 0.15s ease;
                ">
                    <h4 style="margin: 0 0 8px 0; font-size: 15px; font-weight: 600; color: #0f172a;">${title}</h4>
                    <p style="margin: 0; font-size: 13px; line-height: 1.5; color: #334155;">${text}</p>
                </div>
            `).appendTo('body');

            let offset = $el.offset();
            // Posicionar justo a la derecha del icono
            hoverCard.css({
                top: offset.top - 10,
                left: offset.left + 25
            });

            // Trigger reflow para iniciar transición
            hoverCard[0].offsetHeight;
            hoverCard.css('opacity', '1');
        });

        $(document).on('mouseleave', '.mint-info-icon', function(e) {
            if (hoverCard) {
                hoverCard.css('opacity', '0');
                setTimeout(() => {
                    $('.mint-hover-card').remove();
                }, 150);
            }
        });
    }
};
