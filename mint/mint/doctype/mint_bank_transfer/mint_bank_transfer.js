// Copyright (c) 2026, The Commit Company (Algocode Technologies Pvt. Ltd.) and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mint Bank Transfer", {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Accounting Ledger'), function() {
				frappe.route_options = {
					voucher_no: frm.doc.name,
					from_date: frm.doc.date,
					to_date: frm.doc.date,
					company: frm.doc.company,
					show_cancelled_entries: frm.doc.docstatus === 2
				};
				frappe.set_route("query-report", "General Ledger");
			}, __('View'));

			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "GL Entry",
					filters: {
						voucher_type: frm.doc.doctype,
						voucher_no: frm.doc.name,
						is_cancelled: 0
					},
					fields: ["account", "debit", "credit"]
				},
				callback: function(r) {
					if (r.message && r.message.length > 0) {
						let html = `
							<table class="table table-bordered table-condensed" style="margin-top: 15px;">
								<thead>
									<tr>
										<th>${__('Account')}</th>
										<th class="text-right">${__('Debit')}</th>
										<th class="text-right">${__('Credit')}</th>
									</tr>
								</thead>
								<tbody>
						`;
						let total_debit = 0;
						let total_credit = 0;
						r.message.forEach(row => {
							html += `
								<tr>
									<td>${row.account}</td>
									<td class="text-right">${frappe.format(row.debit, {fieldtype: 'Currency'})}</td>
									<td class="text-right">${frappe.format(row.credit, {fieldtype: 'Currency'})}</td>
								</tr>
							`;
							total_debit += row.debit;
							total_credit += row.credit;
						});
						html += `
								</tbody>
								<tfoot>
									<tr>
										<th class="text-right">${__('Total')}</th>
										<th class="text-right">${frappe.format(total_debit, {fieldtype: 'Currency'})}</th>
										<th class="text-right">${frappe.format(total_credit, {fieldtype: 'Currency'})}</th>
									</tr>
								</tfoot>
							</table>
						`;
						frm.get_field("gl_entries_html").$wrapper.html(html);
					} else {
						frm.get_field("gl_entries_html").$wrapper.html("");
					}
				}
			});
		} else {
			frm.get_field("gl_entries_html").$wrapper.html("");
		}
	},
});
