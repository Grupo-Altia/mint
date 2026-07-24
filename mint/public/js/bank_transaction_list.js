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
                size: 'extra-large',
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
                                        // Todos seleccionados por defecto
                                        d.duplicates.forEach(dup => dup.selected = true);
                                        d.render_table();
                                    }
                                }
                            });
                        }
                    },
                    {
                        fieldtype: 'Data',
                        fieldname: 'search_term',
                        label: __('Buscar por referencia, monto o descripción...'),
                    },
                    {
                        fieldtype: 'HTML',
                        fieldname: 'table_html'
                    }
                ],
                primary_action_label: __('Eliminar Duplicados Seleccionados'),
                primary_action: function(values) {
                    if (!d.duplicates || d.duplicates.length === 0) {
                        frappe.msgprint(__('No hay duplicados encontrados.'));
                        d.hide();
                        return;
                    }
                    
                    let to_delete = d.duplicates.filter(dup => dup.selected);
                    if (to_delete.length === 0) {
                        frappe.msgprint(__('No ha seleccionado ningún duplicado para eliminar.'));
                        return;
                    }
                    
                    frappe.confirm(__('¿Estás seguro que deseas eliminar los ' + to_delete.length + ' duplicados seleccionados? Esta acción es irreversible.'), function() {
                        frappe.call({
                            method: 'mint.apis.reconciliation.remove_duplicate_bank_transactions',
                            args: { duplicates_json: JSON.stringify(to_delete) },
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
            
            d.render_table = function() {
                if (!d.duplicates) return;
                let search_term = (d.get_value('search_term') || '').toLowerCase();
                
                let filtered = d.duplicates.filter(dup => {
                    if (!search_term) return true;
                    let desc = (dup.description || '').toLowerCase();
                    let ref = (dup.reference || '').toLowerCase();
                    let amount = (dup.amount || '').toString();
                    return desc.includes(search_term) || ref.includes(search_term) || amount.includes(search_term);
                });
                
                let html = `
                    <style>
                        .duplicate-table-wrapper { max-height: 400px; overflow-y: auto; }
                        .duplicate-table th { position: sticky; top: 0; background: white; z-index: 10; }
                    </style>
                    <div class="duplicate-table-wrapper">
                    <table class="table table-bordered duplicate-table text-sm">
                        <thead>
                            <tr>
                                <th><input type="checkbox" id="check_all_dups" checked></th>
                                <th>Fecha</th>
                                <th>Descripción</th>
                                <th>Referencia</th>
                                <th>Monto</th>
                                <th>Original (a conservar)</th>
                                <th>Duplicado (a eliminar)</th>
                            </tr>
                        </thead>
                        <tbody>
                `;
                
                if (filtered.length === 0) {
                    html += `<tr><td colspan="7" class="text-center text-muted">No se encontraron duplicados</td></tr>`;
                } else {
                    filtered.forEach(function(dup) {
                        let orig_badge = dup.original_status === 'Reconciled' ? 'bg-success text-white' : 'bg-warning text-dark';
                        let checked = dup.selected ? 'checked' : '';
                        // get index in original array
                        let idx = d.duplicates.indexOf(dup);
                        
                        html += `
                            <tr>
                                <td><input type="checkbox" class="dup-checkbox" data-dup-index="${idx}" ${checked}></td>
                                <td>${dup.date}</td>
                                <td>${dup.description || ''}</td>
                                <td>${dup.reference}</td>
                                <td>${format_currency(dup.amount)}</td>
                                <td><a href="/app/bank-transaction/${dup.original_name}" target="_blank">${dup.original_name}</a><br><span class="badge rounded-pill ${orig_badge}">${dup.original_status}</span></td>
                                <td><a href="/app/bank-transaction/${dup.duplicate_name}" target="_blank">${dup.duplicate_name}</a><br><span class="badge rounded-pill bg-danger text-white">${dup.duplicate_status}</span></td>
                            </tr>
                        `;
                    });
                }
                
                html += `</tbody></table></div>`;
                d.fields_dict.table_html.$wrapper.html(html);
                
                // Eventos
                d.fields_dict.table_html.$wrapper.find('.dup-checkbox').on('change', function() {
                    let idx = $(this).data('dup-index');
                    d.duplicates[idx].selected = $(this).prop('checked');
                    update_check_all_state();
                });
                
                d.fields_dict.table_html.$wrapper.find('#check_all_dups').on('change', function() {
                    let is_checked = $(this).prop('checked');
                    d.fields_dict.table_html.$wrapper.find('.dup-checkbox').prop('checked', is_checked);
                    filtered.forEach(dup => {
                        dup.selected = is_checked;
                    });
                });
                
                function update_check_all_state() {
                    let all_checked = filtered.length > 0 && filtered.every(dup => dup.selected);
                    let some_checked = filtered.some(dup => dup.selected);
                    let check_all = d.fields_dict.table_html.$wrapper.find('#check_all_dups');
                    check_all.prop('checked', all_checked);
                    check_all.prop('indeterminate', some_checked && !all_checked);
                }
                
                update_check_all_state();
            };
            
            d.show();
            d.fields_dict.table_html.$wrapper.html('<p class="text-muted">Haga clic en "Buscar Duplicados" para listar los duplicados exactos.</p>');
            
            // Vincular el campo de búsqueda para que filtre al escribir
            setTimeout(() => {
                if (d.get_field('search_term') && d.get_field('search_term').$input) {
                    d.get_field('search_term').$input.on('input', function() {
                        d.render_table();
                    });
                }
            }, 500);
            
        } catch (e) {
            frappe.msgprint("Error abriendo diálogo: " + e.message);
        }
    });
};
