frappe.ui.form.on('Bank Transaction', {
	setup: function(frm) {
		frm.events.get_payment_doctypes = function(frm) {
			return ["Payment Entry", "Journal Entry", "Sales Invoice", "Purchase Invoice", "Bank Transaction", "Mint Bank Transfer"];
		};
	},
	refresh: function(frm) {
		if (frm.doc.status === 'Reconciled') {
			frm.set_df_property('payment_entries', 'read_only', 1);
		}
	}
});
