frappe.ui.form.on('Bank Transaction', {
	refresh: function(frm) {
		if (frm.doc.status === 'Reconciled') {
			frm.set_df_property('payment_entries', 'read_only', 1);
		}
	}
});
