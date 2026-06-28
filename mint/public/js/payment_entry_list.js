frappe.listview_settings['Payment Entry'] = {
	add_fields: ["clearance_date", "mode_of_payment"],
	get_indicator: function (doc) {
		if (doc.docstatus === 2) {
			return [__("Cancelado"), "red", "docstatus,=,2"];
		} else if (doc.docstatus === 0) {
			return [__("Por conciliar"), "orange", "docstatus,=,0"];
		} else if (doc.docstatus === 1) {
			if (doc.clearance_date) {
				return [__("Conciliado"), "green", "docstatus,=,1|clearance_date,!=,"];
			} else if (doc.mode_of_payment && doc.mode_of_payment.toLowerCase().includes("efectivo")) {
				return [__("Recibido"), "green", `docstatus,=,1|mode_of_payment,=,${doc.mode_of_payment}`];
			} else {
				return [__("Por conciliar"), "orange", "docstatus,=,1|clearance_date,=,"];
			}
		}
	}
};
