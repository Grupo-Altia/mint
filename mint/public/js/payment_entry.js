frappe.ui.form.on("Payment Entry", {
	onload: function(frm) {
		const original_set_current_exchange_rate = frm.events.set_current_exchange_rate;
		frm.events.set_current_exchange_rate = function(frm, exchange_rate_field, from_currency, to_currency) {
			if (!from_currency || !to_currency) return;
			if (original_set_current_exchange_rate) {
				return original_set_current_exchange_rate(frm, exchange_rate_field, from_currency, to_currency);
			}
		}
	},
	setup: function (frm) {
		frm.set_query("paid_to", function () {
			frm.events.validate_company(frm);
			return {
				filters: {
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});
	},
	refresh: function (frm) {
		// Custom logic for Payment Entry
	}
});
