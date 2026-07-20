frappe.listview_settings['Bank Transaction'] = frappe.listview_settings['Bank Transaction'] || {};

let old_onload = frappe.listview_settings['Bank Transaction'].onload;

frappe.listview_settings['Bank Transaction'].onload = function(listview) {
    if (old_onload) {
        old_onload(listview);
    }
    
    listview.page.add_inner_button(__('Buscar y Eliminar Duplicados Exactos'), function() {
        try {
            let d = new frappe.ui.Dialog({
                title: __('Buscador de Duplicados'),
                size: 'large',
                fields: [
                    {
                        fieldtype: 'Button',
                        fieldname: 'btn_buscar',
                        label: __('Buscar Duplicados'),
                        click: function() {
                            d.fields_dict.table_html.$wrapper.html('<p class="text-muted">Buscando duplicados...</p>');
                            frappe.call({
                                method: 'mint.apis.reconciliation.get_duplicate_bank_transactions',
                                callback: function(r) {
                                    if (r.message) {
                                        d.duplicates = r.message;
                                        let html = `
                                            <table class="table table-bordered">
                                                <thead>
                                                    <tr>
                                                        <th>Fecha</th>
                                                        <th>Referencia</th>
                                                        <th>Monto</th>
                                                        <th>Original (a conservar)</th>
                                                        <th>Duplicado (a eliminar)</th>
                                                    </tr>
                                                </thead>
                                                <tbody>
                                        `;
                                        
                                        if (r.message.length === 0) {
                                            html += `<tr><td colspan="5" class="text-center text-muted">No se encontraron duplicados exactos</td></tr>`;
                                        } else {
                                            r.message.forEach(function(dup) {
                                                let orig_badge = dup.original_status === 'Reconciled' ? 'bg-success text-white' : 'bg-warning text-dark';
                                                html += `
                                                    <tr>
                                                        <td>${dup.date}</td>
                                                        <td>${dup.reference}</td>
                                                        <td>${format_currency(dup.amount)}</td>
                                                        <td><a href="/app/bank-transaction/${dup.original_name}" target="_blank">${dup.original_name}</a><br><span class="badge rounded-pill ${orig_badge}">${dup.original_status}</span></td>
                                                        <td><a href="/app/bank-transaction/${dup.duplicate_name}" target="_blank">${dup.duplicate_name}</a><br><span class="badge rounded-pill bg-danger text-white">${dup.duplicate_status}</span></td>
                                                    </tr>
                                                `;
                                            });
                                        }
                                        
                                        html += `</tbody></table>`;
                                        d.fields_dict.table_html.$wrapper.html(html);
                                    }
                                }
                            });
                        }
                    },
                    {
                        fieldtype: 'HTML',
                        fieldname: 'table_html'
                    }
                ],
                primary_action_label: __('Eliminar Duplicados'),
                primary_action: function(values) {
                    if (!d.duplicates || d.duplicates.length === 0) {
                        frappe.msgprint(__('No hay duplicados para eliminar.'));
                        d.hide();
                        return;
                    }
                    
                    frappe.confirm(__('¿Estás seguro que deseas eliminar los duplicados listados? Esta acción es irreversible.'), function() {
                        frappe.call({
                            method: 'mint.apis.reconciliation.remove_duplicate_bank_transactions',
                            args: { duplicates_json: JSON.stringify(d.duplicates) },
                            callback: function(r) {
                                if (!r.exc) {
                                    frappe.msgprint({
                                        title: __('Limpieza completada'),
                                        message: `Se procesaron ${r.message.processed} registros con ${r.message.errors.length} errores.`,
                                        indicator: 'green'
                                    });
                                    d.hide();
                                    listview.refresh();
                                }
                            }
                        });
                    });
                }
            });
            
            d.show();
            d.fields_dict.table_html.$wrapper.html('<p class="text-muted">Haga clic en "Buscar Duplicados" para listar los duplicados exactos.</p>');
        } catch (e) {
            frappe.msgprint("Error abriendo diálogo: " + e.message);
        }
    });
};
