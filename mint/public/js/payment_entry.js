console.log("Loading custom Payment Entry JS from mint");
frappe.ui.form.on("Payment Entry", {
	refresh: function (frm) {
			if (frm.doc.docstatus === 0 || frm.doc.docstatus === 1) {
				let label, color;
				
				if (frm.doc.docstatus === 0) {
					label = "Por conciliar";
					color = "orange";
				} else {
					let status = frm.doc.custom_reconciliation_status;
					label = status === "Conciliado" ? "Conciliado" : "Por conciliar";
					color = status === "Conciliado" ? "green" : "orange";
				}
				
				frm.page.set_indicator(__(label), color);
			}
		}, 100);
	}
});
